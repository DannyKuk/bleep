"""The `recall_incidents` read tool: Investigate asks long-term memory about past incidents in its
own words, beyond the three Gather put in the context.

Memory is Bleep's own record, but this tool's answer reaches the model like every tool result:
redacted, capped and wrapped once as untrusted data by Investigate.
"""

from typing import Protocol

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from bleep.memory.text import describe_past
from bleep.schemas.graph import PastIncident
from bleep.tools.docker_read import _invalid
from bleep.tools.registry import ToolContext, ToolSpec

RECALL_K = 5
NOTHING = "No past incident is like this one."

DESCRIPTION = (
    "Search Bleep's records of past incidents on these hosts for ones like a short description "
    "you write (what failed and how, e.g. 'shop-web exits: database connection refused'). "
    f"Returns at most {RECALL_K}, each with its date, cause, what was done, the outcome and what "
    "the human decided. Use it when the similar incidents already in the context do not match "
    "what you found. A past cause is a lead to check, not proof."
)


class Recall(Protocol):
    async def similar(
        self, text: str, service_id: str | None = None, k: int = 3, min_score: float = 0.5
    ) -> list[PastIncident]: ...


class RecallArgs(BaseModel):
    query: str = Field(
        min_length=1, max_length=500, description="What happened, in a sentence or a few words."
    )


def recall_specs(memory: Recall) -> list[ToolSpec]:
    """The memory tools, bound to the memory they read."""

    def build(ctx: ToolContext) -> StructuredTool:
        async def call(query: str) -> str:
            found = await memory.similar(query, k=RECALL_K)
            if not found:
                return NOTHING
            lines = "\n".join(f"- {describe_past(p)}" for p in found)
            return f"Past incidents like this one (Bleep's own records):\n{lines}"

        return StructuredTool(
            name="recall_incidents",
            description=DESCRIPTION,
            args_schema=RecallArgs,
            coroutine=call,
            handle_validation_error=_invalid,
        )

    return [ToolSpec("recall_incidents", "read", "memory", build)]
