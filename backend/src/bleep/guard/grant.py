"""Approval grants: the plan hash, the HMAC over it, and the check every act tool runs first.

The approval store issues grants (`incidents/approvals.py`) and the act tools check them
(`tools/docker_act.py`); both use these functions, so neither imports the other. See
docs/design/guard.md, "Act tools".
"""

import hashlib
import hmac
import json

from pydantic import SecretStr

from bleep.schemas.graph import ApprovalGrant, ApprovalRecord, PlanStep, Proposal, TargetSnapshot
from bleep.schemas.shown import Shown


class GrantInvalid(Exception):
    """The step is not covered by a grant for this exact plan; nothing was run. The message is
    English; `shown` is the same as a shown text, for the notice that says why (#455)."""

    def __init__(self, text: str, shown: Shown | None = None) -> None:
        super().__init__(text)
        self.shown = shown


def binds_app(step: PlanStep) -> bool:
    """Whether `step`'s target is a Coolify application; every other target is a container."""
    return step.action.startswith("coolify.")


def step_snapshot(snapshots: list[TargetSnapshot], step: PlanStep) -> TargetSnapshot | None:
    """The approval's snapshot of `step`'s target: its application for a Coolify step, else its
    container. A container and an application of the same name are told apart."""
    app = binds_app(step)
    return next(
        (
            s
            for s in snapshots
            if (s.host_id, s.target) == (step.host_id, step.target)
            and (s.app_uuid is not None) == app
        ),
        None,
    )


def plan_hash(proposal: Proposal, snapshots: list[TargetSnapshot]) -> str:
    """sha256 of the steps as canonical JSON, each with what its target resolved to: the container
    id, or for a Coolify step the application uuid (#139).

    A recreated container keeps its name but not its id, and a recreated Coolify application its
    name but not its uuid, so the same words make another plan.
    """
    steps = []
    for step in proposal.steps:
        snapshot = step_snapshot(snapshots, step)
        if binds_app(step):
            bound = {"container_id": None, "app_uuid": snapshot and snapshot.app_uuid}
        else:
            bound = {"container_id": snapshot and snapshot.container_id}
        steps.append(step.model_dump(mode="json") | bound)
    canonical = json.dumps(steps, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def grant_token(secret: str, incident_id: str, plan_hash: str) -> str:
    """HMAC-SHA256 keyed with `BLEEP_SECRET` over the incident id followed by the plan hash."""
    message = (incident_id + plan_hash).encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_grant(
    grant: ApprovalGrant | None, incident_id: str, plan_hash: str, secret: SecretStr | str
) -> None:
    """Raise `GrantInvalid` unless `grant` was issued for this incident's plan `plan_hash`.

    The token is recomputed from `incident_id` and `plan_hash` and compared in constant time, so
    a grant whose fields were edited to match carries the wrong token.
    """
    if grant is None:
        raise GrantInvalid("no grant", Shown.of("grant/none"))
    if grant.incident_id != incident_id:
        raise GrantInvalid("the grant is for another incident", Shown.of("grant/other_incident"))
    if grant.plan_hash != plan_hash:
        raise GrantInvalid("the grant is for another plan", Shown.of("grant/other_plan"))
    key = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
    expected = grant_token(key, incident_id, plan_hash)
    if not hmac.compare_digest(grant.token.encode(), expected.encode()):
        raise GrantInvalid("the grant's token does not match", Shown.of("grant/bad_token"))


def check_step_index(step: PlanStep, plan: ApprovalRecord, step_index: int) -> None:
    """Raise `GrantInvalid` unless `step` is step `step_index` of the approved `plan`."""
    steps = plan.proposal.steps
    number = str(step_index + 1)
    if not 0 <= step_index < len(steps):
        raise GrantInvalid(
            f"the approved plan has no step {number}", Shown.of("grant/no_step", number=number)
        )
    if steps[step_index] != step:
        raise GrantInvalid(
            f"this is not step {number} of the approved plan",
            Shown.of("grant/not_step", number=number),
        )


def verify_step(
    step: PlanStep,
    grant: ApprovalGrant | None,
    plan: ApprovalRecord,
    step_index: int,
    secret: SecretStr | str,
) -> None:
    """Raise `GrantInvalid` unless `step` is step `step_index` of the approved `plan` and `grant`
    was issued for that plan as it is now (its hash recomputed, not read). Every act handler runs
    this before anything else."""
    check_step_index(step, plan, step_index)
    verify_grant(grant, plan.incident_id, plan_hash(plan.proposal, plan.snapshots), secret)
