"""The incident graph: its nodes, the order they run in, and its checkpoint store.

Each node lives in `graph/nodes/<name>.py` with its own `make(deps)` and, where the next step
depends on the state, its own `route(state)`. This module only wires them, so changing a node
never touches another. See docs/design/graph.md.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
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


def _traced(bus: EventBus, name: str, node: Node) -> Node:
    """Publish `node_started` and `node_finished` around the node, for the live view.

    A node that stops on `interrupt()` publishes no `node_finished`; it starts again on resume.
    """

    async def run(state: IncidentState) -> dict:
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
        graph.add_node(name, _traced(deps.bus, name, module.make(deps)))
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
