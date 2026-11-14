from importlib.metadata import version

import httpx

from bleep.hosts.config import UrlCheckConfig
from bleep.hosts.url_check import USER_AGENT, check_url

URL = "https://shop.example/health"


async def test_every_request_names_bleep_including_a_followed_redirect():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/health":
            return httpx.Response(302, headers={"Location": "https://shop.example/ok"})
        return httpx.Response(200, text="fine")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await check_url(client, UrlCheckConfig(url=URL))

    assert result.ok
    assert [request.url.path for request in seen] == ["/health", "/ok"]
    assert [request.headers["user-agent"] for request in seen] == [USER_AGENT, USER_AGENT]


def test_user_agent_carries_the_package_version():
    assert USER_AGENT.startswith(f"Bleep/{version('bleep')} (")
    assert USER_AGENT.endswith("+https://github.com/DannyKuk/bleep)")
