"""Going back to Propose from Approval or Execute, at most `Budgets.replans` times in one run.

An instruction, a target Approval cannot read and a plan Execute finds stale each send the run
back to Propose for a new plan. Each counts as one re-plan; one more than the budget ends the run
with the outcome `replan_limit` instead, so no loop between a plan and the world (or the human)
can go on forever. See docs/design/graph.md.
"""

from bleep.graph.deps import GraphDeps
from bleep.graph.state import IncidentState

PLANNING_AGAIN = "Planning again."
STOPPED = "Bleep stopped after {limit} new plans in this incident; nothing more will run."


async def replan(deps: GraphDeps, state: IncidentState, notice: str | None = None) -> dict:
    """The state update for one more trip back to Propose, or for the end of the run.

    `notice`, when given, is published with "Planning again." after it; when the budget is spent
    the stop is published instead (after `notice`, if any) and the update sets the outcome.
    """
    count = state.get("replans", 0) + 1
    limit = deps.settings_store.budgets().replans
    incident_id = state["incident_id"]
    if count > limit:
        text = STOPPED.format(limit=limit)
        await deps.bus.publish(
            incident_id, "notice", {"text": f"{notice} {text}" if notice else text}
        )
        return {"replans": count, "outcome": "replan_limit"}
    if notice:
        await deps.bus.publish(incident_id, "notice", {"text": f"{notice} {PLANNING_AGAIN}"})
    return {"replans": count}
