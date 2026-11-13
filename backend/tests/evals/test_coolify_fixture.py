"""The evals' Coolify API: a fixture's application served in this process to Bleep's own Coolify
plugin. Reads answer, and anything that would change Coolify is refused and recorded."""

from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr

from bleep.hosts.config import HostConfig
from bleep.hosts.registry import Registry
from bleep.plugins.coolify.client import CoolifyClient, CoolifyError
from bleep.tools.default import default_registry
from bleep.tools.registry import ToolContext
from evals.coolify import (
    FIXTURES_DIR,
    TOKEN,
    URL,
    CoolifyFixtureError,
    load_coolify_fixture,
    serve,
)
from evals.scripted_host import ScriptedContainer, ScriptedEngine
from graph_env import RecordingGuardLog

UUID = "store0uuid0000000000000a1"


def test_every_shipped_fixture_loads():
    names = sorted(p.stem for p in FIXTURES_DIR.glob("*.yaml"))
    assert names, f"no fixture in {FIXTURES_DIR}"
    for name in names:
        assert load_coolify_fixture(name).deployments


def test_the_fixture_labels_its_container_as_coolify_labels_an_applications():
    fixture = load_coolify_fixture("storefront")
    assert fixture.labels() == {
        "coolify.managed": "true",
        "coolify.type": "application",
        "coolify.applicationUuid": UUID,
        "coolify.resourceName": "storefront",
        "coolify.pullRequestId": "0",
    }


def write(directory: Path, body: dict) -> None:
    (directory / "bad.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ({"container": "c", "application": {"name": "x"}}, "uuid"),
        ({"container": "c", "application": {"uuid": UUID}}, "name"),
        ({"container": "c", "application": {"uuid": UUID, "name": "!!"}}, "resource name"),
        ({"container": "c", "application": {"uuid": UUID, "name": "x"}, "deployments": [{}]}, "x"),
        ({"container": "c", "application": {"uuid": UUID, "name": "x"}, "extra": 1}, "extra"),
        ({"application": {"uuid": UUID, "name": "x"}}, "container"),
    ],
)
def test_a_malformed_fixture_is_refused_with_its_file_named(tmp_path, body, why):
    write(tmp_path, body)
    with pytest.raises(CoolifyFixtureError, match="bad.yaml") as refused:
        load_coolify_fixture("bad", tmp_path)
    assert why in str(refused.value)


def test_a_missing_fixture_is_refused(tmp_path):
    with pytest.raises(CoolifyFixtureError, match="nope.yaml"):
        load_coolify_fixture("nope", tmp_path)


def scripted_registry(labels: dict[str, str]) -> Registry:
    box = ScriptedContainer(name="storefront-web", labels=labels, state="restarting")
    engine = ScriptedEngine("demo", "storefront", [box])

    async def connect(_host: HostConfig):
        return None, engine

    host = HostConfig(id="demo", name="d", kind="socket", endpoint="tcp://scripted:2375")
    return Registry([host], connect)


async def test_the_plugin_reads_the_application_its_env_keys_and_deployments_from_the_server():
    served = serve(load_coolify_fixture("storefront"))
    registry = scripted_registry(served.fixture.labels())
    await registry.start()
    plugins = served.plugins(RecordingGuardLog())
    ctx = ToolContext(incident_id="inc_1", host_id="demo", registry=registry)
    tools = {t.name: t for t in default_registry(plugins=plugins).read_tools(ctx)}
    apps = await tools["coolify_applications"].ainvoke({})
    keys = await tools["coolify_env_keys"].ainvoke({"app": "storefront"})
    deploys = await tools["coolify_deployments"].ainvoke({"app": "storefront"})
    await registry.close()

    assert plugins.actions() == {"coolify.redeploy", "coolify.set_env"}
    assert "storefront" in apps and UUID in apps
    assert all(key in keys for key in ("DATABASE_URL", "NODE_ENV", "PORT"))
    assert "SESSION_TTL_S" not in keys
    assert "Move session handling into its own module" in deploys
    assert served.writes == []


async def test_a_request_that_would_change_coolify_is_refused_and_recorded():
    served = serve(load_coolify_fixture("storefront"))
    client = CoolifyClient(URL, SecretStr(TOKEN), transport=served)

    assert [a.uuid for a in await client.applications()] == [UUID]
    assert served.writes == []
    with pytest.raises(CoolifyError):
        await client.set_env(UUID, "SESSION_TTL_S", SecretStr("3600"))
    with pytest.raises(CoolifyError):
        await client.deploy(UUID)

    assert [(r.method, r.url.path) for r in served.writes] == [
        ("PATCH", f"/api/v1/applications/{UUID}/envs"),
        ("POST", "/api/v1/deploy"),
    ]
