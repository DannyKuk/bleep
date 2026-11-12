"""Builds the chat model for a role: OpenRouter and a local server speak the OpenAI chat protocol,
the Anthropic API its own."""

from typing import Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_openrouter import ChatOpenRouter

from bleep.schemas.settings import ModelSettings

CLIENT_MARGIN_S = 5
# Claude takes max_tokens on every call; langchain-anthropic would fill in the model's own limit
# (128,000), a call the SDK refuses to make without streaming. Bleep's replies are short.
ANTHROPIC_MAX_TOKENS = 16_000

Role = Literal["main", "cheap"]


def chat_model(settings: ModelSettings, role: Role, timeout_s: int | None = None) -> BaseChatModel:
    """`timeout_s` is the timeout of `llm.guard`; the client's own is `CLIENT_MARGIN_S` longer, so
    the guard cuts a call off first, and retrying is left to the guard (no client retries).
    The units differ: ChatOpenAI and ChatAnthropic take seconds, ChatOpenRouter milliseconds."""
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
    if settings.provider == "anthropic":
        # No temperature: Claude Sonnet 5.5 rejects any value but its default (langchain-anthropic
        # raises on it), and a model that takes one is better left at its own.
        key = {"api_key": settings.anthropic_api_key} if settings.anthropic_api_key else {}
        return ChatAnthropic(
            model=name, max_tokens=ANTHROPIC_MAX_TOKENS, **key, **_bounds(timeout_s, 1, "timeout")
        )
    key = {"api_key": settings.openrouter_api_key} if settings.openrouter_api_key else {}
    return ChatOpenRouter(
        model=name, temperature=settings.temperature, **key, **_bounds(timeout_s, 1000)
    )


def _bounds(
    timeout_s: int | None, per_second: int, timeout_arg: str = "request_timeout"
) -> dict[str, int]:
    if timeout_s is None:
        return {}
    return {timeout_arg: (timeout_s + CLIENT_MARGIN_S) * per_second, "max_retries": 0}
