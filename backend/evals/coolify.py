"""A Coolify API for the evals: a fixture's application served in this process to Bleep's own
Coolify plugin, so its read tools and the `coolify.*` actions run unchanged and nothing leaves
the machine.

A fixture is a YAML file in `evals/fixtures/coolify/`: the one application (as `GET
/api/v1/applications` lists it), its env keys and its deployments (as `GET
/api/v1/deployments/applications/{uuid}` lists them), and the container of the scenario's host
that belongs to it. The shapes are those in `tests/fixtures/coolify/`. The API is an httpx
transport that answers in this process, so no socket is opened. Evals stop after Propose, so
nothing may write: a request that is not a GET is recorded in `ServedCoolify.writes`, answered
with 405, and fails the run.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from bleep.guard.log import GuardLogger
from bleep.hosts.coolify_names import (
    COOLIFY_MANAGED,
    COOLIFY_NAME,
    COOLIFY_TYPE,
    COOLIFY_UUID,
    coolify_slug,
)
from bleep.plugins.coolify.plugin import TOKEN_ENV, URL_ENV, CoolifyPlugin
from bleep.plugins.registry import PluginRegistry
from bleep.schemas.coolify import CoolifyApplication, CoolifyDeployment
from evals.mcp_server import GRANT_SECRET

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "coolify"
# Only the plugin's URL rule reads it: the transport answers every request in this process.
URL = "https://coolify.eval.invalid"
TOKEN = "evals-token-not-a-secret"
API = "/api/v1"
DEPLOY_MINUTES = 3  # how long a deployment of the fixture ran


class CoolifyFixtureError(ValueError):
    """A Coolify fixture is missing or malformed."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CoolifyFixture(_Strict):
    container: str  # the scenario's container that runs this application
    application: dict[str, Any]
    env_keys: list[str] = Field(default_factory=list)  # keys only; no value is ever served
    deployments: list[dict[str, Any]] = Field(default_factory=list)  # newest first

    @field_validator("application")
    @classmethod
    def _an_application(cls, value: dict[str, Any]) -> dict[str, Any]:
        app = CoolifyApplication.model_validate(value)
        if not app.name or not coolify_slug(app.name):
            raise ValueError("the application has a name that makes a resource name")
        return value

    @field_validator("deployments")
    @classmethod
    def _deployments(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for deployment in value:
            CoolifyDeployment.model_validate(deployment)
        return value

    @property
    def uuid(self) -> str:
        return str(self.application["uuid"])

    @property
    def name(self) -> str:
        return str(self.application["name"])

    def labels(self) -> dict[str, str]:
        """What Coolify writes onto the application's container, as the plan check and the
        plugin read it; the target of a `coolify.*` step is `name` or `uuid`."""
        return {
            COOLIFY_MANAGED: "true",
            COOLIFY_TYPE: "application",
            COOLIFY_UUID: self.uuid,
            COOLIFY_NAME: coolify_slug(self.name),
            "coolify.pullRequestId": "0",
        }


def load_coolify_fixture(name: str, directory: Path = FIXTURES_DIR) -> CoolifyFixture:
    path = directory / f"{name}.yaml"
    try:
        return CoolifyFixture.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise CoolifyFixtureError(f"Coolify fixture {path}: {error}") from error


class ServedCoolify(httpx.AsyncBaseTransport):
    """A fixture being served: the transport the plugin's client sends its requests through, and
    every request it answered. Like the client's own transport in the plugin's tests it hands the
    answer over unread, as the network does."""

    def __init__(
        self, fixture: CoolifyFixture, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.fixture = fixture
        self.requests: list[httpx.Request] = []
        uuid = fixture.uuid
        envs = [
            {"uuid": f"env-{i}", "key": key, "is_buildtime": False, "is_runtime": True}
            for i, key in enumerate(fixture.env_keys)
        ]
        now = clock()
        deployments = [_dated(d, now) for d in fixture.deployments]
        self._reads: dict[str, Any] = {
            f"{API}/applications": [fixture.application],
            f"{API}/applications/{uuid}/envs": envs,
            f"{API}/deployments/applications/{uuid}": {
                "count": len(deployments),
                "deployments": deployments,
            },
        }

    @property
    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method != "GET"]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method != "GET":
            answer = httpx.Response(405, json={"message": "the evals never change Coolify"})
        elif request.url.path == f"{API}/version":
            answer = httpx.Response(200, text="4.6.0")
        elif request.url.path in self._reads:
            answer = httpx.Response(200, json=self._reads[request.url.path])
        else:
            answer = httpx.Response(404, json={"message": "Not found"})
        body = httpx.ByteStream(b"".join(answer.stream))
        return httpx.Response(answer.status_code, headers=answer.headers, stream=body)

    def plugins(self, guard_log: GuardLogger | None = None) -> PluginRegistry:
        """Bleep's Coolify plugin on this API, switched on."""
        env = {URL_ENV: URL, TOKEN_ENV: TOKEN}
        registry = PluginRegistry([CoolifyPlugin(self)], GRANT_SECRET, guard_log=guard_log, env=env)
        registry.set("coolify", {}, True)
        return registry


def _dated(deployment: dict[str, Any], now: datetime) -> dict[str, Any]:
    """The deployment as Coolify lists it: `minutes_ago` (when it started; it ran for
    `DEPLOY_MINUTES`) turned into its `created_at` and `finished_at`."""
    given = dict(deployment)
    started = now - timedelta(minutes=given.pop("minutes_ago", 0))
    stamp = "%Y-%m-%dT%H:%M:%S.000000Z"
    given.setdefault("created_at", started.strftime(stamp))
    given.setdefault("finished_at", (started + timedelta(minutes=DEPLOY_MINUTES)).strftime(stamp))
    return given


def serve(
    fixture: CoolifyFixture, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> ServedCoolify:
    """`fixture` as an API that reads answer and writes are refused by; `clock` dates its
    deployments."""
    return ServedCoolify(fixture, clock)
