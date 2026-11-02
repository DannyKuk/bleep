"""The state one incident's graph thread carries, and how it is checkpointed."""

import types
import typing
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph.message import add_messages
from pydantic import BaseModel

from bleep.schemas.graph import (
    ApprovalGrant,
    Decision,
    Diagnosis,
    GatherContext,
    Outcome,
    Proposal,
    Report,
    StepResult,
    VerificationResult,
)
from bleep.schemas.incidents import Signal, Subject


class IncidentState(TypedDict, total=False):
    incident_id: str
    subject: Subject
    signals: list[Signal]
    context: GatherContext
    messages: Annotated[list[AnyMessage], add_messages]
    investigate_rounds: int
    tool_calls_used: int
    diagnosis: Diagnosis | None
    proposal: Proposal | None
    instructions: list[str]
    decision: Decision | None
    grant: ApprovalGrant | None
    execution: list[StepResult]
    verify_loops: int
    verification: VerificationResult | None
    outcome: Outcome | None
    report: Report | None


# A node takes the state and returns the keys it changes.
Node = Callable[[IncidentState], Awaitable[dict[str, Any]]]


def checkpoint_types() -> set[type[BaseModel]]:
    """Every pydantic model the state can hold, nested ones included."""
    found: set[type[BaseModel]] = set()
    todo: list[Any] = list(typing.get_type_hints(IncidentState).values())
    while todo:
        hint = todo.pop()
        if typing.get_origin(hint) is Literal:
            continue
        if isinstance(hint, type) and issubclass(hint, BaseModel):
            if hint in found or hint.__module__.startswith("langchain_core"):
                continue
            found.add(hint)
            todo.extend(f.annotation for f in hint.model_fields.values())
        elif isinstance(hint, types.UnionType) or typing.get_args(hint):
            todo.extend(typing.get_args(hint))
    return found


def checkpoint_serializer() -> JsonPlusSerializer:
    """LangGraph's serializer, allowed to rebuild exactly the state's own models.

    Without the list LangGraph rebuilds any class a checkpoint names (and warns that it will stop
    doing so); with it, a checkpoint row cannot make Bleep construct anything else.
    """
    return JsonPlusSerializer(allowed_msgpack_modules=sorted(checkpoint_types(), key=str))
