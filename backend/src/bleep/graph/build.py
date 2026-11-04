"""The incident graph: its nodes, the order they run in, and its checkpoint store.

Each node lives in `graph/nodes/<name>.py` with its own `make(deps)` and, where the next step
depends on the state, its own `route(state)`. This module only wires them, so changing a node
never touches another. See docs/design/graph.md.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from bleep.graph.deps import GraphDeps
from bleep.graph.nodes import (
    approval,
    diagnose,
    execute,
    gather,
    investigate,
    propose,
    report,
    verify,
)
from bleep.graph.state import IncidentState, Node, checkpoint_serializer
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo
from bleep.schemas.incidents import IncidentStatus

# The status an incident shows while a node runs. Approval and Report are left out: the runner
# sets `awaiting_approval` when the run stops at the interrupt and settles the end status itself.
NODE_STATUS: dict[str, IncidentStatus] = {
    "gather": "investigating",
    "investigate": "investigating",
    "diagnose": "investigating",
    "propose": "investigating",
    "execute": "executing",
    "verify": "verifying",
}


def _traced(bus: EventBus, incidents: IncidentRepo, name: str, node: Node) -> Node:
    """Set the incident's status for the node, publish `node_started` and `node_finished` around
    it, for the live view.

    The status is written before `node_started` goes out: the dashboard reads the incident again
    on that event and must find the new status.

    A node that stops on `interrupt()` publishes no `node_finished`; it starts again on resume.
    """

    async def run(state: IncidentState, config: RunnableConfig) -> dict:
        status = NODE_STATUS.get(name)
        if status is not None:
            await incidents.set_status(state["incident_id"], status)
        last = await bus.last_event(state["incident_id"])
        retried = config.get("configurable", {}).get("after_pause", False)
        again = (
            retried
            and last is not None
            and last.kind == "node_started"
            and last.payload["node"] == name
        )
        if not again:  # a node run again after a pause adds nothing to the timeline
            await bus.publish(state["incident_id"], "node_started", {"node": name})
        update = await node(state)
        await bus.publish(state["incident_id"], "node_finished", {"node": name})
        return update

    return run


def build_graph(deps: GraphDeps, checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    """gather → investigate → diagnose → propose → approval → execute → verify → report, with
    the loops back."""
    graph = StateGraph(IncidentState)
    for module in (gather, investigate, diagnose, propose, approval, execute, verify, report):
        name = module.__name__.rsplit(".", 1)[-1]
        graph.add_node(name, _traced(deps.bus, deps.incidents, name, module.make(deps)))
    graph.add_edge(START, "gather")
    graph.add_edge("gather", "investigate")
    graph.add_conditional_edges("investigate", investigate.route, ["diagnose", "report"])
    graph.add_conditional_edges("diagnose", diagnose.route, ["investigate", "propose"])
    graph.add_conditional_edges("propose", propose.route, ["approval", "report"])
    graph.add_conditional_edges("approval", approval.route, ["propose", "execute", "report"])
    graph.add_conditional_edges("execute", execute.route, ["propose", "verify", "report"])
    graph.add_conditional_edges("verify", verify.route, ["investigate", "report"])
    graph.add_edge("report", END)
    return graph.compile(checkpointer=checkpointer)


@asynccontextmanager
async def open_checkpointer(path: Path) -> AsyncIterator[AsyncSqliteSaver]:
    """The checkpoint store (`checkpoints.sqlite`), open for as long as the block runs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as conn:
        saver = AsyncSqliteSaver(conn, serde=checkpoint_serializer())
        await saver.setup()
        yield saver
