"""Verify: prove the fix worked, or say plainly that it did not.

After Execute the node waits 20 s, then checks up to 3 times, 10 s apart, and stops at the first
check where everything passes. A check passes only when every planned container is running and
healthy (or running with no healthcheck) and every planned URL passes `check_url`. The plan is
the proposal's verification plan; when that is empty it falls back to the affected service's
containers (protected ones too) and URLs, and then to the containers the plan's steps acted on.
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
from typing import Literal

import httpx
from langchain_core.messages import HumanMessage

from bleep import prompts
from bleep.graph.deps import GraphDeps
from bleep.graph.state import IncidentState, Node
from bleep.guard.plan_check import coolify_members, host_urls
from bleep.hosts.config import UrlCheckConfig
from bleep.hosts.engine import ContainerNotFound, EngineUnavailable
from bleep.schemas.graph import Check, Proposal, VerificationResult
from bleep.schemas.incidents import Subject
from bleep.watcher.http_checks import check_url

log = logging.getLogger(__name__)

SETTLE_S = 20  # before the first check, for the service to come up
RECHECK_S = 10  # between checks
CHECKS = 3  # at most, per round


def _redeployed(proposal: Proposal | None) -> list[str]:
    """The Coolify applications the plan redeployed, each checked as a whole."""
    steps = proposal.steps if proposal is not None else []
    return list(dict.fromkeys(s.target for s in steps if s.action == "coolify.redeploy"))


def _plan(
    deps: GraphDeps, subject: Subject, proposal: Proposal | None
) -> tuple[list[str], list[UrlCheckConfig]]:
    """The containers and URLs to check, from the plan or else from what it acted on.

    A planned URL keeps its expected status and body text from the hosts file, whichever of the
    host's services it belongs to (the plan check allows any of them). The fallback checks every
    container of the service, protected ones too: reading one is harmless, and leaving out a
    database that is still down would let a check of the web container alone pass.
    """
    planned = proposal.verification if proposal is not None else None
    service = deps.registry.service(subject.service_id) if subject.service_id else None
    known = host_urls(deps.registry, subject.host_id)
    if service is not None:
        known |= {cfg.url: cfg for cfg in service.urls}
    containers = list(planned.containers) if planned is not None else []
    urls = [known.get(u) or UrlCheckConfig(url=u) for u in (planned.urls if planned else [])]
    if containers or urls:
        return containers, urls
    if service is not None:
        # A redeployed application's containers are replaced; its own check covers them.
        apps = _redeployed(proposal)
        replaced = {c.name for app in apps for c in coolify_members(service.containers, app)}
        return [c.name for c in service.containers if c.name not in replaced], list(service.urls)
    acted = proposal.steps if proposal is not None else []
    return [s.target for s in acted if s.action.startswith("docker.")], []


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


async def _check_application(deps: GraphDeps, host_id: str, app: str) -> Check:
    """A redeployed Coolify application, read from the engine now: at least one container of
    it (previews aside) running, and none unhealthy or still starting."""
    target = f"{app} (Coolify application)"
    try:
        listed = await deps.registry.engine(host_id).containers()
    except EngineUnavailable, KeyError:
        return Check(kind="container", target=target, ok=False, detail="host not reachable")
    running = [
        c
        for c in coolify_members(listed, app)
        if c.state == "running" and c.labels.get("coolify.pullRequestId", "0") in ("", "0")
    ]
    if not running:
        return Check(kind="container", target=target, ok=False, detail="no container running")
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


def _describe(result: VerificationResult) -> str:
    return "\n".join(f"- {c.kind} {c.target}: {c.detail}" for c in result.checks if not c.ok)


def make(deps: GraphDeps) -> Node:
    async def one_check(
        host_id: str,
        containers: list[str],
        urls: list[UrlCheckConfig],
        apps: list[str],
        attempt: int,
    ) -> VerificationResult:
        owned = deps.http is None
        client = deps.http or httpx.AsyncClient()
        try:
            checks = [await _check_application(deps, host_id, app) for app in apps]
            checks += [await _check_container(deps, host_id, name) for name in containers]
            checks += [await _check_url(client, cfg) for cfg in urls]
        finally:
            if owned:
                await client.aclose()
        return VerificationResult(ok=all(c.ok for c in checks), checks=checks, attempt=attempt)

    async def verify(state: IncidentState) -> dict:
        incident_id = state["incident_id"]
        subject = state["subject"]
        proposal = state.get("proposal")
        containers, urls = _plan(deps, subject, proposal)
        apps = _redeployed(proposal)

        result = VerificationResult(ok=False)
        if not containers and not urls and not apps:
            # Nothing to look at is not a pass, and another look at nothing would not help either.
            await deps.bus.publish(incident_id, "verification", _payload(result))
        else:
            await deps.sleep(SETTLE_S)
            for attempt in range(1, CHECKS + 1):
                result = await one_check(subject.host_id, containers, urls, apps, attempt)
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
