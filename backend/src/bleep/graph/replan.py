"""Going back to Propose from Approval or Execute, at most `Budgets.replans` times in one run.

An instruction (also one after a refused plan), a target Approval cannot read and a plan Execute
finds stale each send the run back to Propose for a new plan. Each counts as one re-plan; one
more than the budget ends the run with the outcome `replan_limit` instead, so no loop between a
plan and the world (or the human) can go on forever. See docs/design/graph.md.
"""

from bleep.graph.deps import GraphDeps
from bleep.graph.shown import shown_payload
from bleep.graph.state import IncidentState
from bleep.schemas.shown import Shown

PLANNING_AGAIN = Shown.of("notice/planning_again")


def replans_left(deps: GraphDeps, state: IncidentState) -> bool:
    """Whether one more trip back to Propose is still inside the budget."""
    return state.get("replans", 0) + 1 <= deps.settings_store.budgets().replans


async def replan(deps: GraphDeps, state: IncidentState, notice: Shown | None = None) -> dict:
    """The state update for one more trip back to Propose, or for the end of the run.

    `notice`, when given, is published with "Planning again." after it; when the budget is spent
    the stop is published instead (after `notice`, if any) and the update sets the outcome.
    """
    count = state.get("replans", 0) + 1
    limit = deps.settings_store.budgets().replans
    incident_id = state["incident_id"]
    if count > limit:
        stopped = Shown.of("notice/stopped", limit=str(limit))
        text = notice.then(stopped) if notice else stopped
        await deps.bus.publish(incident_id, "notice", shown_payload(text))
        return {"replans": count, "outcome": "replan_limit"}
    if notice:
        await deps.bus.publish(incident_id, "notice", shown_payload(notice.then(PLANNING_AGAIN)))
    return {"replans": count}
