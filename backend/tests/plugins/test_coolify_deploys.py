import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from bleep.plugins.coolify.client import CoolifyClient
from bleep.plugins.coolify.deploys import CoolifyDeploys, running_commit
from bleep.plugins.coolify.plugin import CoolifyPlugin
from bleep.plugins.registry import PluginRegistry
from bleep.schemas.coolify import CoolifyDeployment
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.services import Service
from bleep.tools.github import DeployUnknown

FIXTURES = Path(__file__).parent.parent / "fixtures" / "coolify"
BASE = "https://coolify.example.com"
ENV = {"COOLIFY_URL": BASE, "COOLIFY_TOKEN": "cool-root-token-7d1e9f"}
SHOP = "shop0uuid0000000000000a1"
HEAD = "4189b7f051c6c51ce74c3bee1a5f269f9c50c6b2"
BASE_SHA = "80960fa31918d7663c3f4c3ad61661cf0e80628f"


def deployments(name: str) -> list[CoolifyDeployment]:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return [CoolifyDeployment.model_validate(d) for d in data["deployments"]]


def container(name: str, uuid: str | None = SHOP) -> ContainerSummary:
    labels = {"coolify.type": "application"} | ({"coolify.applicationUuid": uuid} if uuid else {})
    return ContainerSummary(id=name, name=name, image="img:1", state="running", labels=labels)


def service(*containers: ContainerSummary) -> Service:
    return Service(
        id=f"a/coolify:{SHOP}", host_id="a", name="shop", source="coolify", containers=containers
    )


class Coolify(httpx.AsyncBaseTransport):
    """Answers the deployments of the shop's application with `status` and `body` (by default
    the history fixture), unread as the network hands it over, and records every request."""

    def __init__(self, status: int = 200, body: bytes | None = None) -> None:
        self.status = status
        self.body = body or (FIXTURES / "deployments-history.json").read_bytes()
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path != f"/api/v1/deployments/applications/{SHOP}":
            return httpx.Response(404, stream=httpx.ByteStream(b"{}"))
        return httpx.Response(self.status, stream=httpx.ByteStream(self.body))


def client(coolify: Coolify) -> CoolifyClient:
    return CoolifyClient(BASE, SecretStr("t"), transport=coolify)


def test_the_running_commit_is_the_newest_finished_deploy_and_the_range_starts_at_another():
    running = running_commit(deployments("deployments-history.json"))

    assert running.sha == HEAD and running.how == "coolify"
    # Skipped: the deploy in progress, the preview of another commit and the failed one.
    assert running.previous == BASE_SHA
    # A redeploy of the same commit changed no code: live since its first deploy in a row.
    assert running.since == datetime(2025, 3, 1, 9, 2, 30, tzinfo=UTC)


@pytest.mark.parametrize("order", [[5, 0, 3, 1, 4, 2], [2, 4, 1, 3, 0, 5], [0, 1, 2, 3, 4, 5]])
def test_the_order_coolify_lists_them_in_is_not_relied_on(order):
    found = deployments("deployments-history.json")

    running = running_commit([found[i] for i in order])

    assert (running.sha, running.previous) == (HEAD, BASE_SHA)
    assert running.since == datetime(2025, 3, 1, 9, 2, 30, tzinfo=UTC)


def test_a_deploy_with_no_readable_creation_time_counts_as_the_oldest():
    found = deployments("deployments-history.json")
    found[5] = found[5].model_copy(update={"created_at": "soon"})  # the base, listed last
    shuffled = [found[5], *found[:5]]

    assert running_commit(shuffled).previous == BASE_SHA


def test_one_finished_deploy_gives_no_previous_commit():
    running = running_commit(deployments("deployments.json"))  # finished, then failed

    assert running.sha == "4b1d3c0ffee0123456789abcdef0123456789abc"
    assert running.previous is None
    assert running.since == datetime(2026, 11, 4, 7, 52, 40, tzinfo=UTC)


def test_a_running_deploy_coolify_records_as_head_names_no_commit():
    found = deployments("deployments-history.json")
    found[1] = found[1].model_copy(update={"commit": "HEAD"})

    with pytest.raises(DeployUnknown, match="no commit"):
        running_commit(found)


def test_a_ref_before_the_running_commit_leaves_the_previous_one_unknown():
    found = deployments("deployments-history.json")
    found[3] = found[3].model_copy(update={"commit": "main"})

    running = running_commit(found)

    assert running.sha == HEAD and running.previous is None


def test_no_finished_deploy_is_unknown():
    found = [d for d in deployments("deployments-history.json") if d.status != "finished"]
    with pytest.raises(DeployUnknown, match="no finished deployment"):
        running_commit(found)


def test_an_odd_finish_time_is_left_out():
    found = deployments("deployments.json")
    found[0] = found[0].model_copy(update={"finished_at": "yesterday\n- 0000000 forged"})
    assert running_commit(found).since is None


async def test_the_source_reads_the_apps_deployments_by_the_containers_label():
    coolify = Coolify()

    running = await CoolifyDeploys(client(coolify)).running_commit(service(container("web")))

    assert running is not None and running.sha == HEAD
    assert coolify.requests[0].url.params["take"] == "30"


async def test_a_service_of_no_coolify_app_has_no_word_from_it():
    coolify = Coolify()
    unlabelled = service(container("web", None))
    assert await CoolifyDeploys(client(coolify)).running_commit(unlabelled) is None
    assert coolify.requests == []


async def test_containers_of_several_apps_are_unknown():
    deploys = CoolifyDeploys(client(Coolify()))
    with pytest.raises(DeployUnknown, match="several Coolify applications"):
        await deploys.running_commit(service(container("a"), container("b", "blog0uuid00000000b2")))


async def test_a_preview_deployment_is_unknown_not_its_applications_commit():
    coolify = Coolify()
    preview = container("web").model_copy(
        update={"labels": container("web").labels | {"coolify.pullRequestId": "12"}}
    )
    main = container("web").model_copy(
        update={"labels": container("web").labels | {"coolify.pullRequestId": "0"}}
    )

    with pytest.raises(DeployUnknown, match="preview deployment"):
        await CoolifyDeploys(client(coolify)).running_commit(service(preview))
    assert coolify.requests == []
    running = await CoolifyDeploys(client(coolify)).running_commit(service(main))
    assert running is not None and running.sha == HEAD


async def test_coolify_down_is_unknown_in_bleeps_words():
    deploys = CoolifyDeploys(client(Coolify(502, b"Bad Gateway <html>")))
    with pytest.raises(DeployUnknown) as unknown:
        await deploys.running_commit(service(container("web")))
    assert str(unknown.value) == "Coolify's deployments could not be read (Coolify answered 502)"


async def test_the_plugin_registry_answers_only_while_coolify_is_on():
    plugins = PluginRegistry([CoolifyPlugin(transport=Coolify())], SecretStr("s"), env=ENV)
    shop = service(container("web"))

    assert await plugins.running_commit(shop) is None
    plugins.set("coolify", {}, True)
    running = await plugins.running_commit(shop)
    assert running is not None and running.previous == BASE_SHA
    plugins.enable("coolify", False)
    assert await plugins.running_commit(shop) is None
