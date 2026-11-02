"""Investigate: the model looks around with read-only tools, within a tool-call budget.

Placeholder until #17: it only counts the round, so the graph runs end to end.
"""

from typing import Literal

from bleep.graph.deps import GraphDeps
from bleep.graph.state import IncidentState, Node


def make(deps: GraphDeps) -> Node:
    async def investigate(state: IncidentState) -> dict:
        return {"investigate_rounds": state.get("investigate_rounds", 0) + 1}

    return investigate


def route(state: IncidentState) -> Literal["diagnose", "report"]:
    """A spent budget ends the run with a report; otherwise diagnose what was found."""
    return "report" if state.get("outcome") == "budget" else "diagnose"
