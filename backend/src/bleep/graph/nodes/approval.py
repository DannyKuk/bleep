"""Approval: wait for a human's decision on the plan, for as long as it takes.

`interrupt()` checkpoints the run and stops it; `IncidentRunner.resume` continues it with the
`Decision`. A node that interrupts runs again from its first line on resume, so anything it does
before `interrupt()` must be safe to repeat. #20 adds the approval request, the notification and
the grant; this version only waits and routes.
"""

from typing import Literal

from langgraph.types import interrupt

from bleep.graph.deps import GraphDeps
from bleep.graph.state import IncidentState, Node
from bleep.schemas.graph import Decision


def make(deps: GraphDeps) -> Node:
    async def approval(state: IncidentState) -> dict:
        proposal = state.get("proposal")
        asked = {"proposal": proposal.model_dump(mode="json") if proposal else None}
        decision = Decision.model_validate(interrupt(asked))
        update: dict = {"decision": decision}
        if decision.kind == "instruct" and decision.text:
            update["instructions"] = [*state.get("instructions", []), decision.text]
        return update

    return approval


def route(state: IncidentState) -> Literal["propose", "report"]:
    """An instruction goes back to Propose; a no closes with a report.

    A yes goes to the report too until Execute exists (Milestone 3), which reports the plan as
    approved but unverified.
    """
    decision = state.get("decision")
    return "propose" if decision is not None and decision.kind == "instruct" else "report"
