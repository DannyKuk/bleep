"""Propose: a short plan of allowlisted actions, or "no action, informing you".

Placeholder until #19: it always proposes no action.
"""

from typing import Literal

from bleep.graph.deps import GraphDeps
from bleep.graph.state import IncidentState, Node
from bleep.schemas.graph import Proposal


def make(deps: GraphDeps) -> Node:
    async def propose(state: IncidentState) -> dict:
        proposal = Proposal(kind="no_action", reason="Bleep has no proposal step yet.")
        return {"proposal": proposal}

    return propose


def route(state: IncidentState) -> Literal["approval", "report"]:
    """A plan that acts needs a human's yes; "no action" goes straight to the report."""
    proposal = state.get("proposal")
    return "approval" if proposal is not None and proposal.kind == "act" else "report"
