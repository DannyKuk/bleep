"""The data the graph nodes hand the model, described one way in every node.

The words are prompt parts under `prompts/graph/parts/`; Python only fills them and joins the
rows, one a line and a list's items each after "- " (#623). A part is no prompt of its own, so the
dev panel cannot override it.
"""

from bleep import prompts
from bleep.graph.host_text import gib
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.graph import Diagnosis, GatherContext


def part(name: str, /, **values: str) -> str:
    """The part `graph/parts/<name>` filled with `values`, without the file's closing newline."""
    return prompts.load(f"graph/parts/{name}", **values).removesuffix("\n")


def bullets(lines: list[str]) -> str:
    """Each line after "- ", one a line; the `none` part when there are none."""
    return "\n".join(f"- {line}" for line in lines) or part("none")


def diagnosis(found: Diagnosis | None) -> str:
    """The root cause, its category and confidence, the services it hurts and each piece of
    evidence; the `none` part without a diagnosis."""
    if found is None:
        return part("none")
    lines = [
        part(
            "diagnosis",
            root_cause=found.root_cause,
            category=found.category,
            confidence=f"{found.confidence:.2f}",
        )
    ]
    if found.affected_services:
        lines.append(part("diagnosis_affected", services=", ".join(found.affected_services)))
    lines += [f"- {e.source}: {e.detail}" for e in found.evidence]
    return "\n".join(lines)


def container(c: ContainerSummary) -> str:
    state = c.state
    if c.exit_code is not None:
        state = part("container_exited", state=c.state, code=str(c.exit_code))
    return part(
        "container",
        name=c.name,
        state=state,
        health=c.health,
        restarts=str(c.restart_count),
        image=c.image,
    )


def containers(context: GatherContext | None) -> str:
    """Gather's containers, one a line; the `none` part when it found none."""
    return bullets([container(c) for c in context.containers] if context else [])


def top_memory(context: GatherContext | None) -> str:
    """The containers using the most memory Gather measured, under their heading; empty when it
    measured none."""
    if context is None or not context.top_memory:
        return ""
    lines = []
    for m in context.top_memory:
        values = {"name": m.name, "used": gib(m.mem_usage), "percent": f"{m.mem_percent:.0f}%"}
        if m.mem_limit:
            lines.append(part("memory_use_of", **values, limit=gib(m.mem_limit)))
        else:
            lines.append(part("memory_use", **values))
    return part("top_memory", containers=bullets(lines))


def step(action: str, target: str, host: str, params: str = "") -> str:
    """One plan step, its params after it in brackets when it has any."""
    text = part("step", action=action, target=target, host=host)
    return f"{text} ({params})" if params else text
