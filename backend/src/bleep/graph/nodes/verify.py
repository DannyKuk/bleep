"""Verify: prove the fix worked, or say plainly that it did not.

After Execute the node waits 20 s, then checks up to 3 times, 10 s apart, and stops at the first
check where everything passes. A check passes only when every planned container is running and
healthy (or running with no healthcheck) and every planned URL passes `check_url`. The plan is
the proposal's verification plan; when that is empty it falls back to the affected service's
containers (protected ones too) and URLs, and then to the containers the plan's steps acted on.
Whatever the plan names, the incident's own symptoms are checked too: every container its signals
name and every URL they report failing, so a plan that checks something else cannot pass while
the failure that opened the incident goes on.
A Coolify application the plan redeployed is checked as a whole, read from the engine each time:
the redeploy replaced its containers under new names, so they are left out of the name checks.
The redeploy step ends only once Coolify reports its deployment over (`plugins/coolify`), so the
wait here starts after the new containers are up.

Passing sets the outcome `resolved`. A failure with fewer than `verify_loops` (2) loop-backs so
far hands the failed checks to Investigate as the next round's instruction (an appended message,
as Diagnose's loop-back does); otherwise the outcome is `unverified`, and so it is at once when
there was nothing to check. Nothing here says "fixed": that word needs a passing check. The
clock that waits is `deps.sleep`, so tests never sleep.
Each check publishes a `verification` event; the notifier hears the round's final result once.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import httpx
from langchain_core.messages import HumanMessage

from bleep import prompts
from bleep.graph.deps import GraphDeps
from bleep.graph.state import IncidentState, Node, plan_results
from bleep.guard.plan_check import COOLIFY_UUID, coolify_members, host_urls
from bleep.hosts.config import UrlCheckConfig
from bleep.hosts.engine import ContainerNotFound, EngineUnavailable
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.graph import Check, Proposal, StepResult, VerificationResult
from bleep.schemas.incidents import Signal
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


UNPROVEN = "stopped as approved; nothing shows the service works (no URL or running container)"


@dataclass(frozen=True)
class _Plan:
    """What to check: containers that must run, URLs that must answer, and signalled containers
    the plan stopped, which must stay down. `unproven` says the incident's own symptom is only
    covered by those stop checks: a container already dead passes one, so it proves nothing."""

    containers: list[str]
    urls: list[UrlCheckConfig]
    stopped: list[str]
    unproven: bool = False

    def empty(self) -> bool:
        return not (self.containers or self.urls or self.stopped)


def _plan(deps: GraphDeps, state: IncidentState) -> _Plan:
    """The containers and URLs to check: the plan's, or else what it acted on, and always the
    incident's own symptoms (`_symptoms`). A signalled container the plan stopped (its last
    Docker step is a stop) is checked to be down, unless it is among the containers that must
    run (the fallback can put it there; that check then fails, the safe side). When those stop
    checks are all there is about the symptom (no URL, no signalled container that must run, no
    redeploy), the plan is `unproven` and cannot pass.

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
    if not containers and not urls:
        if service is not None:
            containers = [c.name for c in service.containers]
            urls = [cfg.url for cfg in service.urls]
        else:
            acted = proposal.steps if proposal is not None else []
            containers = [s.target for s in acted if s.action.startswith("docker.")]
    signalled, failing_urls = _symptoms(state.get("signals", []))
    stopped = _stopped(proposal)
    left_out = [c for c in signalled if c in stopped or replaced(c)]
    if left_out and service is not None:
        # The plan meant that container gone or down; only the service's URLs can say whether
        # that fixed anything.
        failing_urls += [cfg.url for cfg in service.urls]
    named = [*containers, *(c for c in signalled if c not in left_out)]
    containers = [c for c in dict.fromkeys(named) if not replaced(c)]
    urls = list(dict.fromkeys([*urls, *failing_urls]))
    down = [c for c in left_out if c in stopped and c not in containers]
    covered = bool(urls) or bool(set(signalled) & set(containers)) or bool(_redeployed(proposal))
    checks = [known.get(u) or UrlCheckConfig(url=u) for u in urls]
    return _Plan(containers, checks, down, unproven=bool(down) and not covered)


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
    named = [re.compile(rf"(?:^|-){re.escape(u)}-\d+$") for u in uuids]
    return lambda name: name in members or any(p.search(name) for p in named)


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
    refused at run time) has no start and its app no entry."""
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


def _describe(result: VerificationResult) -> str:
    return "\n".join(f"- {c.kind} {c.target}: {c.detail}" for c in result.checks if not c.ok)


def make(deps: GraphDeps) -> Node:
    async def one_check(
        host_id: str,
        plan: _Plan,
        apps: list[str],
        starts: dict[str, datetime],
        attempt: int,
    ) -> VerificationResult:
        owned = deps.http is None
        client = deps.http or httpx.AsyncClient()
        try:
            checks = [await _check_application(deps, host_id, app, starts.get(app)) for app in apps]
            checks += [await _check_container(deps, host_id, name) for name in plan.containers]
            checks += [await _check_stopped(deps, host_id, name) for name in plan.stopped]
            if plan.unproven:
                target = f"{', '.join(plan.stopped)} (the incident's symptom)"
                checks.append(Check(kind="container", target=target, ok=False, detail=UNPROVEN))
            checks += [await _check_url(client, cfg) for cfg in plan.urls]
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
            for attempt in range(1, CHECKS + 1):
                result = await one_check(subject.host_id, plan, apps, starts, attempt)
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
        elif result.checks and loops < deps.settings_store.budgets().verify_loops:
            update["verify_loops"] = loops + 1
            update["messages"] = [
                HumanMessage(
                    prompts.load(
                        "graph/verify_failed",
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
