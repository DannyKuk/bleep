"""Approval grants: the plan hash, the HMAC over it, and the check every act tool runs first.

The approval store issues grants (`incidents/approvals.py`) and the act tools check them
(`tools/docker_act.py`); both use these functions, so neither imports the other. See
docs/design/guard.md, "Act tools".
"""

import hashlib
import hmac
import json

from pydantic import SecretStr

from bleep.schemas.graph import ApprovalGrant, Proposal, TargetSnapshot


class GrantInvalid(Exception):
    """The step is not covered by a grant for this exact plan; nothing was run."""


def plan_hash(proposal: Proposal, snapshots: list[TargetSnapshot]) -> str:
    """sha256 of the steps as canonical JSON, each with the container id its target resolved to.

    A recreated container keeps its name but not its id, so the same words make another plan.
    Steps whose target is not a container (Coolify applications) carry `null`.
    """
    resolved = {(s.host_id, s.target): s.container_id for s in snapshots}
    steps = [
        step.model_dump(mode="json") | {"container_id": resolved.get((step.host_id, step.target))}
        for step in proposal.steps
    ]
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
        raise GrantInvalid("no grant")
    if grant.incident_id != incident_id:
        raise GrantInvalid("the grant is for another incident")
    if grant.plan_hash != plan_hash:
        raise GrantInvalid("the grant is for another plan")
    key = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
    expected = grant_token(key, incident_id, plan_hash)
    if not hmac.compare_digest(grant.token.encode(), expected.encode()):
        raise GrantInvalid("the grant's token does not match")
