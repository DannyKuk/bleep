"""The plan check: what code refuses in a proposal, whatever the model or the prompt said.

`check_plan` runs on every proposal before a human is asked. It reads the registry as it stands
(the last container listing), so a plan cannot name a container Bleep has never seen there.
See docs/design/guard.md.
"""

import re
import typing
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

from bleep.hosts.config import UrlCheckConfig
from bleep.hosts.discovery import is_self
from bleep.hosts.registry import Registry
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.graph import Action, PlanStep, Proposal

MAX_STEPS = 3
KNOWN_ACTIONS: frozenset[str] = frozenset(typing.get_args(Action))
# The labels a coolify.* target is matched on. Not `coolify.applicationId`: Coolify's API never
# shows an application's id, and a bare number could match another application's name.
COOLIFY_UUID, COOLIFY_NAME = "coolify.applicationUuid", "coolify.resourceName"
COOLIFY_APP_LABELS = (COOLIFY_UUID, COOLIFY_NAME)
# The params each action takes; an action not listed here takes whatever it is given.
ACTION_PARAMS: dict[str, frozenset[str]] = {
    "coolify.redeploy": frozenset(),
    "coolify.set_env": frozenset({"key", "value"}),
}
ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")  # one line, no "=", at most 128
MAX_ENV_VALUE = 10_000

Rule = Literal[
    "plan_shape",
    "too_many_steps",
    "unknown_action",
    "disabled_action",
    "other_host",
    "host_error",
    "not_found",
    "bad_params",
    "set_env_without_redeploy",
    "protected",
    "self",
    "unknown_url",
    "unknown_container",
    "healthy_target",
]


@dataclass(frozen=True)
class Violation:
    """One reason a plan is refused. `step` is the 1-based step it is about, None for the plan."""

    rule: Rule
    text: str
    step: int | None = None


def check_plan(
    proposal: Proposal,
    registry: Registry,
    enabled_actions: set[str],
    host_id: str,
    *,
    incident_containers: Collection[str],
) -> list[Violation]:
    """Every reason to refuse `proposal` for an incident on `host_id`; empty means it may go on.

    Refused: an `act` plan with no steps or over 3, a `no_action` with steps, an unknown or
    disabled action, a target on another host, any target on a host whose state carries an error
    (an ambiguous override means its protected list may not apply), a target not found on the
    host, a protected container, a Bleep container (`bleep.self=true`), a Docker action on a
    container that works (running and healthy, or running with no health check) unless it is one
    of the incident's own (`incident_containers`, by name; Propose says which), a verification
    URL that is not one of the host's health checks in the hosts file, and a verification
    container that is not on the host.
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
    named = set(incident_containers)
    for number, step in enumerate(proposal.steps, start=1):
        found = _check_step(number, step, registry, enabled_actions, host_id)
        violations.extend(found or _check_working(number, step, registry, named))
    violations.extend(_check_set_env_redeploy(proposal))
    if proposal.kind == "act":
        violations.extend(_check_urls(proposal, registry, host_id))
        violations.extend(_check_containers(proposal, registry, host_id))
    return violations


def _check_set_env_redeploy(proposal: Proposal) -> list[Violation]:
    """A Coolify env var takes effect only at the next deploy, so a `coolify.set_env` must be
    followed by a `coolify.redeploy` of the same application on the same host; else Verify would
    check the old release and could call a change that never applied a fix."""
    found: list[Violation] = []
    steps = proposal.steps
    for number, step in enumerate(steps, start=1):
        if step.action != "coolify.set_env":
            continue
        later = steps[number:]
        if any(
            (s.action, s.target, s.host_id) == ("coolify.redeploy", step.target, step.host_id)
            for s in later
        ):
            continue
        what = f"step {number} ({step.action} {step.target} on {step.host_id})"
        why = (
            f"an env var applies only at the next deploy; add a coolify.redeploy of {step.target}"
            f" on {step.host_id} after this step, naming the application exactly as this step"
            " does"
        )
        found.append(Violation("set_env_without_redeploy", f"{what}: {why}", number))
    return found


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
            f"verification url {url}: is not a health check of host {host_id} in its settings;"
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


def _check_working(
    number: int, step: PlanStep, registry: Registry, named: set[str]
) -> list[Violation]:
    """A Docker action on a container that works is no fix for an incident about something
    else: a planted log line that names a healthy container is the usual way such a step gets
    into a plan (#35). The incident's own containers stay allowed; Propose's
    `_incident_containers` says which those are, from trusted sources only (#112).
    A Coolify action is left alone: a release can fail its URLs while every container is
    healthy, and a rollback is then the fix."""
    if not step.action.startswith("docker."):
        return []
    container = registry.container(step.host_id, step.target)
    if container is None or container.name in named or container.state != "running":
        return []
    if container.health == "healthy":
        how = "running and healthy"
    elif container.health == "none":
        how = "running with no health check"
    else:
        return []
    what = f"step {number} ({step.action} {step.target} on {step.host_id})"
    why = (
        f"{container.name} is {how}, and it is not one of this incident's containers; acting on "
        "a container that works is not a fix for this incident"
    )
    return [Violation("healthy_target", f"{what}: {why}", number)]


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
    return check_params(number, step) or check_target(number, step, registry)


def check_params(number: int, step: PlanStep) -> list[Violation]:
    """A Coolify step takes exactly its params: none for a redeploy, `key` and `value` for a
    set_env, with a key Coolify accepts. The text never quotes a value."""
    wanted = ACTION_PARAMS.get(step.action)
    if wanted is None:
        return []
    what = f"step {number} ({step.action} {step.target} on {step.host_id})"
    if set(step.params) != wanted:
        takes = ", ".join(sorted(wanted)) or "no params"
        return [Violation("bad_params", f"{what}: takes {takes}", number)]
    if "key" in wanted and not ENV_KEY.fullmatch(step.params["key"]):
        why = "key must be letters, digits and _, not starting with a digit"
        return [Violation("bad_params", f"{what}: {why}", number)]
    if len(step.params.get("value", "")) > MAX_ENV_VALUE:
        why = f"value is longer than {MAX_ENV_VALUE} characters"
        return [Violation("bad_params", f"{what}: {why}", number)]
    return []


def check_target(number: int, step: PlanStep, registry: Registry) -> list[Violation]:
    """Every reason `step`'s target may not be acted on now, read from the registry as it stands.

    Refused: a host not in the hosts file, unreachable or carrying a configuration error, a target
    not found on it, a protected container and a Bleep container. A Coolify target must name one
    application by uuid or resource name, and is refused when its uuid, its name or any of its
    containers is protected, or any of its containers is Bleep's own. The plan check runs it
    before a human is asked and every act tool again right before its engine call (#24).
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
        members = coolify_members(registry.snapshot(step.host_id) or [], step.target)
        if not members:
            return refuse("not_found", "no Coolify application by that name or uuid on this host")
        uuids = {c.labels[COOLIFY_UUID] for c in members if c.labels.get(COOLIFY_UUID)}
        if len(uuids) != 1:
            why = (
                "names more than one Coolify application; use its uuid"
                if uuids
                else "its containers carry no Coolify application uuid; redeploy it from Coolify"
                " once"
            )
            return refuse("not_found", why)
        # Coolify names an app's containers anew at every deploy, so its uuid and its name
        # protect it too, not only a container name that lasts until the next deploy. The name
        # label is Coolify's slug of the app's name, so a protected name is compared slugged.
        names = {c.labels.get(COOLIFY_NAME) for c in members} - {None}
        held = sorted(uuids & protected) + sorted(p for p in protected if coolify_slug(p) in names)
        if held:
            return refuse(
                "protected", f"the Coolify application {held[0]} is protected in the hosts file"
            )
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


def coolify_members(containers: list[ContainerSummary], target: str) -> list[ContainerSummary]:
    """The containers of the Coolify application `target` names (by uuid or resource name)."""
    return [c for c in containers if target in (c.labels.get(k) for k in COOLIFY_APP_LABELS)]


class NoCoolifyApp(LookupError):
    """A Coolify step's target names no single application on its host now."""


def coolify_app_uuid(registry: Registry, step: PlanStep) -> str:
    """The one application uuid a Coolify step's target names on its host; raises
    `NoCoolifyApp` when it names none or several (`check_target` refuses both first)."""
    members = coolify_members(registry.snapshot(step.host_id) or [], step.target)
    uuids = {c.labels[COOLIFY_UUID] for c in members if c.labels.get(COOLIFY_UUID)}
    if len(uuids) != 1:
        raise NoCoolifyApp(step.target)
    return uuids.pop()


def coolify_slug(name: str) -> str:
    """`name` as Coolify writes it into `coolify.resourceName`: Laravel's `Str::slug`
    (`defaultLabels` in Coolify's bootstrap/helpers/docker.php). Lower-case ASCII, `_` and runs
    of spaces or `-` become one `-`, `@` reads `at`, anything else that is not a letter or a
    digit is dropped, and `-` is trimmed: `Shop API` is `shop-api`, `static:main` `staticmain`.
    Laravel transliterates with a table; NFKD with the marks dropped stands in for it."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"_+", "-", text).replace("@", "-at-")
    text = re.sub(r"[^-a-z0-9\s]+", "", text.lower())
    return re.sub(r"[-\s]+", "-", text).strip("-")


def protected_names(host) -> set[str]:
    """Every name in protected_containers on the host's overrides, read from the hosts file.

    This does not go through discovery, so an override whose key matches no service group
    still protects its containers.
    """
    return {n for o in host.config.services.values() for n in o.protected_containers}
