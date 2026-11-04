"""Bounds every model call the graph makes: a timeout, and retries for failures worth retrying.

A provider that hangs or is briefly down must not hold an incident for an hour, and must not fail
it either: after the retries the call raises `ModelUnavailable`, which the runner turns into a
paused incident that goes on from its checkpoint later. Errors that retrying cannot fix (a
rejected request, a bad key, a reply that does not validate) pass through untouched. See
docs/design/graph.md and docs/design/incidents.md.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx
import openai
from openrouter import errors as openrouter_errors

log = logging.getLogger(__name__)

# Timeouts, connection failures, rate limits and 5xx answers, from the layers a model call can
# fail in: asyncio's own timeout, httpx, the openai SDK (ChatOpenAI) and the openrouter SDK
# (ChatOpenRouter, which raises its own types).
TRANSIENT = (
    TimeoutError,
    httpx.TransportError,
    openai.APIConnectionError,  # includes APITimeoutError
    openai.RateLimitError,
    openai.InternalServerError,
    openrouter_errors.NoResponseError,
    openrouter_errors.RequestTimeoutResponseError,
    openrouter_errors.EdgeNetworkTimeoutResponseError,
    openrouter_errors.TooManyRequestsResponseError,
    openrouter_errors.InternalServerResponseError,
    openrouter_errors.BadGatewayResponseError,
    openrouter_errors.ServiceUnavailableResponseError,
    openrouter_errors.ProviderOverloadedResponseError,
)


class ModelUnavailable(Exception):
    """The model did not answer within the timeout, after every retry. Names the error type only:
    a provider's message may quote its endpoint or key."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"the model is unavailable ({reason})")
        self.reason = reason


@dataclass(frozen=True)
class ModelGuard:
    """`timeout_s` bounds each attempt; `retries` more attempts follow a transient failure, after
    `backoff_s`, then twice that, and so on. Tests pass a `sleep` that only records the delay."""

    timeout_s: float
    retries: int = 2
    backoff_s: float = 2.0
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep, compare=False)

    async def call[T](self, attempt: Callable[[], Awaitable[T]]) -> T:
        """Run `attempt` (a fresh call each time) under the timeout, retrying transient errors."""
        for tries in range(self.retries + 1):
            try:
                async with asyncio.timeout(self.timeout_s):
                    return await attempt()
            except TRANSIENT as error:
                name = type(error).__name__
                if tries == self.retries:
                    raise ModelUnavailable(name) from error
                delay = self.backoff_s * 2**tries
                log.warning("model call failed (%s); retrying in %.1fs", name, delay)
                await self.sleep(delay)
        raise AssertionError("unreachable")  # the loop returns or raises
