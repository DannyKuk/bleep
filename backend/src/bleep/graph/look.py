"""A look on request (#403) in the graph's prompts: the person asked Bleep to look at a service,
and nothing Bleep watches had failed. The prompts are written for something that broke, so a run
the look alone opened adds a rule of its own to them (#410); every other run's prompts stay as
they are."""

from bleep import prompts
from bleep.schemas.incidents import LOOK_REQUESTED, Signal


def bare_look(signals: list[Signal]) -> bool:
    """Whether the person's look is all that opened the run: no signal the watcher saw is in it."""
    return bool(signals) and all(s.kind == LOOK_REQUESTED for s in signals)


def look_rule(node: str, signals: list[Signal]) -> str:
    """The node's rule for a bare look (`prompts/graph/look_<node>.md`), set apart from the text
    before it; empty for any other run, so its prompt is unchanged."""
    if not bare_look(signals):
        return ""
    return "\n\n" + prompts.load(f"graph/look_{node}").strip()
