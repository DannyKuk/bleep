"""Diagnose: a structured diagnosis of what was found.

Placeholder until #18: it always answers "unsure", and goes on to Propose.
"""

from typing import Literal

from bleep.graph.deps import GraphDeps
from bleep.graph.state import IncidentState, Node
from bleep.schemas.graph import Diagnosis


def make(deps: GraphDeps) -> Node:
    async def diagnose(state: IncidentState) -> dict:
        diagnosis = Diagnosis(
            root_cause="I could not form a diagnosis",
            category="unknown",
            confidence=0.0,
            unsure=True,
        )
        return {"diagnosis": diagnosis}

    return diagnose


def route(state: IncidentState) -> Literal["investigate", "propose"]:
    """Low confidence after the first round goes back to Investigate once (#18)."""
    return "propose"
