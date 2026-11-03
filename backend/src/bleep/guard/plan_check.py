"""The plan check: what code refuses in a proposal, whatever the model or the prompt said.

`check_plan` runs on every proposal before a human is asked. It reads the registry as it stands
(the last container listing), so a plan cannot name a container Bleep has never seen there.
See docs/design/guard.md.
"""

import typing
from dataclasses import dataclass
from typing import Literal

from bleep.hosts.config import UrlCheckConfig
from bleep.hosts.discovery import is_self
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
    "unknown_url",
    "unknown_container",
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
    host, a protected container, a Bleep container (`bleep.self=true`), a verification URL
    that is not one of the host's health checks in the hosts file, and a verification container
    that is not on the host.
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
    if proposal.kind == "act":
        violations.extend(_check_urls(proposal, registry, host_id))
        violations.extend(_check_containers(proposal, registry, host_id))
    return violations


def host_urls(registry: Registry, host_id: str) -> dict[str, UrlCheckConfig]:
    """Every health check of the host in the hosts file, by URL, across its services."""
    host = next((h for h in registry.hosts() if h.config.id == host_id), None)
    services = host.config.services.values() if host is not None else []
    return {cfg.url: cfg for overrides in services for cfg in overrides.urls}


def _check_urls(proposal: Proposal, registry: Registry, host_id: str) -> list[Violation]:
    """Verify fetches every URL of a plan that ran, so only the host's configured health checks
    may be named: a model-made URL could point Bleep's requests anywhere."""
    configured = host_urls(registry, host_id)
    return [
        Violation(
            "unknown_url",
            f"verification url {url}: is not a health check of host {host_id} in the hosts file;"
            " only those can be checked",
        )
        for url in proposal.verification.urls
        if url not in configured
    ]


def _check_containers(proposal: Proposal, registry: Registry, host_id: str) -> list[Violation]:
    """Verify inspects every container of the plan, so a name not on the host (a model's
    "shop_web" for shop-web-1) would fail every check and end the fix `unverified`. Reading a
    protected or Bleep's own container is harmless, so only the name is checked."""
    return [
        Violation(
            "unknown_container",
            f"verification container {name}: no container by that name on host {host_id}",
        )
        for name in proposal.verification.containers
        if registry.container(host_id, name) is None
    ]


def _check_step(
    number: int, step: PlanStep, registry: Registry, enabled: set[str], host_id: str
) -> list[Violation]:
    what = f"step {number} ({step.action} {step.target} on {step.host_id})"
    if step.action not in KNOWN_ACTIONS:
        return [Violation("unknown_action", f"{what}: is not an action Bleep has", number)]
    if step.action not in enabled:
        return [Violation("disabled_action", f"{what}: is not enabled; its plugin is off", number)]
    if step.host_id != host_id:
        why = f"targets another host; this incident is on {host_id}"
        return [Violation("other_host", f"{what}: {why}", number)]
    return check_target(number, step, registry)


def check_target(number: int, step: PlanStep, registry: Registry) -> list[Violation]:
    """Every reason `step`'s target may not be acted on now, read from the registry as it stands.

    Refused: a host not in the hosts file, unreachable or carrying a configuration error, a target
    not found on it, a protected container and a Bleep container. The plan check runs it before a
    human is asked and every act tool again right before its engine call (#24).
    """
    what = f"step {number} ({step.action} {step.target} on {step.host_id})"

    def refuse(rule: Rule, why: str) -> list[Violation]:
        return [Violation(rule, f"{what}: {why}", number)]

    host = next((h for h in registry.hosts() if h.config.id == step.host_id), None)
    if host is None:
        return refuse("not_found", "the host is not in the hosts file")
    if not host.reachable:  # `error` then holds the connection failure, not a config problem
        return refuse(
            "host_error", "the host is unreachable right now, so its containers cannot be checked"
        )
    if host.error is not None:
        return refuse(
            "host_error",
            "the host has a configuration error, so its protected containers may not be known",
        )

    protected = protected_names(host)
    if step.action.startswith("coolify."):
        members = _coolify_members(registry.snapshot(step.host_id) or [], step.target)
        if not members:
            return refuse("not_found", "no Coolify application by that name on this host")
    else:
        container = registry.container(step.host_id, step.target)
        if container is None:
            return refuse("not_found", "no container by that name on this host")
        members = [container]

    found: list[Violation] = []
    for c in members:
        if is_self(c.labels):
            found += refuse("self", f"{c.name} is one of Bleep's own containers")
        elif c.name in protected:
            found += refuse("protected", f"{c.name} is protected in the hosts file")
    return found


def _coolify_members(containers: list[ContainerSummary], target: str) -> list[ContainerSummary]:
    """The containers of the Coolify application `target` names (by uuid, id or resource name)."""
    return [c for c in containers if target in (c.labels.get(k) for k in COOLIFY_APP_LABELS)]


def protected_names(host) -> set[str]:
    """Every name in protected_containers on the host's overrides, read from the hosts file.

    This does not go through discovery, so an override whose key matches no service group
    still protects its containers.
    """
    return {n for o in host.config.services.values() for n in o.protected_containers}
