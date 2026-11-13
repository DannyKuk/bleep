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
# (128,000), far above what Bleep's short replies need and enough for a runaway reply to run up
# the bill. Thinking tokens count against it.
ANTHROPIC_MAX_TOKENS = 16_000
# Top-level automatic caching: the API puts the breakpoint on the last cacheable block of each
# request, so it moves forward as an incident's messages grow, and the tools, the system prompt
# and every earlier message are read back from the cache (5-minute entries, each read refreshes).
# A cache write costs 25% more than plain input, so `chat_model()` leaves it off and only a call
# that sends the same prefix again turns it on through `cached()`: the investigate loop (#546).
ANTHROPIC_CACHE = {"cache_control": {"type": "ephemeral"}}

Role = Literal["main", "cheap"]
# The key of a model's `metadata` that names its provider: LangChain hands the metadata to every
# callback of the model's calls, so the usage ledger can record where a call went (#547). The
# client's own `ls_provider` cannot say it: a local server is a `ChatOpenAI` too.
PROVIDER_METADATA = "bleep_provider"


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
            metadata={PROVIDER_METADATA: "local"},
            **_bounds(timeout_s, 1),
        )
    settings = settings.resolved()
    name = settings.model if role == "main" else settings.cheap_model
    if settings.provider == "anthropic":
        # No temperature: Claude Sonnet 5.5 rejects any value but its default (langchain-anthropic
        # raises on it), and a model that takes one is better left at its own.
        key = {"api_key": settings.anthropic_api_key} if settings.anthropic_api_key else {}
        return ChatAnthropic(
            model=name,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            metadata={PROVIDER_METADATA: "anthropic"},
            **key,
            **_bounds(timeout_s, 1, "timeout"),
        )
    key = {"api_key": settings.openrouter_api_key} if settings.openrouter_api_key else {}
    return ChatOpenRouter(
        model=name,
        temperature=settings.temperature,
        metadata={PROVIDER_METADATA: "openrouter"},
        **key,
        **_bounds(timeout_s, 1000),
    )


def cached(model: BaseChatModel) -> BaseChatModel:
    """The same model with prompt caching on, for a call that re-sends its prefix. Only the
    investigate loop does (#546); a model of another provider comes back as it is."""
    if not isinstance(model, ChatAnthropic):
        return model
    return model.model_copy(update={"model_kwargs": {**model.model_kwargs, **ANTHROPIC_CACHE}})


def _bounds(
    timeout_s: int | None, per_second: int, timeout_arg: str = "request_timeout"
) -> dict[str, int]:
    if timeout_s is None:
        return {}
    return {timeout_arg: (timeout_s + CLIENT_MARGIN_S) * per_second, "max_retries": 0}
