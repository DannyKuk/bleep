"""A small client for the Coolify API (v4, `/api/v1`): applications, their deployments and env
keys, and the two writes Bleep's actions make, a deploy and an env var.

The endpoints and the shapes seen on a real server are in docs/design/plugins.md. The token goes
only into the Authorization header and is never logged; redirects are not followed, so it is
never sent anywhere but `base_url`. Every failure is a `CoolifyError` whose text is Bleep's own,
never Coolify's answer or httpx's message (either can echo the URL or a value).
"""

from typing import Any, Literal

import httpx
from pydantic import SecretStr, TypeAdapter, ValidationError

from bleep.schemas.coolify import CoolifyApplication, CoolifyDeployment, CoolifyEnvKey

TIMEOUT_S = 15.0
DEPLOYMENTS = 5  # the newest ones a read asks for


class CoolifyError(Exception):
    """Coolify did not give what was asked for; the message is Bleep's own."""


class CoolifyUnavailable(CoolifyError):
    """Coolify could not be reached, timed out or answered with a server error."""


class CoolifyNotFound(CoolifyError):
    """No such application, deployment or env var."""


class CoolifyClient:
    """`base_url` is where Coolify answers (`http://host:8000`); the API is under `/api/v1`."""

    def __init__(
        self,
        base_url: str,
        token: SecretStr,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/api/v1"
        self._token = token
        self._transport = transport
        self._timeout_s = timeout_s

    async def version(self) -> str:
        return str(await self._call("GET", "/version", "version", text=True)).strip()

    async def applications(self) -> list[CoolifyApplication]:
        data = await self._call("GET", "/applications", "application list")
        return _parse(list[CoolifyApplication], data)

    async def deployments(self, app_uuid: str, take: int = DEPLOYMENTS) -> list[CoolifyDeployment]:
        """The application's newest deployments, newest first."""
        data = await self._call(
            "GET",
            f"/deployments/applications/{app_uuid}",
            "application",
            params={"take": str(take)},
        )
        # The OpenAPI spec says a list; v4.6.0 answers {count, deployments}.
        listed = data.get("deployments") if isinstance(data, dict) else data
        return _parse(list[CoolifyDeployment], listed)

    async def deployment(self, deployment_uuid: str) -> CoolifyDeployment:
        data = await self._call("GET", f"/deployments/{deployment_uuid}", "deployment")
        return _parse(CoolifyDeployment, data)

    async def env_keys(self, app_uuid: str) -> list[CoolifyEnvKey]:
        """The application's env vars, keys and flags only: the values are never parsed."""
        data = await self._call("GET", f"/applications/{app_uuid}/envs", "application")
        return _parse(list[CoolifyEnvKey], data)

    async def deploy(self, app_uuid: str) -> str:
        """Queue a deployment of the application; its deployment uuid."""
        data = await self._call(
            "POST", "/deploy", "application", params={"uuid": app_uuid, "force": "false"}
        )
        queued = data.get("deployments") if isinstance(data, dict) else None
        found = [d.get("deployment_uuid") for d in queued or [] if isinstance(d, dict)]
        if not found or not isinstance(found[0], str):
            raise CoolifyError("Coolify queued no deployment")
        return found[0]

    async def set_env(
        self, app_uuid: str, key: str, value: SecretStr
    ) -> Literal["updated", "created"]:
        """Set one env var: update it when the application has it, else create it. Coolify's
        answer holds the value, so it is read for its status only."""
        body = {"key": key, "value": value.get_secret_value()}
        try:
            await self._call("PATCH", f"/applications/{app_uuid}/envs", "env var", json=body)
            return "updated"
        except CoolifyNotFound:
            await self._call("POST", f"/applications/{app_uuid}/envs", "application", json=body)
            return "created"

    async def _call(
        self,
        method: str,
        path: str,
        what: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, str] | None = None,
        text: bool = False,
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self._token.get_secret_value()}",
            "Accept": "application/json",
            "User-Agent": "bleep",
        }
        async with httpx.AsyncClient(
            base_url=self._base_url,
            transport=self._transport,
            timeout=self._timeout_s,
            headers=headers,
            follow_redirects=False,
        ) as http:
            try:
                response = await http.request(method, path, params=params, json=json)
            except httpx.HTTPError as error:
                raise CoolifyUnavailable(
                    f"Coolify could not be reached ({type(error).__name__})"
                ) from None
        status = response.status_code
        if 200 <= status < 300:
            if text:
                return response.text[:40]
            try:
                return response.json()
            except ValueError:
                raise CoolifyError(f"Coolify's {what} answer is not JSON") from None
        if status == 404:
            raise CoolifyNotFound(f"Coolify has no such {what}")
        if status in (401, 403):
            raise CoolifyError(f"Coolify refused the token ({status}); check COOLIFY_TOKEN")
        if status == 429:
            raise CoolifyUnavailable("Coolify's rate limit is spent for now")
        if status >= 500:
            raise CoolifyUnavailable(f"Coolify answered {status}")
        if 300 <= status < 400:
            raise CoolifyError(f"Coolify redirected ({status}); set base_url to where it answers")
        raise CoolifyError(f"Coolify refused the {what} request ({status})")


def _parse[T](shape: type[T], data: Any) -> T:
    try:
        return TypeAdapter(shape).validate_python(data)
    except ValidationError:
        raise CoolifyError("Coolify's answer has an unexpected shape") from None
