"""Act tools: restart, stop and start a container, only for a step of a plan a human approved.

An act tool is not a LangChain tool and is never offered to a model: `ToolRegistry.read_tools`
holds only read tools, and Execute calls these directly. Before its one engine call a tool
checks, in this order, and refuses with nothing run:

1. the step is the plan's step at `step_index`, and the grant was issued for this incident and
   for the plan as it is now (its hash is recomputed, not read): else `GrantInvalid`;
2. the target as the registry sees it now: the host is reachable and has no configuration error,
   and the container is neither protected nor Bleep's own (`check_target`, the plan check's own
   rules): else `StepRefused`;
3. the plan holds a snapshot of the target, and the container id it approved is not Bleep's own.

The engine is called with the approved container id, never the name, so a container recreated
under the same name since the approval is not acted on: the call fails as "the container is
gone". Every refusal is recorded as `blocked_action` in the guard log. See docs/design/guard.md.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import SecretStr

from bleep.guard.grant import GrantInvalid, plan_hash, verify_grant
from bleep.guard.log import GuardLogger
from bleep.guard.plan_check import check_target
from bleep.hosts.discovery import is_self
from bleep.hosts.engine import ContainerNotFound, EngineError, EngineUnavailable
from bleep.hosts.registry import Registry
from bleep.schemas.graph import Action, ApprovalGrant, ApprovalRecord, PlanStep, StepResult

log = logging.getLogger(__name__)

# Each Docker action, the Engine method it calls and the word its result uses.
_DOCKER: dict[Action, tuple[str, str]] = {
    "docker.restart": ("restart", "restarted"),
    "docker.stop": ("stop", "stopped"),
    "docker.start": ("start", "started"),
}


class StepRefused(Exception):
    """The guard refused the step's target (host error, protected, Bleep's own); nothing ran."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _failure(error: EngineError) -> str:
    """A fixed reason: engine messages are host text and do not belong in Bleep's own."""
    if isinstance(error, ContainerNotFound):
        return "the container is gone; it was removed or recreated since approval"
    if isinstance(error, EngineUnavailable):
        return "host unreachable"
    return "the engine refused the request"


@dataclass(frozen=True)
class ActTool:
    """One Docker action, run only for a step of an approved plan (see the module docstring)."""

    name: Action
    secret: SecretStr | str = field(repr=False)  # BLEEP_SECRET; a repr must not print it
    guard_log: GuardLogger | None = None
    clock: Callable[[], datetime] = _utcnow

    async def run(
        self,
        step: PlanStep,
        grant: ApprovalGrant | None,
        registry: Registry,
        *,
        plan: ApprovalRecord,
        step_index: int,
    ) -> StepResult:
        """Run `step`, step `step_index` of the approved `plan`, under `grant`.

        Raises `GrantInvalid` or `StepRefused` without touching the engine. An engine failure is
        a result with `ok=False`, not an exception: the call was made and may have done part.
        """
        container_id = self.check(step, grant, registry, plan=plan, step_index=step_index)
        method, done = _DOCKER[self.name]
        started = self.clock()
        try:
            await getattr(registry.engine(step.host_id), method)(container_id)
        except EngineError as error:
            log.warning("%s failed on %s: %s", self.name, step.host_id, type(error).__name__)
            ok, detail = False, _failure(error)
        else:
            ok, detail = True, f"{done} {step.target}"
        return StepResult(
            step_index=step_index,
            action=self.name,
            target=step.target,
            ok=ok,
            detail=detail,
            started_at=started,
            finished_at=self.clock(),
        )

    def check(
        self,
        step: PlanStep,
        grant: ApprovalGrant | None,
        registry: Registry,
        *,
        plan: ApprovalRecord,
        step_index: int,
    ) -> str:
        """Every check `run` makes before its engine call, without the call: the container id it
        would act on. Raises `GrantInvalid` or `StepRefused`, recorded as `blocked_action`.

        Execute runs it for every step before the first one runs, so a plan the guard refuses
        runs no step at all."""
        try:
            return self._authorize(step, grant, registry, plan, step_index)
        except GrantInvalid as refusal:
            what = f"step {step_index + 1} ({step.action} {step.target} on {step.host_id})"
            self._blocked(plan, f"{what}: refused, {refusal}")
            raise
        except StepRefused as refusal:
            self._blocked(plan, str(refusal))
            raise

    def _blocked(self, plan: ApprovalRecord, text: str) -> None:
        if self.guard_log is not None:
            self.guard_log.record(plan.incident_id, "blocked_action", text)

    def _authorize(
        self,
        step: PlanStep,
        grant: ApprovalGrant | None,
        registry: Registry,
        plan: ApprovalRecord,
        step_index: int,
    ) -> str:
        """The container id to act on, once every check passed; raises on the first that fails.

        A `StepRefused` message names the step, as the plan check's violations do."""
        what = f"step {step_index + 1} ({step.action} {step.target} on {step.host_id})"
        if step.action != self.name:
            raise StepRefused(f"{what}: {self.name} cannot run {step.action}")
        steps = plan.proposal.steps
        if not 0 <= step_index < len(steps):
            raise GrantInvalid(f"the approved plan has no step {step_index + 1}")
        if steps[step_index] != step:
            raise GrantInvalid(f"this is not step {step_index + 1} of the approved plan")
        verify_grant(grant, plan.incident_id, plan_hash(plan.proposal, plan.snapshots), self.secret)

        violations = check_target(step_index + 1, step, registry)
        if violations:
            raise StepRefused("; ".join(v.text for v in violations))

        snapshot = next(
            (s for s in plan.snapshots if (s.host_id, s.target) == (step.host_id, step.target)),
            None,
        )
        if snapshot is None:
            raise StepRefused(f"{what}: the approved plan holds no snapshot of this target")
        approved = registry.container(step.host_id, snapshot.container_id)
        if approved is not None and is_self(approved.labels):
            raise StepRefused(f"{what}: {approved.name} is one of Bleep's own containers")
        return snapshot.container_id


def docker_act_tools(
    secret: SecretStr | str,
    guard_log: GuardLogger | None = None,
    clock: Callable[[], datetime] = _utcnow,
) -> dict[str, ActTool]:
    """The Docker act tools by action name, keyed with `BLEEP_SECRET` (the grants' key).

    Kept apart from `ToolRegistry`: nothing here is a LangChain tool, so none can be bound to a
    model."""
    return {
        name: ActTool(name=name, secret=secret, guard_log=guard_log, clock=clock)
        for name in _DOCKER
    }
