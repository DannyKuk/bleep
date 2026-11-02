"""The plan check: what code refuses in a proposal, whatever the model or the prompt said.

`check_plan` runs on every proposal before a human is asked. It reads the registry as it stands
(the last container listing), so a plan cannot name a container Bleep has never seen there.
See docs/design/guard.md.
"""

import typing
from dataclasses import dataclass
from typing import Literal

from bleep.hosts.discovery import SELF_LABEL
from bleep.hosts.registry import Registry
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.graph import Action, PlanStep, Proposal

MAX_STEPS = 3
KNOWN_ACTIONS: frozenset[str] = frozenset(typing.get_args(Action))
# The labels a Coolify application's containers carry; a coolify.* target is one of their values.
COOLIFY_APP_LABELS = ("coolify.applicationUuid", "coolify.applicationId", "coolify.resourceName")

Rule = Literal[
    "plan_shape",
    "too_many_steps",
    "unknown_action",
    "disabled_action",
    "other_host",
    "host_error",
    "not_found",
    "protected",
    "self",
]


@dataclass(frozen=True)
class Violation:
    """One reason a plan is refused. `step` is the 1-based step it is about, None for the plan."""

    rule: Rule
    text: str
    step: int | None = None


def check_plan(
    proposal: Proposal, registry: Registry, enabled_actions: set[str], host_id: str
) -> list[Violation]:
    """Every reason to refuse `proposal` for an incident on `host_id`; empty means it may go on.

    Refused: an `act` plan with no steps or over 3, a `no_action` with steps, an unknown or
    disabled action, a target on another host, any target on a host whose state carries an error
    (an ambiguous override means its protected list may not apply), a target not found on the
    host, a protected container and a Bleep container (`bleep.self=true`).
    """
    violations: list[Violation] = []
    if proposal.kind == "no_action" and proposal.steps:
        violations.append(Violation("plan_shape", "a no_action proposal must have no steps"))
    if proposal.kind == "act" and not proposal.steps:
        violations.append(Violation("plan_shape", "an act proposal needs at least one step"))
    if len(proposal.steps) > MAX_STEPS:
        violations.append(
            Violation(
                "too_many_steps",
                f"the plan has {len(proposal.steps)} steps; at most {MAX_STEPS} are allowed",
            )
        )
    for number, step in enumerate(proposal.steps, start=1):
        violations.extend(_check_step(number, step, registry, enabled_actions, host_id))
    return violations


def _check_step(
    number: int, step: PlanStep, registry: Registry, enabled: set[str], host_id: str
) -> list[Violation]:
    what = f"step {number} ({step.action} {step.target} on {step.host_id})"

    def refuse(rule: Rule, why: str) -> list[Violation]:
        return [Violation(rule, f"{what}: {why}", number)]

    if step.action not in KNOWN_ACTIONS:
        return refuse("unknown_action", "is not an action Bleep has")
    if step.action not in enabled:
        return refuse("disabled_action", "is not enabled; its plugin is off")
    if step.host_id != host_id:
        return refuse("other_host", f"targets another host; this incident is on {host_id}")
    host = next((h for h in registry.hosts() if h.config.id == host_id), None)
    if host is None:
        return refuse("not_found", "the host is not in the hosts file")
    if host.error is not None:
        return refuse(
            "host_error",
            "the host has a configuration error, so its protected containers may not be known",
        )

    protected = {name for s in registry.services(host_id) for name in s.protected}
    if step.action.startswith("coolify."):
        members = _coolify_members(registry.snapshot(host_id) or [], step.target)
        if not members:
            return refuse("not_found", "no Coolify application by that name on this host")
    else:
        container = registry.container(host_id, step.target)
        if container is None:
            return refuse("not_found", "no container by that name on this host")
        members = [container]

    found: list[Violation] = []
    for c in members:
        if c.labels.get(SELF_LABEL) == "true":
            found += refuse("self", f"{c.name} is one of Bleep's own containers")
        elif c.name in protected:
            found += refuse("protected", f"{c.name} is protected in the hosts file")
    return found


def _coolify_members(containers: list[ContainerSummary], target: str) -> list[ContainerSummary]:
    """The containers of the Coolify application `target` names (by uuid, id or resource name)."""
    return [c for c in containers if target in (c.labels.get(k) for k in COOLIFY_APP_LABELS)]
