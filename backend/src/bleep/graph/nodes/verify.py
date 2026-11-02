"""Verify: prove the fix worked, or say plainly that it did not.

After Execute the node waits 20 s, then checks up to 3 times, 10 s apart, and stops at the first
check where everything passes. A check passes only when every planned container is running and
healthy (or running with no healthcheck) and every planned URL passes `check_url`. The plan is
the proposal's verification plan; when that is empty it falls back to the affected service's
containers and URLs, and then to the containers the plan's steps acted on.

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
from bleep.hosts.config import UrlCheckConfig
from bleep.hosts.engine import ContainerNotFound, EngineUnavailable
from bleep.schemas.graph import Check, Proposal, VerificationResult
from bleep.schemas.incidents import Subject
from bleep.watcher.http_checks import check_url

log = logging.getLogger(__name__)

SETTLE_S = 20  # before the first check, for the service to come up
RECHECK_S = 10  # between checks
CHECKS = 3  # at most, per round


def _plan(
    deps: GraphDeps, subject: Subject, proposal: Proposal | None
) -> tuple[list[str], list[UrlCheckConfig]]:
    """The containers and URLs to check, from the plan or else from what it acted on."""
    planned = proposal.verification if proposal is not None else None
    service = deps.registry.service(subject.service_id) if subject.service_id else None
    known = {cfg.url: cfg for cfg in service.urls} if service is not None else {}
    containers = list(planned.containers) if planned is not None else []
    urls = [known.get(u) or UrlCheckConfig(url=u) for u in (planned.urls if planned else [])]
    if containers or urls:
        return containers, urls
    if service is not None:
        names = [c.name for c in service.containers if c.name not in service.protected]
        return names, list(service.urls)
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


async def _check_url(client: httpx.AsyncClient, cfg: UrlCheckConfig) -> Check:
    result = await check_url(client, cfg)
    if result.ok:
        return Check(kind="url", target=cfg.url, ok=True, detail=f"answering {result.status}")
    return Check(kind="url", target=cfg.url, ok=False, detail=result.error or "failed")


def _describe(result: VerificationResult) -> str:
    return "\n".join(f"- {c.kind} {c.target}: {c.detail}" for c in result.checks if not c.ok)


def make(deps: GraphDeps) -> Node:
    async def one_check(
        host_id: str, containers: list[str], urls: list[UrlCheckConfig], attempt: int
    ) -> VerificationResult:
        owned = deps.http is None
        client = deps.http or httpx.AsyncClient()
        try:
            checks = [await _check_container(deps, host_id, name) for name in containers]
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

        result = VerificationResult(ok=False)
        if not containers and not urls:
            # Nothing to look at is not a pass, and another look at nothing would not help either.
            result = VerificationResult(ok=False, checks=[])
            await deps.bus.publish(incident_id, "verification", _payload(result))
        else:
            await deps.sleep(SETTLE_S)
            for attempt in range(1, CHECKS + 1):
                result = await one_check(subject.host_id, containers, urls, attempt)
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
