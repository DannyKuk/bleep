"""Where host text tried to give Bleep orders, and the one sentence that tells the human (#105).

Only code's own signs are told, in the host text the run read (Gather's logs, the causes of the
past incidents it recalled, and every tool's result, named by that tool's source): a wrapper tag
the guard escaped, or a line that gives orders by `guard.orders` (#271). The diagnosis's
`planted_in`, the model's reading, is kept only for places the run read (`read_sources`) and
measured by the injection eval, but never shown: on 2026-11-05 it flagged 8 of 18 honest control
runs (docs/evals/injection-2026-11-05.md). The sentence is Bleep's own, built from the closed
list, and quotes nothing.
"""

import re

from langchain_core.messages import AnyMessage, ToolMessage

from bleep.graph.nodes.investigate import ESCAPED_TAG
from bleep.guard.orders import gives_orders, line_signs, orders
from bleep.schemas.graph import GatherContext, PlantedPlace, PlantedSource
from bleep.tools.knowledge import SOURCE_LABEL

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
# The evidence line's end. Propose and Report read the diagnosis's evidence in their prompts, so
# this wording is fixed; the report's follow-up reaches only the human and says it is a hint.
PLANTED_END = "that tried to give it orders and ignored it; check who can write there."
HINT_END = (
    "that looks like orders to it and ignored it; check who can write there. It looks only for "
    "some signs of planted orders, so a report without this note does not mean none were planted."
)
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
_OWN_SENTENCE = re.compile(
    rf"(?:bleep:\s{{0,4}})?{re.escape(PLANTED_START)}in the [^;\n]{{1,300}}?"
    rf"(?:{re.escape(PLANTED_END)}|{re.escape(HINT_END)})",
    re.IGNORECASE,
)
# The head of each hit in a knowledge search result (`tools.knowledge.describe_hits`).
_HIT = re.compile(r"\[\d+\] (" + "|".join(map(re.escape, SOURCE_LABEL.values())) + r"): ")


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


def without_own_sentences(text: str) -> str:
    """A record of Bleep's with its own planted-orders sentences taken out: rendered as an
    evidence line ("bleep: Bleep found text ... write there.") it would read as an order."""
    return _OWN_SENTENCE.sub(" ", text)


def _record_gives_orders(text: str) -> bool:
    return gives_orders(without_own_sentences(text), own_record=True)


def _knowledge_gives_orders(text: str) -> bool:
    """Whether a line of a knowledge search result gives orders; the hits from Bleep's past
    incidents are read as its own records, the docs as host text."""
    own = False
    for line in text.splitlines():
        if hit := _HIT.match(line):
            own = hit[1] == SOURCE_LABEL["incident"]
        read = without_own_sentences(line) if own else line
        if orders(line_signs(read), own_record=own):
            return True
    return False


def _result_gives_orders(m: ToolMessage) -> bool:
    if m.name == "knowledge_search":
        return _knowledge_gives_orders(m.text)
    if m.name == "recall_incidents":
        return _record_gives_orders(m.text)
    return gives_orders(m.text)


def ordered_places(context: GatherContext | None, messages: list[AnyMessage]) -> set[PlantedPlace]:
    """The places where a line gives orders by the signs of `guard.orders`. Past incidents are
    Bleep's own records, which say who approved what, so they are read as such."""
    found: set[PlantedPlace] = set()
    if context is not None and any(gives_orders(t) for t in context.logs.values()):
        found.add("logs")
    if context is not None and any(_record_gives_orders(past.cause) for past in context.similar):
        found.add("memory")
    for m in _tool_results(messages):
        if _result_gives_orders(m):
            found.add(TOOL_SOURCE.get(m.name or "", "other"))
    return found


def planted_places(context: GatherContext | None, messages: list[AnyMessage]) -> list[PlantedPlace]:
    """The places where code saw a wrapper tag the guard escaped or a line that gives orders, in
    the closed list's order."""
    found = escaped_places(context, messages) | ordered_places(context, messages)
    return [place for place in PLANTED_WHERE if place in found]


def planted_sentence(places: list[PlantedPlace], end: str = PLANTED_END) -> str | None:
    """The fixed sentence that names the places, or None when there are none; `HINT_END` gives
    the report's follow-up."""
    where = [PLANTED_WHERE[place] for place in places]
    if not where:
        return None
    named = where[0] if len(where) == 1 else f"{', '.join(where[:-1])} and {where[-1]}"
    return f"{PLANTED_START}in the {named} {end}"
