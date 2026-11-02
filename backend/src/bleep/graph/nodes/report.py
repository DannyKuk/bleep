"""Report: close the run with its outcome.

Placeholder until #27: it sets `outcome` only; the written report and memory come then.
"""

from bleep.graph.deps import GraphDeps
from bleep.graph.state import IncidentState, Node
from bleep.schemas.graph import Outcome


def outcome(state: IncidentState) -> Outcome:
    """An outcome set earlier (a spent budget) stands; otherwise it follows the decision."""
    if state.get("outcome"):
        return state["outcome"]  # type: ignore[return-value]
    decision = state.get("decision")
    proposal = state.get("proposal")
    if decision is not None and decision.kind == "reject":
        return "rejected"
    if proposal is None or proposal.kind == "no_action":
        return "no_action"
    return "unverified"  # approved, but nothing ran or was checked: never claim "fixed"


def make(deps: GraphDeps) -> Node:
    async def report(state: IncidentState) -> dict:
        return {"outcome": outcome(state)}

    return report
