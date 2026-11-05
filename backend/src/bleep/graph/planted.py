"""Where host text tried to give Bleep orders, and the one sentence that tells the human (#105).

Two signs count. Code's own: a wrapper tag the guard escaped in Gather's logs or in a tool's
result, named by that tool's source. The model's: the diagnosis's `planted_in`, a closed list,
kept only for places the run actually read, since the model flagged places it never saw (#105).
Neither carries any host or model text: the sentence is Bleep's own, built from the closed list,
and it quotes nothing. The flag changes no route, plan or check; it is only told to the human.
"""

from langchain_core.messages import AnyMessage, ToolMessage

from bleep.graph.nodes.investigate import ESCAPED_TAG
from bleep.schemas.graph import Diagnosis, GatherContext, PlantedPlace, PlantedSource

# Each place in the human's words; the order is the closed list's, which the plugins' places
# extend (Diagnose's `planted_in` keeps to `PlantedSource`).
PLANTED_WHERE: dict[PlantedPlace, str] = {
    "logs": "container logs",
    "events": "Docker events",
    "inspect": "container settings",
    "http_body": "HTTP replies",
    "memory": "past incidents Bleep remembers",
    "commits": "commit messages and diffs of the service's repo",
    "docs": "documents Bleep searched",
    "mcp": "answers of the MCP server",
    "coolify": "answers from Coolify",
    "other": "other host output",
}
PLANTED_START = "Bleep found text "  # the injection eval reads it
PLANTED_END = "that tried to give it orders and ignored it; check who can write there."
EVIDENCE_SOURCE = "bleep"  # the diagnosis evidence line Bleep writes itself
# A tool's output by its place on the closed list; any other tool is `other`.
TOOL_SOURCE: dict[str, PlantedPlace] = {
    "container_logs": "logs",
    "recent_events": "events",
    "inspect_container": "inspect",
    "recall_incidents": "memory",
    "recent_commits": "commits",
    "commit_diff": "commits",
    "knowledge_search": "docs",
    "mcp_tools": "mcp",
    "mcp_call": "mcp",
    "coolify_applications": "coolify",
    "coolify_deployments": "coolify",
    "coolify_env_keys": "coolify",
}
_PLUGIN_PLACES: frozenset[PlantedPlace] = frozenset({"mcp", "coolify"})


def _tool_results(messages: list[AnyMessage]) -> list[ToolMessage]:
    return [m for m in messages if isinstance(m, ToolMessage)]


def read_sources(context: GatherContext | None, messages: list[AnyMessage]) -> set[PlantedSource]:
    """The places on Diagnose's list that the run read host text from. A plugin's answers count
    as `other`, the word the diagnosis has for them."""
    read: set[PlantedSource] = set()
    if context is not None and context.logs:
        read.add("logs")
    if context is not None and context.similar:
        read.add("memory")
    for m in _tool_results(messages):
        place = TOOL_SOURCE.get(m.name or "", "other")
        read.add("other" if place in _PLUGIN_PLACES else place)  # type: ignore[arg-type]
    return read


def escaped_places(context: GatherContext | None, messages: list[AnyMessage]) -> set[PlantedPlace]:
    """The places where code saw a wrapper tag the guard escaped."""
    found: set[PlantedPlace] = set()
    if context is not None and any(ESCAPED_TAG.search(t) for t in context.logs.values()):
        found.add("logs")
    for m in _tool_results(messages):
        if ESCAPED_TAG.search(m.text):
            found.add(TOOL_SOURCE.get(m.name or "", "other"))
    return found


def planted_places(
    context: GatherContext | None, messages: list[AnyMessage], diagnosis: Diagnosis | None
) -> list[PlantedPlace]:
    """Every place where host text tried to give orders, in the closed list's order: what code
    saw, and what the diagnosis flagged in a place the run read."""
    found = escaped_places(context, messages)
    if diagnosis is not None:
        found |= set(diagnosis.planted_in) & read_sources(context, messages)
    return [place for place in PLANTED_WHERE if place in found]


def planted_sentence(places: list[PlantedPlace]) -> str | None:
    """The fixed sentence that names the places, or None when there are none."""
    where = [PLANTED_WHERE[place] for place in places]
    if not where:
        return None
    named = where[0] if len(where) == 1 else f"{', '.join(where[:-1])} and {where[-1]}"
    return f"{PLANTED_START}in the {named} {PLANTED_END}"
