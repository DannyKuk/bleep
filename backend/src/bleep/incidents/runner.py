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

from bleep.graph.deps import Approvals, Notifier
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo
from bleep.schemas.graph import Decision
from bleep.schemas.incidents import Incident, Signal

log = logging.getLogger(__name__)


class IncidentRunner:
    """`approvals` is the store the graph's Approval node uses; with it, a decision recorded
    while the run was not waiting (Bleep died between the decision and the resume) is delivered
    when the run is started again. `notifier` hears `incident_opened` once, when a new
    incident's run starts, whatever the run ends with."""

    def __init__(
        self,
        graph: CompiledStateGraph,
        repo: IncidentRepo,
        bus: EventBus,
        approvals: Approvals | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.graph = graph
        self.repo = repo
        self.bus = bus
        self.approvals = approvals
        self.notifier = notifier
        self._runs: dict[str, asyncio.Task[None]] = {}

    @staticmethod
    def config(incident_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": incident_id}}

    async def start(self, incident: Incident) -> asyncio.Task[None]:
        """Run the incident's graph in the background, from where its thread stands.

        A new thread tells the notifier the incident opened, then starts at Gather. One with a
        checkpoint mid-graph goes on from it. One waiting for approval stays waiting, unless its
        approval was decided before a restart: then it goes on with the stored decision. A
        finished one is not run again; only its terminal status is settled if a crash left it
        unset. Calling it while a run is in flight returns that run, so the graph never runs
        twice at once for one incident.
        """
        running = self._runs.get(incident.id)
        if running is not None and not running.done():
            return running
        return self._schedule(incident.id, lambda: self._advance(incident))

    async def resume(
        self, incident_id: str, decision: Decision, approval_id: str
    ) -> asyncio.Task[None]:
        """Continue a run waiting at Approval with the human's decision, in the background.

        `approval_id` is the approval the decision was made for. The run goes on only if it is
        waiting on that approval: a resume queued behind a run that already consumed the
        decision must not answer the next plan, which nobody has seen. A run that is not
        waiting (finished, never started, or waiting on another approval) is left alone.
        """
        return self._schedule(incident_id, lambda: self._resume(incident_id, decision, approval_id))

    async def waiting_for(self, incident_id: str) -> str | None:
        """The id of the approval the run is waiting on at its interrupt; None if not waiting."""
        snapshot = await self.graph.aget_state(self.config(incident_id))
        if not snapshot.interrupts:
            return None
        return snapshot.interrupts[0].value.get("approval_id")

    async def state(self, incident_id: str) -> dict[str, Any]:
        """The incident's graph state as last checkpointed; empty before its first run."""
        return dict((await self.graph.aget_state(self.config(incident_id))).values)

    async def recover_on_boot(self) -> list[str]:
        """Restart every unfinished run except those waiting for a human; returns their ids.

        A run waiting for approval stays waiting: its checkpoint holds it until a decision
        arrives through `resume`. One whose approval was decided before Bleep stopped is not
        waiting any more, and goes on with that decision.
        """
        started = []
        for incident in await self.repo.unfinished():
            if incident.status == "awaiting_approval" and await self._decided(incident.id) is None:
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
            try:
                await run()
            except Exception:  # logged now, not found in the task at shutdown
                log.exception("incident %s: the runner failed", incident_id)

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
            await self._announce(incident)
            signals = await self.repo.signals(incident.id)
            await self._drive(incident.id, _initial(incident, signals))
        elif snapshot.interrupts:
            decision = await self._decided(incident.id)
            if decision is not None:  # decided, but the resume never reached a checkpoint
                await self._drive(incident.id, Command(resume=decision))
            else:
                await self.repo.set_status(incident.id, "awaiting_approval")
        elif snapshot.next:
            await self._drive(incident.id, None)  # None: go on from the checkpoint
        else:  # finished, but the status never settled (cancelled or crashed just after the end)
            await self._settle(incident.id, snapshot)

    async def _announce(self, incident: Incident) -> None:
        """Tell the channels a new incident opened, before its run starts; a channel outage is
        logged and never stops the run."""
        if self.notifier is None:
            return
        try:
            await self.notifier.incident_opened(incident)
        except Exception as error:
            log.warning("incident %s: announcing it failed: %s", incident.id, type(error).__name__)

    async def _decided(self, incident_id: str) -> Decision | None:
        """The decision on the incident's latest approval, if it has been decided."""
        if self.approvals is None:
            return None
        record = await self.approvals.current(incident_id)
        return record.decision if record is not None else None

    async def _resume(self, incident_id: str, decision: Decision, approval_id: str) -> None:
        waiting = await self.waiting_for(incident_id)
        if waiting is None:
            log.warning("incident %s: a decision arrived but the run is not waiting", incident_id)
            return
        if waiting != approval_id:
            log.warning(
                "incident %s: a decision for approval %s arrived, but the run waits on %s",
                incident_id,
                approval_id,
                waiting,
            )
            return
        await self._drive(incident_id, Command(resume=decision))

    async def _drive(self, incident_id: str, payload: Any) -> None:
        config = self.config(incident_id)
        try:
            await self.repo.set_status(incident_id, "investigating")
            await self.graph.ainvoke(payload, config)
        except Exception as error:  # a broken run must not take the runner down
            log.exception("incident %s: graph run failed", incident_id)
            try:
                await self.repo.set_status(incident_id, "failed")
                text = f"The run stopped on an error ({type(error).__name__}); see the backend log."
                await self.bus.publish(incident_id, "notice", {"text": text})
            except Exception:
                log.exception("incident %s: recording the failure failed", incident_id)
            return
        snapshot = await self.graph.aget_state(config)
        if snapshot.interrupts:
            await self.repo.set_status(incident_id, "awaiting_approval")
        elif not snapshot.next:
            await self._settle(incident_id, snapshot)

    async def _settle(self, incident_id: str, snapshot: Any) -> None:
        """Set the terminal status of a finished thread from its outcome."""
        outcome = snapshot.values.get("outcome")
        await self.repo.set_status(incident_id, "resolved" if outcome == "resolved" else "closed")


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
