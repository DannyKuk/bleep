"""Verify: prove the fix worked, or say plainly that it did not.

After Execute the node waits 20 s, then checks up to 3 times, 10 s apart, and stops at the first
check where everything passes. A check passes only when every planned container is running and
healthy (or running with no healthcheck) and every planned URL passes `check_url`. The plan is
the proposal's verification plan; when that is empty it falls back to the affected service's
containers (protected ones too), and then to the containers the plan's steps acted on.
Whatever the plan names, the incident's own symptoms are checked too: every container its signals
name and every URL they report failing, so a plan that checks something else cannot pass while
the failure that opened the incident goes on. So is every URL the hosts file gives the incident's
service, with its expected status and body text: a container that runs again does not show that
the service answers. A service with no URL is judged by its containers alone.
A host incident's disk, memory or load is read again on the host and judged against the same
thresholds that opened it (`hosts/readings.past_threshold`, load over the last minute); a host
incident about anything else has nothing to read again, so it ends `unverified` without a
loop-back.
A Coolify application the plan redeployed is checked as a whole, read from the engine each time:
the redeploy replaced its containers under new names, so they are left out of the name checks.
The redeploy step ends only once Coolify reports its deployment over (`plugins/coolify`), so the
wait here starts after the new containers are up.

Passing sets the outcome `resolved`. A failure with fewer than `verify_loops` (2) loop-backs so
far hands the failed checks to Investigate as the next round's instruction (an appended message,
as Diagnose's loop-back does; when the only failure is the missing evidence after an approved
stop, it asks for that evidence, not for a new fix); otherwise the outcome is `unverified`, and
so it is at once when there was nothing to check. Nothing here says "fixed": that word needs a
passing check. The clock that waits is `deps.sleep`, so tests never sleep.
Each check publishes a `verification` event; the notifier hears the round's final result once.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

import httpx
from langchain_core.messages import HumanMessage

from bleep import prompts
from bleep.graph.deps import GraphDeps
from bleep.graph.state import IncidentState, Node, plan_results
from bleep.guard.plan_check import COOLIFY_UUID, coolify_members, host_urls
from bleep.hosts.config import UrlCheckConfig
from bleep.hosts.coolify_names import coolify_container_name
from bleep.hosts.engine import ContainerNotFound, EngineUnavailable
from bleep.hosts.readings import past_threshold, readings
from bleep.hosts.shell import HostUnreadable, host_basics
from bleep.hosts.ssh import SshUnavailable
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.graph import Check, Proposal, StepResult, VerificationResult
from bleep.schemas.incidents import Signal
from bleep.schemas.settings import Thresholds
from bleep.watcher.http_checks import check_url

log = logging.getLogger(__name__)

SETTLE_S = 20  # before the first check, for the service to come up
RECHECK_S = 10  # between checks
CHECKS = 3  # at most, per round
CLOCK_SLACK_S = 120  # between Bleep's clock and a host's, for a redeployed app's start times


def _redeployed(proposal: Proposal | None) -> list[str]:
    """The Coolify applications the plan redeployed, each checked as a whole."""
    steps = proposal.steps if proposal is not None else []
    return list(dict.fromkeys(s.target for s in steps if s.action == "coolify.redeploy"))


UNPROVEN = "stopped as approved; nothing shows the service works (none of its URLs was checked)"
NOT_REREAD = (
    "only disk, memory and load can be read again; nothing shows this host's problem is over"
)
HOST_READINGS = ("disk", "memory", "load")


@dataclass(frozen=True)
class _Plan:
    """What to check: containers that must run, URLs that must answer, signalled containers the
    plan stopped, which must stay down, and the host readings (disk, memory, load) the incident's
    signals named. `unproven` says those stop checks are all there is about a stopped
    container: one already dead passes, so it proves nothing. Each stopped container needs its
    own evidence: a URL the incident's signals saw fail or one of its own service's. Another
    signalled container that runs, another service's URL or an unrelated redeploy is not that;
    a stopped container a redeploy of the plan replaced is left to its application's check.
    `blind` says the incident is about a host and none of its signals can be read again."""

    containers: list[str]
    urls: list[UrlCheckConfig]
    stopped: list[str]
    unproven: bool = False
    readings: list[str] = field(default_factory=list)
    blind: bool = False

    def empty(self) -> bool:
        return not (self.containers or self.urls or self.stopped or self.readings)


def _plan(deps: GraphDeps, state: IncidentState) -> _Plan:
    """The containers and URLs to check: the plan's, or else what it acted on, and always the
    incident's own symptoms (`_symptoms`) and its service's URLs. A signalled container the plan
    stopped (its last Docker step is a stop) is checked to be down, unless a redeploy of the plan
    replaced it or it is among the containers that must run (the fallback can put it there; that
    check then fails, the safe side). When no URL of the incident's is checked (its service has
    none and no signal saw one fail), those stop checks are all there is about the stopped
    containers, and the plan is `unproven` and cannot pass. URLs are compared and deduplicated by
    `_url_key`.

    A URL keeps its expected status and body text from the hosts file, whichever of the host's
    services it belongs to (the plan check allows any of them). The fallback checks every
    container of the service, protected ones too: reading one is harmless, and leaving out a
    database that is still down would let a check of the web container alone pass.
    """
    subject, proposal = state["subject"], state.get("proposal")
    planned = proposal.verification if proposal is not None else None
    service = deps.registry.service(subject.service_id) if subject.service_id else None
    known = host_urls(deps.registry, subject.host_id)
    if service is not None:
        known |= {cfg.url: cfg for cfg in service.urls}
    replaced = _replaced(deps, state)
    containers = list(planned.containers) if planned is not None else []
    urls = list(planned.urls) if planned is not None else []
    service_urls = [cfg.url for cfg in service.urls] if service is not None else []
    if not containers and not urls:
        if service is not None:
            containers = [c.name for c in service.containers]
        else:
            acted = proposal.steps if proposal is not None else []
            containers = [s.target for s in acted if s.action.startswith("docker.")]
    signals = state.get("signals", [])
    signalled, failing_urls = _symptoms(signals)
    # The URLs that say something about this incident: those its signals saw fail and its own
    # service's. Another service's URL the plan names says nothing about this one.
    own_urls = {*failing_urls, *service_urls}
    stopped = _stopped(proposal)
    left_out = [c for c in signalled if c in stopped or replaced(c)]
    named = [*containers, *(c for c in signalled if c not in left_out)]
    containers = [c for c in dict.fromkeys(named) if not replaced(c)]
    urls = _unique_urls([*urls, *service_urls, *failing_urls])
    down = [c for c in left_out if c in stopped and not replaced(c) and c not in containers]
    own_url_checked = bool({_url_key(u) for u in own_urls} & {_url_key(u) for u in urls})
    by_key = {_url_key(u): cfg for u, cfg in known.items()}
    checks = [by_key.get(_url_key(u)) or UrlCheckConfig(url=u) for u in urls]
    host_readings = list(dict.fromkeys(s.kind for s in signals if s.kind in HOST_READINGS))
    return _Plan(
        containers,
        checks,
        down,
        unproven=bool(down) and not own_url_checked,
        readings=host_readings,
        blind=subject.kind == "host" and not host_readings,
    )


def _unique_urls(urls: list[str]) -> list[str]:
    """Each URL once by `_url_key`, in the spelling seen first."""
    seen: dict[str, str] = {}
    for url in urls:
        seen.setdefault(_url_key(url), url)
    return list(seen.values())


def _url_key(url: str) -> str:
    """A URL as Verify compares it: scheme and host in lower case, without the default port, a
    trailing slash or a fragment. Text that does not parse is compared as it is."""
    try:
        parsed = httpx.URL(url.strip())
    except httpx.InvalidURL, ValueError:
        return url
    if not parsed.host:
        return url
    default = {"http": 80, "https": 443}.get(parsed.scheme)
    port = f":{parsed.port}" if parsed.port not in (None, default) else ""
    query = f"?{parsed.query.decode()}" if parsed.query else ""
    return f"{parsed.scheme}://{parsed.host}{port}{parsed.path.rstrip('/')}{query}"


def _stopped(proposal: Proposal | None) -> set[str]:
    """The containers whose last Docker step in the plan is a stop: a stop then a start of the
    same container means it should run."""
    last: dict[str, str] = {}
    for step in proposal.steps if proposal is not None else []:
        if step.action.startswith("docker."):
            last[step.target] = step.action
    return {target for target, action in last.items() if action == "docker.stop"}


def _replaced(deps: GraphDeps, state: IncidentState) -> Callable[[str], bool]:
    """Whether a container name is one a redeploy of this plan replaced; the application's own
    check covers those.

    Each redeployed target is first resolved to its one application uuid, from the registry as
    it is now (the new containers carry the same labels); only when the registry shows no
    application by that name is Gather's listing read. A target that names several uuids
    replaces nothing, so its containers are still checked by name. By
    Verify the registry may list only the new containers, so a container counts as replaced when
    Gather's listing (taken before the plan ran) shows it as one of that uuid's, or when its
    name is Coolify's for it: `<uuid>-<timestamp>` or `<service>-<uuid>-<timestamp>`. A preview
    container (`coolify.pullRequestId` set) is never replaced: a redeploy of the main application
    leaves the previews as they are."""
    apps = _redeployed(state.get("proposal"))
    if not apps:
        return lambda _name: False
    context = state.get("context")
    gathered = list(context.containers) if context is not None else []
    now = deps.registry.snapshot(state["subject"].host_id) or []
    uuids: set[str] = set()
    for app in apps:
        found = _app_uuids(now, app) or _app_uuids(gathered, app)
        uuids |= found if len(found) == 1 else set()
    members = {
        c.name
        for c in [*gathered, *now]
        if c.labels.get(COOLIFY_UUID) in uuids and _not_preview(c.labels)
    }
    return lambda name: name in members or any(coolify_container_name(name, u) for u in uuids)


def _app_uuids(containers: list[ContainerSummary], target: str) -> set[str]:
    """The application uuids `target` names among `containers`, as `coolify_app_uuid` reads
    them; one is an answer, several an ambiguity."""
    found = {c.labels.get(COOLIFY_UUID, "") for c in coolify_members(containers, target)}
    return found - {""}


def _not_preview(labels: dict[str, str]) -> bool:
    return labels.get("coolify.pullRequestId", "0") in ("", "0")


def _symptoms(signals: list[Signal]) -> tuple[list[str], list[str]]:
    """The containers the incident's signals name and the URLs they report failing."""
    containers = [s.container for s in signals if s.container]
    urls = [
        str(s.detail["url"])
        for s in signals
        if s.kind == "url_failed" and not s.detail.get("ok") and s.detail.get("url")
    ]
    return list(dict.fromkeys(containers)), urls


async def _check_container(deps: GraphDeps, host_id: str, name: str) -> Check:
    try:
        summary = (await deps.registry.engine(host_id).inspect(name)).summary
    except ContainerNotFound:
        return Check(kind="container", target=name, ok=False, detail="not found")
    except EngineUnavailable, KeyError:
        return Check(kind="container", target=name, ok=False, detail="host not reachable")
    if summary.state != "running":
        return Check(kind="container", target=name, ok=False, detail=f"is {summary.state}")
    if summary.health in ("unhealthy", "starting"):
        return Check(kind="container", target=name, ok=False, detail=f"is {summary.health}")
    return Check(
        kind="container",
        target=name,
        ok=True,
        detail="running, healthy" if summary.health == "healthy" else "running",
    )


def _deploy_starts(
    proposal: Proposal | None, results: list[StepResult] | None
) -> dict[str, datetime]:
    """When each application this plan redeployed had its redeploy step start, on Bleep's clock.

    Only this plan's results count (`plan_results`). A redeploy that never started (skipped, or
    refused at run time) has no start and its app no entry. When a plan redeploys an application
    twice, the last start wins: the later redeploy is the one whose containers must be newer."""
    steps = proposal.steps if proposal is not None else []
    starts: dict[str, datetime] = {}
    for r in plan_results(proposal, results):
        if not 0 <= r.step_index < len(steps):
            continue
        step = steps[r.step_index]
        if step.action == "coolify.redeploy" and r.target == step.target and r.started_at:
            starts[step.target] = r.started_at
    return starts


async def _check_application(
    deps: GraphDeps, host_id: str, app: str, since: datetime | None
) -> Check:
    """A redeployed Coolify application, read from the engine now: at least one container of
    it (previews aside) running that was created after its redeploy began, and none of those
    unhealthy or still starting. `since` None (the redeploy never started) fails the check.

    The creation time is the host's and `since` is Bleep's, so `CLOCK_SLACK_S` of skew is
    allowed. A host clock further behind makes new containers look old and the check fail, the
    safe side; a host clock ahead widens the window by as much, so a container created within
    the slack plus that lead before the deploy counts. A restart keeps a container's creation
    time, so an old container restarting after the deploy does not count. One with no creation
    time does not count either."""
    target = f"{app} (Coolify application)"
    if since is None:
        return Check(kind="container", target=target, ok=False, detail="not redeployed")
    try:
        listed = await deps.registry.engine(host_id).containers()
    except EngineUnavailable, KeyError:
        return Check(kind="container", target=target, ok=False, detail="host not reachable")
    floor = since - timedelta(seconds=CLOCK_SLACK_S)
    running = [
        c
        for c in coolify_members(listed, app)
        if c.state == "running"
        and _not_preview(c.labels)
        and c.created_at is not None
        and c.created_at >= floor
    ]
    if not running:
        return Check(
            kind="container", target=target, ok=False, detail="no container made by the redeploy"
        )
    bad = next((c for c in running if c.health in ("unhealthy", "starting")), None)
    if bad is not None:
        return Check(
            kind="container", target=target, ok=False, detail=f"{bad.name} is {bad.health}"
        )
    return Check(kind="container", target=target, ok=True, detail=f"{len(running)} running")


async def _check_url(client: httpx.AsyncClient, cfg: UrlCheckConfig) -> Check:
    result = await check_url(client, cfg)
    if result.ok:
        return Check(kind="url", target=cfg.url, ok=True, detail=f"answering {result.status}")
    return Check(kind="url", target=cfg.url, ok=False, detail=result.error or "failed")


async def _check_stopped(deps: GraphDeps, host_id: str, name: str) -> Check:
    """A signalled container the plan stopped: it passes only while it is down."""
    target = f"{name} (stopped by the plan)"
    try:
        summary = (await deps.registry.engine(host_id).inspect(name)).summary
    except ContainerNotFound:
        return Check(kind="container", target=target, ok=False, detail="not found")
    except EngineUnavailable, KeyError:
        return Check(kind="container", target=target, ok=False, detail="host not reachable")
    if summary.state == "running":
        return Check(kind="container", target=target, ok=False, detail="is running again")
    return Check(kind="container", target=target, ok=True, detail=f"is {summary.state}")


async def _check_host(
    deps: GraphDeps, host_id: str, kinds: list[str], calm: set[str]
) -> list[Check]:
    """The incident's disk, memory or load, read again on the host now (one read for all) and
    judged against the thresholds as they are now. Load is the last minute's: the 15-minute
    average the watcher opens on lags far behind a fix, past Verify's last check. One dip of a
    one-minute average is weak evidence, so load passes only when it was within its threshold
    at the previous check of this round too. `calm` holds the kinds that were within their
    threshold at the previous check; it is updated to this one's. A host whose OS Bleep cannot
    read basics on (neither Linux nor macOS) reports no readings, so each "could not be read"."""
    before = set(calm)
    calm.clear()
    shell = deps.registry.shell(host_id)
    try:
        found = readings(await host_basics(shell), load_minutes=1) if shell is not None else {}
    except SshUnavailable, OSError:
        return [Check(kind="host", target=k, ok=False, detail="host not reachable") for k in kinds]
    except HostUnreadable:
        return [Check(kind="host", target=k, ok=False, detail="could not be read") for k in kinds]
    thresholds = deps.settings_store.thresholds()
    checks = []
    for kind in kinds:
        detail = found.get(kind)
        if detail is None:
            checks.append(Check(kind="host", target=kind, ok=False, detail="could not be read"))
            continue
        within = not past_threshold(kind, detail, thresholds)
        if within:
            calm.add(kind)
        ok = within and (kind != "load" or kind in before)
        checks.append(
            Check(kind="host", target=kind, ok=ok, detail=_reading(kind, detail, thresholds))
        )
    return checks


def _reading(kind: str, detail: dict, t: Thresholds) -> str:
    """A reading against its threshold, in Bleep's words: the mount name is host text, left out."""
    match kind:
        case "disk":
            return (
                f"{detail['percent']:g} % used on the fullest disk, "
                f"needs under {t.disk_percent:g} %"
            )
        case "memory":
            return (
                f"{detail['available_percent']:.1f} % available, "
                f"needs at least {t.memory_available_percent_min:g} %"
            )
        case _:
            return (
                f"{detail['load_per_cpu']:.2f} per CPU over the last minute, "
                f"needs at most {t.load_per_cpu:g} on two checks in a row"
            )


def loop_back_prompt(result: VerificationResult) -> str:
    """`verify_unproven` when every failed check is only the missing evidence after an approved
    stop, so Investigate looks for that evidence instead of a fault; else `verify_failed`."""
    failed = [c for c in result.checks if not c.ok]
    if failed and all(c.kind == "symptom" and c.detail == UNPROVEN for c in failed):
        return "graph/verify_unproven"
    return "graph/verify_failed"


def _describe(result: VerificationResult) -> str:
    return "\n".join(f"- {c.kind} {c.target}: {c.detail}" for c in result.checks if not c.ok)


def make(deps: GraphDeps) -> Node:
    async def one_check(
        host_id: str,
        plan: _Plan,
        apps: list[str],
        starts: dict[str, datetime],
        attempt: int,
        calm: set[str],
    ) -> VerificationResult:
        owned = deps.http is None
        client = deps.http or httpx.AsyncClient()
        try:
            checks = [await _check_application(deps, host_id, app, starts.get(app)) for app in apps]
            checks += [await _check_container(deps, host_id, name) for name in plan.containers]
            checks += [await _check_stopped(deps, host_id, name) for name in plan.stopped]
            if plan.unproven:
                checks += [
                    Check(kind="symptom", target=name, ok=False, detail=UNPROVEN)
                    for name in plan.stopped
                ]
            checks += [await _check_url(client, cfg) for cfg in plan.urls]
            if plan.readings:
                checks += await _check_host(deps, host_id, plan.readings, calm)
            if plan.blind:
                checks.append(Check(kind="symptom", target=host_id, ok=False, detail=NOT_REREAD))
        finally:
            if owned:
                await client.aclose()
        return VerificationResult(ok=all(c.ok for c in checks), checks=checks, attempt=attempt)

    async def verify(state: IncidentState) -> dict:
        incident_id = state["incident_id"]
        subject = state["subject"]
        proposal = state.get("proposal")
        plan = _plan(deps, state)
        apps = _redeployed(proposal)
        starts = _deploy_starts(proposal, state.get("step_results"))

        result = VerificationResult(ok=False)
        if plan.empty() and not apps:
            # Nothing to look at is not a pass, and another look at nothing would not help either.
            await deps.bus.publish(incident_id, "verification", _payload(result))
        else:
            await deps.sleep(SETTLE_S)
            calm: set[str] = set()
            for attempt in range(1, CHECKS + 1):
                result = await one_check(subject.host_id, plan, apps, starts, attempt, calm)
                await deps.bus.publish(incident_id, "verification", _payload(result))
                if result.ok or attempt == CHECKS:
                    break
                await deps.sleep(RECHECK_S)

        incident = await deps.incidents.get(incident_id)
        if incident is not None:
            try:
                await deps.notifier.verification(incident, result)
            except Exception as error:  # a channel outage must not fail the verification
                log.warning("verify: notifying %s failed: %s", incident_id, type(error).__name__)

        loops = state.get("verify_loops", 0)
        update: dict = {"verification": result, "verify_loops": loops}
        if result.ok:
            update["outcome"] = "resolved"
        elif (
            result.checks
            and not plan.blind  # no plan can make that symptom readable
            and loops < deps.settings_store.budgets().verify_loops
        ):
            update["verify_loops"] = loops + 1
            update["messages"] = [
                HumanMessage(
                    prompts.load(
                        loop_back_prompt(result),
                        checks=_describe(result),
                        budget=str(deps.settings_store.budgets().investigate_tool_calls),
                    )
                )
            ]
        else:
            update["outcome"] = "unverified"
        return update

    return verify


def _payload(result: VerificationResult) -> dict:
    return {
        "ok": result.ok,
        "attempt": result.attempt,
        "checks": [c.model_dump(mode="json") for c in result.checks],
    }


def route(state: IncidentState) -> Literal["investigate", "report"]:
    """A failed check with loop-backs left goes to Investigate; everything else to Report."""
    verification = state.get("verification")
    if verification is not None and not verification.ok and state.get("outcome") is None:
        return "investigate"
    return "report"
