"""Builds the chat model for a role. Both providers speak the OpenAI chat protocol."""

from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_openrouter import ChatOpenRouter

from bleep.schemas.settings import ModelSettings

CLIENT_MARGIN_S = 5

Role = Literal["main", "cheap"]


def chat_model(settings: ModelSettings, role: Role, timeout_s: int | None = None) -> BaseChatModel:
    """`timeout_s` is the timeout of `llm.guard`; the client's own is `CLIENT_MARGIN_S` longer, so
    the guard cuts a call off first, and retrying is left to the guard (no client retries).
    The units differ: ChatOpenAI takes seconds, ChatOpenRouter milliseconds."""
    if role not in ("main", "cheap"):
        raise ValueError(f"unknown model role {role!r}")
    if settings.provider == "local":
        return ChatOpenAI(
            base_url=settings.local_base_url,
            model=settings.local_model,
            api_key="local",
            temperature=settings.temperature,
            **_bounds(timeout_s, 1),
        )
    name = settings.model if role == "main" else settings.cheap_model
    key = {"api_key": settings.openrouter_api_key} if settings.openrouter_api_key else {}
    return ChatOpenRouter(
        model=name, temperature=settings.temperature, **key, **_bounds(timeout_s, 1000)
    )


def _bounds(timeout_s: int | None, per_second: int) -> dict[str, int]:
    if timeout_s is None:
        return {}
    return {"request_timeout": (timeout_s + CLIENT_MARGIN_S) * per_second, "max_retries": 0}
