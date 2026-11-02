"""Runs each incident's graph thread in the background and keeps the incident's status in step.

One thread per incident (`thread_id` = incident id), checkpointed after every node, so a run can
wait days for a human and outlive a restart of Bleep. See docs/design/graph.md.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo
from bleep.schemas.graph import Decision
from bleep.schemas.incidents import Incident, Signal

log = logging.getLogger(__name__)


class IncidentRunner:
    def __init__(self, graph: CompiledStateGraph, repo: IncidentRepo, bus: EventBus) -> None:
        self.graph = graph
        self.repo = repo
        self.bus = bus
        self._runs: dict[str, asyncio.Task[None]] = {}

    @staticmethod
    def config(incident_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": incident_id}}

    async def start(self, incident: Incident) -> asyncio.Task[None]:
        """Run the incident's graph in the background, from where its thread stands.

        A new thread starts at Gather; one with a checkpoint goes on from it, and one already
        waiting for approval or finished is left as it is. Calling it while a run is in flight
        returns that run, so the graph never runs twice at once for one incident.
        """
        running = self._runs.get(incident.id)
        if running is not None and not running.done():
            return running
        return self._schedule(incident.id, lambda: self._advance(incident))

    async def resume(self, incident_id: str, decision: Decision) -> asyncio.Task[None]:
        """Continue a run waiting at Approval with the human's decision, in the background.

        A run that is not waiting (finished, or never started) is left alone.
        """
        return self._schedule(incident_id, lambda: self._resume(incident_id, decision))

    async def recover_on_boot(self) -> list[str]:
        """Restart every unfinished run except those waiting for a human; returns their ids.

        A run waiting for approval stays waiting: its checkpoint holds it until a decision
        arrives through `resume`.
        """
        started = []
        for incident in await self.repo.unfinished():
            if incident.status == "awaiting_approval":
                continue
            await self.start(incident)
            started.append(incident.id)
        return started

    async def join(self) -> None:
        """Wait until no run is in flight."""
        while self._runs:
            await asyncio.gather(*list(self._runs.values()), return_exceptions=True)
            await asyncio.sleep(0)  # let finished runs remove themselves

    async def close(self) -> None:
        """Cancel every run in flight; each goes on from its last checkpoint after a restart."""
        runs = list(self._runs.values())
        for task in runs:
            task.cancel()
        await asyncio.gather(*runs, return_exceptions=True)

    def _schedule(self, incident_id: str, run: Callable[[], Awaitable[None]]) -> asyncio.Task[None]:
        before = self._runs.get(incident_id)

        async def after_previous() -> None:
            if before is not None and not before.done():
                await asyncio.gather(before, return_exceptions=True)
            await run()

        task = asyncio.create_task(after_previous(), name=f"incident {incident_id}")
        self._runs[incident_id] = task

        def forget(done: asyncio.Task[None]) -> None:
            if self._runs.get(incident_id) is done:
                del self._runs[incident_id]

        task.add_done_callback(forget)
        return task

    async def _advance(self, incident: Incident) -> None:
        snapshot = await self.graph.aget_state(self.config(incident.id))
        if not snapshot.values:  # no checkpoint yet: a new run
            signals = await self.repo.signals(incident.id)
            await self._drive(incident.id, _initial(incident, signals))
        elif snapshot.interrupts:
            await self.repo.set_status(incident.id, "awaiting_approval")
        elif snapshot.next:
            await self._drive(incident.id, None)  # None: go on from the checkpoint

    async def _resume(self, incident_id: str, decision: Decision) -> None:
        if not (await self.graph.aget_state(self.config(incident_id))).interrupts:
            log.warning("incident %s: a decision arrived but the run is not waiting", incident_id)
            return
        await self._drive(incident_id, Command(resume=decision))

    async def _drive(self, incident_id: str, payload: Any) -> None:
        config = self.config(incident_id)
        await self.repo.set_status(incident_id, "investigating")
        try:
            await self.graph.ainvoke(payload, config)
        except Exception as error:  # a broken run must not take the runner down
            log.exception("incident %s: graph run failed", incident_id)
            await self.repo.set_status(incident_id, "failed")
            text = f"The run stopped on an error ({type(error).__name__}); see the backend log."
            await self.bus.publish(incident_id, "notice", {"text": text})
            return
        snapshot = await self.graph.aget_state(config)
        if snapshot.interrupts:
            await self.repo.set_status(incident_id, "awaiting_approval")
        elif not snapshot.next:
            outcome = snapshot.values.get("outcome")
            await self.repo.set_status(
                incident_id, "resolved" if outcome == "resolved" else "closed"
            )


def _initial(incident: Incident, signals: list[Signal]) -> dict[str, Any]:
    return {
        "incident_id": incident.id,
        "subject": incident.subject,
        "signals": signals,
        "investigate_rounds": 0,
        "tool_calls_used": 0,
        "instructions": [],
        "verify_loops": 0,
    }
