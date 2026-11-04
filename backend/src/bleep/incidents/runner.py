"""Runs each incident's graph thread in the background and keeps the incident's status in step.

One thread per incident (`thread_id` = incident id), checkpointed after every node, so a run can
wait days for a human and outlive a restart of Bleep. See docs/design/graph.md.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from bleep import prompts
from bleep.graph.build import NODE_STATUS
from bleep.graph.deps import (
    Approvals,
    IncidentFinished,
    Notifier,
    SettingsSource,
    StaticSettings,
    Tracer,
)
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo, utcnow
from bleep.llm.guard import ModelUnavailable
from bleep.schemas.graph import Decision
from bleep.schemas.incidents import TERMINAL_STATUSES, Incident, IncidentStatus, Signal

log = logging.getLogger(__name__)

AFTER_PAUSE = "after_pause"  # config flag: this run goes on from a pause
MAX_PAUSE_MIN = 60  # the longest wait between two retries of a paused run
LONG_PAUSE_S = 3600  # a run paused this long in all gets one more notice


class IncidentRunner:
    """`approvals` is the store the graph's Approval node uses; with it, a decision recorded
    while the run was not waiting (Bleep died between the decision and the resume) is delivered
    when the run is started again. `notifier` hears `incident_opened` once, when a new
    incident's run starts, whatever the run ends with, and a `notice` when a run fails. `tracer`
    adds its callbacks to every graph invocation of an incident; without one, none are added.
    A run whose model stays unavailable after the retries is paused, not failed, and tried again
    `Budgets.paused_retry_min` later (read from `settings`; `sleep` waits, and tests pass one that
    returns at once)."""

    def __init__(
        self,
        graph: CompiledStateGraph,
        repo: IncidentRepo,
        bus: EventBus,
        approvals: Approvals | None = None,
        notifier: Notifier | None = None,
        tracer: Tracer | None = None,
        settings: SettingsSource | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.graph = graph
        self.repo = repo
        self.bus = bus
        self.approvals = approvals
        self.notifier = notifier
        self.tracer = tracer
        self.settings = settings if settings is not None else StaticSettings()
        self.sleep = sleep
        self.clock = clock
        self._runs: dict[str, asyncio.Task[None]] = {}
        self._pauses: dict[str, int] = {}  # consecutive pauses of a run, for the backoff
        self._since: dict[str, datetime] = {}  # when a run first paused
        self._warned_long: set[str] = set()
        self._ended: set[str] = set()  # closed from outside their run (`end`)

    @staticmethod
    def config(incident_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": incident_id}}

    def run_config(self, incident: Incident) -> RunnableConfig:
        """The config of one graph invocation: the thread, plus the tracer's part."""
        traced = self.tracer.run_config(incident) if self.tracer is not None else {}
        return {**traced, **self.config(incident.id)}

    async def start(self, incident: Incident) -> asyncio.Task[None]:
        """Run the incident's graph in the background, from where its thread stands.

        A new thread records the prompt overrides in force on the incident, tells the notifier
        the incident opened, then starts at Gather. One with a
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
        waiting any more, and goes on with that decision. A paused run goes on at once.
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

    async def end(
        self, incident_id: str, text: str, *, spare: tuple[IncidentStatus, ...] = ()
    ) -> None:
        """Close an incident from outside its run (its host was removed) and say why, on its
        timeline and to the channels; one that is finished already is left as it is.

        It is marked ended before the first await, so no run of it is driven from here on; then
        it is closed, which shuts `ApprovalStore.resolve` for it on every channel; then whatever
        run is in flight is cancelled. A decision that won just before is resumed into nothing:
        `_resume` and `_drive` refuse an ended or finished incident, and so does Execute.

        With `spare`, an incident in one of those statuses is left entirely alone (its run
        goes on): it is closed first, and only a close that happened ends the run."""
        if spare:
            before = await self.repo.close_unfinished(incident_id, spare)
            if before is None:
                return
            self._ended.add(incident_id)
        else:
            self._ended.add(incident_id)
            before = await self.repo.close_unfinished(incident_id)
        run = self._runs.get(incident_id)
        if run is not None:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
        self._forget_pauses(incident_id)
        if before is None:
            return
        if before in ("executing", "verifying"):
            text += " A step of the approved plan may already have run; see the actions above."
        await self.bus.publish(incident_id, "notice", {"text": text})
        await self._tell(incident_id, text)

    def _finished(self, incident_id: str, status: IncidentStatus | None) -> bool:
        if incident_id in self._ended or status is None or status in TERMINAL_STATUSES:
            log.info("incident %s: finished, so its run is not driven", incident_id)
            return True
        return False

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
            await self.repo.record_prompt_overrides(incident.id, prompts.override_versions())
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

    async def _tell(self, incident_id: str, text: str) -> None:
        """Send a notice to the channels: a human who approved on Telegram hears of a run that
        died there too. A channel outage is logged and changes nothing."""
        if self.notifier is None:
            return
        try:
            await self.notifier.notice(incident_id, text)
        except Exception as error:
            log.warning(
                "incident %s: telling the channels failed: %s", incident_id, type(error).__name__
            )

    async def _decided(self, incident_id: str) -> Decision | None:
        """The decision on the incident's latest approval, if it has been decided."""
        if self.approvals is None:
            return None
        record = await self.approvals.current(incident_id)
        return record.decision if record is not None else None

    async def _resume(self, incident_id: str, decision: Decision, approval_id: str) -> None:
        if incident_id in self._ended:
            return
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
        was_paused = False
        try:
            current = await self.repo.get(incident_id)
            if self._finished(incident_id, current.status if current is not None else None):
                return
            was_paused = current is not None and current.status == "paused"
            await self.repo.set_status(incident_id, await self._status_on(incident_id, payload))
            config = await self._traced(incident_id)
            if was_paused:  # the graph keeps a retried node off the timeline
                config = {**config, "configurable": {**config["configurable"], AFTER_PAUSE: True}}
            await self.graph.ainvoke(payload, config)
        except ModelUnavailable as unavailable:
            await self._pause(incident_id, unavailable, announce=not was_paused)
            return
        except IncidentFinished:
            log.info("incident %s: closed while its run went on; stopped", incident_id)
            return
        except Exception as error:  # a broken run must not take the runner down
            log.exception("incident %s: graph run failed", incident_id)
            self._forget_pauses(incident_id)
            text = f"The run stopped on an error ({type(error).__name__}); see the backend log."
            try:
                await self.repo.set_status(incident_id, "failed")
                await self.bus.publish(incident_id, "notice", {"text": text})
            except Exception:
                log.exception("incident %s: recording the failure failed", incident_id)
            await self._tell(incident_id, text)
            return
        self._forget_pauses(incident_id)
        snapshot = await self.graph.aget_state(config)
        if snapshot.interrupts:
            await self.repo.set_status(incident_id, "awaiting_approval")
        elif not snapshot.next:
            await self._settle(incident_id, snapshot)

    async def _pause(
        self, incident_id: str, unavailable: ModelUnavailable, *, announce: bool
    ) -> None:
        """Park a run whose model is unavailable and try it again later, from its checkpoint.

        The first retry comes `paused_retry_min` minutes later, then twice as late each time up
        to an hour. The channels hear of it once when the run pauses and once more when it has
        been paused for an hour in all."""
        log.warning("incident %s: paused, %s", incident_id, unavailable)
        base = self.settings.budgets().paused_retry_min
        ceiling = max(base, MAX_PAUSE_MIN)
        seconds = min(base * 2 ** self._pauses.get(incident_id, 0), ceiling) * 60
        self._pauses[incident_id] = self._pauses.get(incident_id, 0) + 1
        now = self.clock()
        paused_for = (now - self._since.setdefault(incident_id, now)).total_seconds()
        text = None
        if announce:
            text = (
                f"The model is not answering ({unavailable.reason}). The run is paused and tries "
                f"again from where it stopped, first in {base} minutes, then less often."
            )
        elif paused_for >= LONG_PAUSE_S and incident_id not in self._warned_long:
            self._warned_long.add(incident_id)
            text = "The run has been paused for an hour: the model is still not answering."
        try:
            await self.repo.set_status(incident_id, "paused")
            if text is not None:
                await self.bus.publish(incident_id, "notice", {"text": text})
        except Exception:
            log.exception("incident %s: recording the pause failed", incident_id)
        if text is not None:
            await self._tell(incident_id, text)
        self._schedule(incident_id, lambda: self._retry_after(incident_id, seconds))

    def _forget_pauses(self, incident_id: str) -> None:
        self._pauses.pop(incident_id, None)
        self._since.pop(incident_id, None)
        self._warned_long.discard(incident_id)

    async def _retry_after(self, incident_id: str, seconds: float) -> None:
        await self.sleep(seconds)
        incident = await self.repo.get(incident_id)
        if incident is not None and incident.status == "paused":  # not moved on by someone else
            await self._advance(incident)
        else:
            self._forget_pauses(incident_id)

    def retrying(self, incident_id: str) -> bool:
        """Whether the run has paused on an unavailable model and is waiting for, or making, a
        retry. The watchdog leaves such an incident alone: the runner already reports on it."""
        return incident_id in self._pauses

    async def _status_on(self, incident_id: str, payload: Any) -> IncidentStatus:
        """The status as a run is driven. A yes goes straight to Execute; a restart or a retry
        takes the status of the node it goes on at, so a verified fix never shows
        `investigating` again while Report runs; a new run, a no and an instruction are
        investigating, and the next node corrects it."""
        if isinstance(payload, Command):
            return (
                "executing"
                if getattr(payload.resume, "kind", None) == "approve"
                else "investigating"
            )
        if payload is not None:
            return "investigating"
        snapshot = await self.graph.aget_state(self.config(incident_id))
        node = snapshot.next[0] if snapshot.next else ""
        if node == "report" and _verified(snapshot.values):
            return "verifying"
        return NODE_STATUS.get(node, "investigating")

    async def _traced(self, incident_id: str) -> RunnableConfig:
        """The run's config, after storing the link to its trace on the incident. Tracing is
        optional: if it fails, the run goes on untraced."""
        incident = await self.repo.get(incident_id)
        if self.tracer is None or incident is None:
            return self.config(incident_id)
        try:
            url = self.tracer.trace_url(incident)
            if url is not None and url != incident.trace_url:
                await self.repo.set_trace_url(incident_id, url)
            return self.run_config(incident)
        except Exception as error:
            # By type only: a tracing client's message may quote its endpoint or key.
            log.warning(
                "incident %s: tracing failed (%s); running untraced",
                incident_id,
                type(error).__name__,
            )
            return self.config(incident_id)

    async def _settle(self, incident_id: str, snapshot: Any) -> None:
        """Set the terminal status of a finished thread from its outcome.

        The only place an incident is closed: after the run's last checkpoint, so a crash before
        it leaves the incident open and `recover_on_boot` finishes the run."""
        outcome = snapshot.values.get("outcome")
        await self.repo.set_status(incident_id, "resolved" if outcome == "resolved" else "closed")


def _verified(values: dict[str, Any]) -> bool:
    """Whether the run's last Verify passed (a failed one loops back and leaves it not ok)."""
    verification = values.get("verification")
    return verification is not None and verification.ok


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
