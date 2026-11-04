"""A small read-only client for the Coolify API (v4, `/api/v1`): its version, the applications,
their deployments and their env keys.

The endpoints and the shapes seen on a real server are in docs/design/plugins.md. The token goes
only into the Authorization header and is never logged; redirects are not followed, so it is
never sent anywhere but `base_url`. A request has an overall deadline and an answer a size cap,
so a slow or endless answer cannot hold a tool or the plugins page. Every failure is a
`CoolifyError` whose text is Bleep's own, never Coolify's answer or httpx's message (either can
echo the URL or a value).
"""

import asyncio
import json
from typing import Any

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from bleep.schemas.coolify import CoolifyApplication, CoolifyDeployment, CoolifyEnvKey

TIMEOUT_S = 10.0  # connecting, and between two reads of the answer
DEADLINE_S = 20.0  # one whole request, however the answer trickles in
MAX_BYTES = 2_000_000  # of one answer
DEPLOYMENTS = 5  # the newest ones a read asks for


class CoolifyError(Exception):
    """Coolify did not give what was asked for; the message is Bleep's own."""


class CoolifyUnavailable(CoolifyError):
    """Coolify could not be reached, timed out or answered with a server error."""


class CoolifyNotFound(CoolifyError):
    """No such application."""


class CoolifyClient:
    """`base_url` is where Coolify answers (`https://coolify.example.com`); the API is under
    `/api/v1`."""

    def __init__(
        self,
        base_url: str,
        token: SecretStr,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = TIMEOUT_S,
        deadline_s: float = DEADLINE_S,
        max_bytes: int = MAX_BYTES,
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/api/v1"
        self._token = token
        self._transport = transport
        self._timeout_s = timeout_s
        self._deadline_s = deadline_s
        self._max_bytes = max_bytes

    async def version(self) -> str:
        body = await self._get("/version", "version")
        return body.decode("utf-8", errors="replace")[:40].strip()

    async def applications(self) -> list[CoolifyApplication]:
        return _parse_list(
            CoolifyApplication, _json(await self._get("/applications", "application list"))
        )

    async def deployments(self, app_uuid: str, take: int = DEPLOYMENTS) -> list[CoolifyDeployment]:
        """The application's newest deployments, newest first."""
        body = await self._get(
            f"/deployments/applications/{app_uuid}", "application", params={"take": str(take)}
        )
        data = _json(body)
        # The OpenAPI spec says a list; v4.6.0 answers {count, deployments}.
        listed = data.get("deployments") if isinstance(data, dict) else data
        return _parse_list(CoolifyDeployment, listed)

    async def env_keys(self, app_uuid: str) -> list[CoolifyEnvKey]:
        """The application's env vars, keys and flags only: the values are never parsed."""
        body = await self._get(f"/applications/{app_uuid}/envs", "application")
        return _parse_list(CoolifyEnvKey, _json(body))

    async def _get(self, path: str, what: str, *, params: dict[str, str] | None = None) -> bytes:
        """The body of a 2xx answer, or the `CoolifyError` for any other."""
        try:
            async with asyncio.timeout(self._deadline_s):
                status, body = await self._fetch(path, params)
        except TimeoutError:
            raise CoolifyUnavailable(
                f"Coolify did not answer within {self._deadline_s:g} s"
            ) from None
        except httpx.HTTPError as error:
            raise CoolifyUnavailable(
                f"Coolify could not be reached ({type(error).__name__})"
            ) from None
        if 200 <= status < 300:
            return body
        if status == 404:
            raise CoolifyNotFound(f"Coolify has no such {what}")
        if status in (401, 403):
            raise CoolifyError(f"Coolify refused the token ({status}); check COOLIFY_TOKEN")
        if status == 429:
            raise CoolifyUnavailable("Coolify's rate limit is spent for now")
        if status >= 500:
            raise CoolifyUnavailable(f"Coolify answered {status}")
        if 300 <= status < 400:
            raise CoolifyError(
                f"Coolify redirected ({status}); set COOLIFY_URL to where it answers"
            )
        raise CoolifyError(f"Coolify refused the {what} request ({status})")

    async def _fetch(self, path: str, params: dict[str, str] | None) -> tuple[int, bytes]:
        headers = {
            "Authorization": f"Bearer {self._token.get_secret_value()}",
            "Accept": "application/json",
            "User-Agent": "bleep",
        }
        async with (
            httpx.AsyncClient(
                base_url=self._base_url,
                transport=self._transport,
                timeout=self._timeout_s,
                headers=headers,
                follow_redirects=False,
            ) as http,
            http.stream("GET", path, params=params) as response,
        ):
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > self._max_bytes:
                    raise CoolifyError(
                        f"Coolify's answer is over {self._max_bytes // 1_000_000} MB; Bleep stopped"
                    )
            return response.status_code, bytes(body)


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except ValueError:
        raise CoolifyError("Coolify's answer is not JSON") from None


def _parse_list[T: BaseModel](model: type[T], data: Any) -> list[T]:
    """Every item of `data` that fits `model`; one odd record does not lose the others."""
    if not isinstance(data, list):
        raise CoolifyError("Coolify's answer has an unexpected shape")
    found: list[T] = []
    for item in data:
        try:
            found.append(model.model_validate(item))
        except ValidationError:
            continue
    return found
