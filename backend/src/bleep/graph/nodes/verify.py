"""Verify: prove the fix worked, or say plainly that it did not.

After Execute the node waits 20 s, then checks up to 3 times, 10 s apart, and stops at the first
check where everything passes. A check passes only when every planned container is running and
healthy (or running with no healthcheck) and every planned URL passes `check_url`. Every
container checked to run must not have been started again since Verify first saw it running
(`_after_fix`): read once before the wait, or at the first check that finds it running when it
was not then (still in its restart backoff, or unreadable); that check fails, so such a
container can pass only at a later check, against that read. Once Execute is done nothing Bleep
does restarts a container, so a restart policy that restarted it then is the failure repeating,
however it looks at the check (#320). The plan is the proposal's verification plan; when that
is empty it falls back to the affected service's containers (protected ones too), and then to
the containers the plan's steps acted on.
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
Its redeploy step must have succeeded, and every container the redeploy made must pass a
container check's rules, not just one (#332). The redeploy step ends only once Coolify reports
its deployment over (`plugins/coolify`), so the wait here starts after the new containers are
up; a step that timed out fails the check, since Coolify may still be deploying.

Passing sets the outcome `resolved`. A failure with fewer than `verify_loops` (2) loop-backs so
far hands the failed checks to Investigate as the next round's instruction (an appended message,
as Diagnose's loop-back does; when the only failure is the missing evidence after an approved
stop, it asks for that evidence, not for a new fix); otherwise the outcome is `unverified`, and
so it is at once when there was nothing to check. Nothing here says "fixed": that word needs a
passing check. The clock that waits is `deps.sleep`, so tests never sleep.
Each check publishes a `verification` event; the notifier hears the round's final result once.
`check_look` checks a look's service once, with these checks, for a look on request that ended
with no action (#421); Report decides with it whether the look ends `healthy`. `check_recovery`
checks, with them too, a service whose incident waits for a yes, for the watcher to close it as
healthy again on its own (#571).
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
from bleep.graph.shown import english
from bleep.graph.state import IncidentState, Node, plan_results
from bleep.guard.plan_check import coolify_members, host_urls
from bleep.hosts.config import UrlCheckConfig
from bleep.hosts.coolify_names import COOLIFY_UUID, coolify_container_name
from bleep.hosts.discovery import expects_exit, is_finished, is_self, service_key
from bleep.hosts.engine import ContainerNotFound, EngineUnavailable
from bleep.hosts.readings import past_threshold, readings
from bleep.hosts.shell import HostUnreadable, host_basics
from bleep.hosts.ssh import SshUnavailable
from bleep.hosts.url_check import check_url
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.graph import Check, Proposal, StepResult, VerificationResult
from bleep.schemas.incidents import Signal, Subject
from bleep.schemas.settings import Thresholds
from bleep.schemas.shown import Shown

log = logging.getLogger(__name__)

SETTLE_S = 20  # before the first check, for the service to come up
RECHECK_S = 10  # between checks
CHECKS = 3  # at most, per round
CLOCK_SLACK_S = 120  # between Bleep's clock and a host's, for a redeployed app's start times


def _redeployed(proposal: Proposal | None) -> list[str]:
    """The Coolify applications the plan redeployed, each checked as a whole."""
    steps = proposal.steps if proposal is not None else []
    return list(dict.fromkeys(s.target for s in steps if s.action == "coolify.redeploy"))


_UNPROVEN = Shown.of("verify/unproven")
_NOT_REREAD = Shown.of("verify/not_reread")
_CAME_UP = Shown.of("verify/came_up")
_NOT_GATHERED_RUNNING = Shown.of("verify/not_gathered_running")  # a look's one check, #421
_NOT_FINISHED = Shown.of("verify/not_finished")
_NO_SINGLE_APP = Shown.of("verify/no_single_app")
_STARTED_AGAIN = Shown.of("verify/started_again")
# The English of each, as a check stores it and a model reads it; `RESTARTED` keeps its
# `{now}` and `{then}`.
UNPROVEN = english(_UNPROVEN)
NOT_REREAD = english(_NOT_REREAD)
CAME_UP = english(_CAME_UP)
NOT_GATHERED_RUNNING = english(_NOT_GATHERED_RUNNING)
NOT_FINISHED = english(_NOT_FINISHED)
NO_SINGLE_APP = english(_NO_SINGLE_APP)
STARTED_AGAIN = english(_STARTED_AGAIN)
RESTARTED = english(Shown.of("verify/restarted", now="{now}", then="{then}"))
HOST_READINGS = ("disk", "memory", "load")
# The states and healths Docker reports that have a sentence of their own ("is exited"), since
# German needs a verb for each.
STATES = ("created", "paused", "restarting", "removing", "exited", "dead", "unhealthy", "starting")


def _is(state: str) -> Shown:
    """ "is exited", "is unhealthy": a container's state or health as a sentence."""
    if state in STATES:
        return Shown.of(f"verify/is_{state}")
    return Shown.of("verify/is_other", state=state)


def _check(
    kind: Literal["container", "url", "host", "symptom"],
    target: str | Shown,
    ok: bool,
    detail: Shown,
) -> Check:
    """A check with its English target and detail, and the texts they were made from."""
    if isinstance(target, Shown):
        return Check(
            kind=kind,
            target=english(target),
            ok=ok,
            detail=english(detail),
            target_shown=target,
            shown=detail,
        )
    return Check(kind=kind, target=target, ok=ok, detail=english(detail), shown=detail)


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


def _plan(deps: GraphDeps, state: IncidentState, redeploys: list[_Redeploy]) -> _Plan:
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
    replaced = _replaced(deps, state, {r.uuid for r in redeploys if r.uuid is not None})
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


def _replaced(deps: GraphDeps, state: IncidentState, uuids: set[str]) -> Callable[[str], bool]:
    """Whether a container name is one a redeploy of this plan replaced; the application's own
    check covers those. `uuids` are the redeployed applications as `_app_uuid` resolved them; a
    target that resolved to none replaces nothing, so its containers are still checked by name.

    By Verify the registry may list only the new containers, so a container counts as replaced
    when Gather's listing (taken before the plan ran) or the registry shows it as one of those
    uuids', or, when neither shows it with a uuid label (Coolify 4.1.x wrote none), when its
    name is Coolify's for one: `<uuid>-<timestamp>` or `<service>-<uuid>-<timestamp>`; one
    labelled as another app's is not replaced, whatever its name. A preview container
    (`coolify.pullRequestId` set) is never replaced: a redeploy of the main application leaves
    the previews as they are."""
    if not uuids:
        return lambda _name: False
    context = state.get("context")
    gathered = list(context.containers) if context is not None else []
    now = deps.registry.snapshot(state["subject"].host_id) or []
    listed = [*gathered, *now]
    members = {
        c.name for c in listed if c.labels.get(COOLIFY_UUID) in uuids and _not_preview(c.labels)
    }
    labelled = {c.name for c in listed if c.labels.get(COOLIFY_UUID)}
    return lambda name: (
        name in members
        or (name not in labelled and any(coolify_container_name(name, u) for u in uuids))
    )


def _of_app(name: str, label: str | None, uuid: str) -> bool:
    """Whether a container named `name` whose uuid label is `label` is one of application
    `uuid`'s: by that label, or, only when it carries none (Coolify 4.1.x wrote none), by
    Coolify's name for it. A container labelled as another app's is that app's, whatever its
    name."""
    if label:
        return label == uuid
    return coolify_container_name(name, uuid)


def _app_uuid(
    app: str,
    approved: str | None,
    now: list[ContainerSummary],
    gathered: list[ContainerSummary],
) -> str | None:
    """The one application uuid the redeployed target `app` stands for, or None.

    The uuid the approval bound (`approved`) is the one Coolify was asked to redeploy. Without
    it, the registry as it is now is read (the new containers carry the same labels), and only
    when it shows no application by that name is Gather's listing; a name with several uuids
    there resolves to none."""
    if approved is not None:
        return approved
    found = _app_uuids(now, app) or _app_uuids(gathered, app)
    return next(iter(found)) if len(found) == 1 else None


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


async def _first_reads(
    deps: GraphDeps, host_id: str, names: list[str]
) -> dict[str, ContainerSummary]:
    """The containers to check that are running before the settle wait, read once. One not
    running then (in its restart backoff, say) or that Bleep cannot read (gone, or the host
    unreachable) has no entry; `_check_container` adds it at the first check that finds it
    running, since the start it was waiting for is not a crash after the fix."""
    reads: dict[str, ContainerSummary] = {}
    for name in names:
        try:
            summary = (await deps.registry.engine(host_id).inspect(name)).summary
        except ContainerNotFound, EngineUnavailable, KeyError:
            continue
        if summary.state == "running":
            reads[name] = summary
    return reads


def _after_fix(first: ContainerSummary, now: ContainerSummary) -> Shown | None:
    """Why `now` was started again since `first`, Verify's first read of it running, or None.

    Docker raises the restart count only for its restart policy's restarts, and resets it only on
    a start or restart through its API, which nothing does once Execute is done; so a higher
    count is the policy restarting the container after the fix, whether a step of the plan acted
    on it or not. Both start times are the host's, so clock skew does not matter. A container
    with another id was recreated since (by Compose or Coolify): it counts its restarts from 0."""
    if now.id != first.id:
        return (
            Shown.of("verify/recreated", now=str(now.restart_count)) if now.restart_count else None
        )
    if now.restart_count > first.restart_count:
        return Shown.of(
            "verify/restarted", now=str(now.restart_count), then=str(first.restart_count)
        )
    if first.started_at and now.started_at and now.started_at > first.started_at:
        return _STARTED_AGAIN
    return None


async def _check_container(
    deps: GraphDeps, host_id: str, name: str, first_running: dict[str, ContainerSummary]
) -> Check:
    """A container that must run: running, not started again since Verify first saw it running
    (`first_running`), and not unhealthy or starting. The check that first sees it running adds
    it there and fails: a read compared with itself proves nothing, and a crash-looper caught in
    its few seconds up would pass after one look."""
    try:
        summary = (await deps.registry.engine(host_id).inspect(name)).summary
    except ContainerNotFound:
        return _check("container", name, False, Shown.of("verify/not_found"))
    except EngineUnavailable, KeyError:
        return _check("container", name, False, Shown.of("verify/host_not_reachable"))
    if summary.state != "running":
        return _check("container", name, False, _is(summary.state))
    first = first_running.get(name)
    if first is None:
        first_running[name] = summary
        return _check("container", name, False, _CAME_UP)
    started_again = _after_fix(first, summary)
    if started_again is not None:
        return _check("container", name, False, started_again)
    if summary.health in ("unhealthy", "starting"):
        return _check("container", name, False, _is(summary.health))
    healthy = summary.health == "healthy"
    return _check(
        "container", name, True, Shown.of("verify/running_healthy" if healthy else "verify/running")
    )


def _deploy_results(
    proposal: Proposal | None, results: list[StepResult] | None
) -> dict[str, StepResult]:
    """The result of each application's redeploy step in this plan, for those that started.

    Only this plan's results count (`plan_results`). A redeploy that never started (skipped, or
    refused at run time) has no entry. When a plan redeploys an application twice, the last
    start wins: the later redeploy is the one whose containers must be newer, and whose result
    counts."""
    steps = proposal.steps if proposal is not None else []
    found: dict[str, StepResult] = {}
    for r in plan_results(proposal, results):
        if not 0 <= r.step_index < len(steps):
            continue
        step = steps[r.step_index]
        if step.action == "coolify.redeploy" and r.target == step.target and r.started_at:
            found[step.target] = r
    return found


@dataclass
class _Redeploy:
    """A Coolify application the plan redeployed, followed through Verify's round.

    `uuid` pins the application (`_app_uuid`); `result` is its redeploy step's, None when it
    never started. `seen` names every container of the redeploy Verify saw this round, by id, so
    one that vanishes is noticed; `first_running` holds each one's first read running, as
    `_check_container`'s `first_running` does for a container checked by name."""

    target: str
    uuid: str | None
    result: StepResult | None
    seen: dict[str, str] = field(default_factory=dict)
    first_running: dict[str, ContainerSummary] = field(default_factory=dict)

    @property
    def since(self) -> datetime | None:
        return self.result.started_at if self.result is not None else None


async def _redeploys(deps: GraphDeps, state: IncidentState) -> list[_Redeploy]:
    """Each application the plan redeployed, with its uuid and its step's result. The uuid is
    the approval's for that step when the incident's latest approval is this plan's."""
    proposal = state.get("proposal")
    apps = _redeployed(proposal)
    if not apps:
        return []
    record = await deps.approvals.current(state["incident_id"])
    snapshots = record.snapshots if record is not None and record.proposal == proposal else []
    approved = {s.target: s.app_uuid for s in snapshots if s.app_uuid is not None}
    context = state.get("context")
    gathered = list(context.containers) if context is not None else []
    now = deps.registry.snapshot(state["subject"].host_id) or []
    results = _deploy_results(proposal, state.get("step_results"))
    return [
        _Redeploy(app, _app_uuid(app, approved.get(app), now, gathered), results.get(app))
        for app in apps
    ]


def _made(listed: list[ContainerSummary], redeploy: _Redeploy) -> list[ContainerSummary]:
    """The containers of the application its redeploy made: those that carry its uuid, or have
    Coolify's name for it (Coolify 4.1.x wrote no uuid label), previews aside, created after the
    redeploy began less `CLOCK_SLACK_S`. One with no creation time counts too, so it can fail
    the app; `_redeploy_failure` never lets it be the one that passes it."""
    uuid, since = redeploy.uuid, redeploy.since
    if uuid is None or since is None:
        return []
    floor = since - timedelta(seconds=CLOCK_SLACK_S)
    return [
        c
        for c in listed
        if _of_app(c.name, c.labels.get(COOLIFY_UUID), uuid)
        and _not_preview(c.labels)
        and (c.created_at is None or c.created_at >= floor)
    ]


async def _first_app_reads(deps: GraphDeps, host_id: str, redeploys: list[_Redeploy]) -> None:
    """Before the settle wait, what each redeploy made, read once: every container is `seen`,
    and each running one gets its first read. One not running then gets it at the first check
    that finds it running, which fails, as for a container checked by name."""
    if not redeploys:
        return
    try:
        listed = await deps.registry.engine(host_id).containers()
    except EngineUnavailable, KeyError:
        return
    for redeploy in redeploys:
        for c in _made(listed, redeploy):
            redeploy.seen[c.id] = c.name
            if c.state == "running":
                redeploy.first_running[c.id] = c


def _finished_job(deps: GraphDeps, host_id: str, container: ContainerSummary) -> Shown | None:
    """The line for `container` when it is a job that finished as the watcher reads one, else
    None: exited 0, and named in its service's `exits` (`expects_exit`, #84) or a one-shot with
    no restart policy to bring it back (`is_finished`)."""
    if container.state != "exited" or container.exit_code != 0:
        return None
    service = deps.registry.service(f"{host_id}/{service_key(container.name, container.labels)}")
    if expects_exit(service.exits if service is not None else [], container.name, container.labels):
        return Shown.of("verify/job_allowed", name=container.name)
    if is_finished(container):
        return Shown.of("verify/job_finished", name=container.name)
    return None


async def _check_application(deps: GraphDeps, host_id: str, redeploy: _Redeploy) -> Check:
    """A redeployed Coolify application, read from the engine now (#332).

    It fails when its redeploy step never started ("not redeployed") or did not succeed
    (Coolify reported it failed, or Bleep stopped waiting while it may still run), and when the
    target resolves to no single application. Otherwise every container the redeploy made
    (`_made`) is judged by `_redeploy_failure`. A job its service names in `exits` that exited 0
    counts neither for nor against the app, and its line is added to the detail.

    The creation time is the host's and the redeploy's start is Bleep's, so `CLOCK_SLACK_S` of
    skew is allowed. A host clock further behind makes new containers look old and the check
    fail, the safe side; a host clock ahead widens the window by as much, so a container created
    within the slack plus that lead before the deploy counts. A restart keeps a container's
    creation time, so an old container (created before that floor) restarting or shutting down
    after the deploy counts neither for nor against the app."""
    target = Shown.of("verify/target_app", name=redeploy.target)
    if redeploy.since is None:
        return _check("container", target, False, Shown.of("verify/not_redeployed"))
    if redeploy.result is not None and not redeploy.result.ok:
        return _check("container", target, False, _NOT_FINISHED)
    if redeploy.uuid is None:
        return _check("container", target, False, _NO_SINGLE_APP)
    try:
        listed = await deps.registry.engine(host_id).containers()
    except EngineUnavailable, KeyError:
        return _check("container", target, False, Shown.of("verify/host_not_reachable"))
    made = _made(listed, redeploy)
    present = {c.id for c in made}
    gone = [name for cid, name in redeploy.seen.items() if cid not in present]
    for c in made:
        redeploy.seen.setdefault(c.id, c.name)
    jobs = {c.id: _finished_job(deps, host_id, c) for c in made}
    notes = [note for note in jobs.values() if note is not None]
    judged = [c for c in made if jobs[c.id] is None]
    came_up = [c for c in judged if c.state == "running" and c.id not in redeploy.first_running]
    for c in came_up:
        redeploy.first_running[c.id] = c
    failure = _redeploy_failure(judged, gone, came_up, redeploy.first_running, bool(notes))
    if failure is not None:
        return _check("container", target, False, Shown.joined([failure, *notes], "; "))
    # One has a verb of its own in German ("1 läuft", "3 laufen").
    running = (
        Shown.of("verify/one_running")
        if len(judged) == 1
        else Shown.of("verify/n_running", n=str(len(judged)))
    )
    detail = Shown.joined([running, *notes], "; ")
    return _check("container", target, True, detail)


def _redeploy_failure(
    judged: list[ContainerSummary],
    gone: list[str],
    came_up: list[ContainerSummary],
    first_running: dict[str, ContainerSummary],
    finished_jobs: bool,
) -> Shown | None:
    """Why the containers a redeploy made do not verify their application, or None.

    The rules of a container check, each across all of them: one seen earlier this round is
    gone; one is not running; one was restarted by its restart policy (a new container starts
    at restart count 0, so any count came after the fix, #320); one is first seen running at
    this check (`came_up`), or started again since its first read; one is unhealthy or still
    starting. At least one with a creation time must be among them: one with none counts
    against the app, never for it."""
    if gone:
        return Shown.of("verify/gone", name=gone[0])
    if not any(c.created_at is not None for c in judged):
        return Shown.of(
            "verify/no_redeploy_container_runs" if finished_jobs else "verify/no_redeploy_container"
        )
    down = next((c for c in judged if c.state != "running"), None)
    if down is not None:
        return Shown.of("verify/named", name=down.name, what=_is(down.state))
    restarted = next((c for c in judged if c.restart_count > 0), None)
    if restarted is not None:
        return Shown.of(
            "verify/redeploy_restarted", name=restarted.name, now=str(restarted.restart_count)
        )
    if came_up:
        return Shown.of("verify/named", name=came_up[0].name, what=_CAME_UP)
    again = next((c for c in judged if _started_since(first_running[c.id], c)), None)
    if again is not None:
        return Shown.of("verify/redeploy_started_again", name=again.name)
    bad = next((c for c in judged if c.health in ("unhealthy", "starting")), None)
    if bad is not None:
        return Shown.of("verify/named", name=bad.name, what=_is(bad.health))
    return None


def _started_since(first: ContainerSummary, now: ContainerSummary) -> bool:
    return bool(first.started_at and now.started_at and now.started_at > first.started_at)


async def _check_url(client: httpx.AsyncClient, cfg: UrlCheckConfig) -> Check:
    result = await check_url(client, cfg)
    if result.ok:
        return _check("url", cfg.url, True, Shown.of("verify/answering", status=str(result.status)))
    if result.error is None:
        return _check("url", cfg.url, False, Shown.of("verify/url_failed"))
    # `check_url` words every error as a template; a check without one shows as stored.
    return Check(kind="url", target=cfg.url, ok=False, detail=result.error, shown=result.shown)


async def _check_stopped(deps: GraphDeps, host_id: str, name: str) -> Check:
    """A signalled container the plan stopped: it passes only while it is down."""
    target = Shown.of("verify/target_stopped", name=name)
    try:
        summary = (await deps.registry.engine(host_id).inspect(name)).summary
    except ContainerNotFound:
        return _check("container", target, False, Shown.of("verify/not_found"))
    except EngineUnavailable, KeyError:
        return _check("container", target, False, Shown.of("verify/host_not_reachable"))
    if summary.state == "running":
        return _check("container", target, False, Shown.of("verify/running_again"))
    return _check("container", target, True, _is(summary.state))


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
        unreachable = Shown.of("verify/host_not_reachable")
        return [_check("host", k, False, unreachable) for k in kinds]
    except HostUnreadable:
        return [_check("host", k, False, Shown.of("verify/could_not_read")) for k in kinds]
    thresholds = deps.settings_store.thresholds()
    checks = []
    for kind in kinds:
        detail = found.get(kind)
        if detail is None:
            checks.append(_check("host", kind, False, Shown.of("verify/could_not_read")))
            continue
        within = not past_threshold(kind, detail, thresholds)
        if within:
            calm.add(kind)
        ok = within and (kind != "load" or kind in before)
        checks.append(_check("host", kind, ok, _reading(kind, detail, thresholds)))
    return checks


def _reading(kind: str, detail: dict, t: Thresholds) -> Shown:
    """A reading against its threshold, in Bleep's words: the mount name is host text, left out."""
    match kind:
        case "disk":
            return Shown.of(
                "verify/disk_reading", percent=f"{detail['percent']:g}", limit=f"{t.disk_percent:g}"
            )
        case "memory":
            return Shown.of(
                "verify/memory_reading",
                percent=f"{detail['available_percent']:.1f}",
                limit=f"{t.memory_available_percent_min:g}",
            )
        case _:
            return Shown.of(
                "verify/load_reading",
                load=f"{detail['load_per_cpu']:.2f}",
                limit=f"{t.load_per_cpu:g}",
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


async def _one_check(
    deps: GraphDeps,
    host_id: str,
    plan: _Plan,
    redeploys: list[_Redeploy],
    first_running: dict[str, ContainerSummary],
    attempt: int,
    calm: set[str],
) -> VerificationResult:
    owned = deps.http is None
    client = deps.http or httpx.AsyncClient()
    try:
        checks = [await _check_application(deps, host_id, r) for r in redeploys]
        checks += [
            await _check_container(deps, host_id, name, first_running) for name in plan.containers
        ]
        checks += [await _check_stopped(deps, host_id, name) for name in plan.stopped]
        if plan.unproven:
            checks += [_check("symptom", name, False, _UNPROVEN) for name in plan.stopped]
        checks += [await _check_url(client, cfg) for cfg in plan.urls]
        if plan.readings:
            checks += await _check_host(deps, host_id, plan.readings, calm)
        if plan.blind:
            checks.append(_check("symptom", host_id, False, _NOT_REREAD))
    finally:
        if owned:
            await client.aclose()
    return VerificationResult(ok=all(c.ok for c in checks), checks=checks, attempt=attempt)


async def check_look(deps: GraphDeps, state: IncidentState) -> VerificationResult:
    """One check of a look's service, for a look on request that ended with no action (#421):
    at once, with no wait, no second check and no model call, by Verify's own checks.

    Every container of the service is checked, never one the plan names instead: Bleep's own
    are left out, and so is a job that finished as its service allows (`_finished_job`). Each
    is judged against Gather's reading of it, so one that was not running then, or was
    restarted or started again since (a crash-looper caught while it is up), fails as one
    Verify first sees running does. The URLs are the service's from the hosts file, plus those
    of the plan that are among the host's health checks; a model's URL is never requested.
    Nothing to check is no pass."""
    subject, proposal = state["subject"], state.get("proposal")
    service = deps.registry.service(subject.service_id) if subject.service_id else None
    if service is None:
        return VerificationResult(ok=False)
    host_id = subject.host_id
    containers = [
        c.name
        for c in service.containers
        if not is_self(c.labels) and _finished_job(deps, host_id, c) is None
    ]
    known = host_urls(deps.registry, host_id)
    planned = [known[u] for u in proposal.verification.urls if u in known] if proposal else []
    urls = {_url_key(cfg.url): cfg for cfg in [*service.urls, *planned]}
    plan = _Plan(containers, list(urls.values()), [])
    if plan.empty():
        return VerificationResult(ok=False)
    context = state.get("context")
    gathered = context.containers if context is not None else []
    first_running = {c.name: c for c in gathered if c.state == "running"}
    result = await _one_check(deps, host_id, plan, [], first_running, 1, set())
    # There is no next check to stay up until: say what the look knows instead.
    checks = [
        c.model_copy(update={"detail": NOT_GATHERED_RUNNING, "shown": _NOT_GATHERED_RUNNING})
        if c.detail == CAME_UP
        else c
        for c in result.checks
    ]
    return result.model_copy(update={"checks": checks})


def _recovery_plan(deps: GraphDeps, subject: Subject, signals: list[Signal]) -> _Plan | None:
    """What a fix would have to pass: every container of the service (Bleep's own and a job
    that finished as its service allows left out) and every container the incident's signals
    name, every URL the hosts file gives the service and every URL the signals saw fail. None
    for a service Bleep no longer knows or one with nothing to check."""
    service = deps.registry.service(subject.service_id) if subject.service_id else None
    if service is None:
        return None
    signalled, failing_urls = _symptoms(signals)
    own = [
        c.name
        for c in service.containers
        if not is_self(c.labels) and _finished_job(deps, subject.host_id, c) is None
    ]
    known = {**host_urls(deps.registry, subject.host_id), **{c.url: c for c in service.urls}}
    by_key = {_url_key(u): cfg for u, cfg in known.items()}
    urls = _unique_urls([*(cfg.url for cfg in service.urls), *failing_urls])
    checks = [by_key.get(_url_key(u)) or UrlCheckConfig(url=u) for u in urls]
    plan = _Plan(list(dict.fromkeys([*own, *signalled])), checks, [])
    return None if plan.empty() else plan


async def recovery_reads(
    deps: GraphDeps, subject: Subject, signals: list[Signal]
) -> dict[str, ContainerSummary]:
    """Verify's first reads for `check_recovery` (#571): each container it checks that runs now,
    read once from the engine, as Verify reads them before its settle wait. No URL is
    requested: nothing is judged yet."""
    plan = _recovery_plan(deps, subject, signals)
    return await _first_reads(deps, subject.host_id, plan.containers) if plan else {}


async def check_recovery(
    deps: GraphDeps,
    subject: Subject,
    signals: list[Signal],
    first_running: dict[str, ContainerSummary],
) -> VerificationResult:
    """One check of a service whose incident waits for a yes, to close it as healthy again on
    its own (#571), by Verify's own checks and publishing nothing: no model call, no event.

    It checks what `_recovery_plan` names against `first_running` (`recovery_reads`, taken when
    the service was first held healthy), as Verify checks against its first reads: a container
    started again since fails, and so does one that was not running then, so a crash-looper
    caught while it is up never passes. Nothing to check is no pass."""
    plan = _recovery_plan(deps, subject, signals)
    if plan is None:
        return VerificationResult(ok=False)
    return await _one_check(deps, subject.host_id, plan, [], dict(first_running), 1, set())


def make(deps: GraphDeps) -> Node:
    async def verify(state: IncidentState) -> dict:
        incident_id = state["incident_id"]
        subject = state["subject"]
        redeploys = await _redeploys(deps, state)
        plan = _plan(deps, state, redeploys)

        result = VerificationResult(ok=False)
        if plan.empty() and not redeploys:
            # Nothing to look at is not a pass, and another look at nothing would not help either.
            await deps.bus.publish(incident_id, "verification", payload(result))
        else:
            first_running = await _first_reads(deps, subject.host_id, plan.containers)
            await _first_app_reads(deps, subject.host_id, redeploys)
            await deps.sleep(SETTLE_S)
            calm: set[str] = set()
            for attempt in range(1, CHECKS + 1):
                result = await _one_check(
                    deps, subject.host_id, plan, redeploys, first_running, attempt, calm
                )
                await deps.bus.publish(incident_id, "verification", payload(result))
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


def payload(result: VerificationResult) -> dict:
    """A check as its `verification` event carries it."""
    return {
        "ok": result.ok,
        "attempt": result.attempt,
        "checks": [c.stored() for c in result.checks],
    }


def route(state: IncidentState) -> Literal["investigate", "report"]:
    """A failed check with loop-backs left goes to Investigate; everything else to Report."""
    verification = state.get("verification")
    if verification is not None and not verification.ok and state.get("outcome") is None:
        return "investigate"
    return "report"
