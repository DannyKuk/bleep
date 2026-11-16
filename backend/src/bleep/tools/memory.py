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
from bleep.tools.common import invalid_arguments, say, untrusted_rule
from bleep.tools.registry import ToolContext, ToolSpec

RECALL_K = 5


class Recall(Protocol):
    async def similar(
        self, text: str, service_id: str | None = None, k: int = 3, min_score: float | None = None
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
                return say(ctx, "tools/reply/no_past_incident")
            lines = "\n".join(f"- {describe_past(p)}" for p in found)
            return say(ctx, "tools/reply/past_incidents", incidents=lines)

        return StructuredTool(
            name="recall_incidents",
            description=say(
                ctx, "tools/recall_incidents", k=str(RECALL_K), untrusted_rule=untrusted_rule(ctx)
            ),
            args_schema=RecallArgs,
            coroutine=call,
            handle_validation_error=invalid_arguments(ctx),
        )

    return [ToolSpec("recall_incidents", "read", "memory", build)]
