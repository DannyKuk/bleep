"""A look on request reads the service's URLs (#423): Gather checks each of them for a run the
look alone opened, and Investigate and Diagnose see the results; any other run checks none."""

import httpx
from test_diagnose import reply
from test_investigate import DONE
from test_investigate import setup as investigate_setup
from test_look import DIED, LOOK

from bleep.graph.host_text import describe_urls
from bleep.graph.nodes import diagnose, gather, investigate
from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.schemas.graph import GatherContext, UrlReading
from bleep.schemas.incidents import Signal, Subject
from fakes import FakeChatModel
from graph_env import graph_env, shop_engine, shop_host

SHOP_URLS = [
    UrlCheckConfig(url="https://shop.example/health"),
    UrlCheckConfig(url="https://shop.example/api/items", body_contains="Coffee beans"),
]


def shop_answers(seen: list[str]) -> httpx.MockTransport:
    """/health answers 200; /api/items 502, as a proxy does when the app behind it fails."""

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        status = 200 if request.url.path == "/health" else 502
        return httpx.Response(status, stream=httpx.ByteStream(b"ok"))

    return httpx.MockTransport(answer)


async def url_env(tmp_path, seen: list[str]):
    engine = shop_engine()
    engine.log_text = {"shop-web-1": "web says hi\n", "shop-db-1": "db ready\n"}
    overrides = ServiceOverrides(urls=SHOP_URLS)
    env = await graph_env(tmp_path, engine=engine, host=shop_host(services={"shop": overrides}))
    env.deps.http = httpx.AsyncClient(transport=shop_answers(seen))
    subject = Subject(kind="service", host_id="a", service_id=env.service_id("shop"))
    return env, subject


def on(signal: Signal, subject: Subject) -> Signal:
    return signal.model_copy(update={"subject": subject})


async def gathered(env, subject: Subject, signals: list[Signal]) -> GatherContext:
    state = {"incident_id": "inc-1", "subject": subject, "signals": signals}
    return (await gather.make(env.deps)(state))["context"]


async def test_gather_of_a_bare_look_checks_every_url_and_reads_every_containers_logs(tmp_path):
    seen: list[str] = []
    env, subject = await url_env(tmp_path, seen)

    ctx = await gathered(env, subject, [on(LOOK, subject)])

    assert sorted(seen) == ["https://shop.example/api/items", "https://shop.example/health"]
    assert ctx.urls == [
        UrlReading(url="https://shop.example/health", ok=True, status=200),
        UrlReading(
            url="https://shop.example/api/items",
            ok=False,
            status=502,
            error="expected status 200, got 502",
        ),
    ]
    assert sorted(ctx.logs) == ["shop-db-1", "shop-web-1"]


async def test_gather_of_a_failure_checks_no_url_even_with_a_look_attached(tmp_path):
    seen: list[str] = []
    env, subject = await url_env(tmp_path, seen)

    for signals in ([on(DIED, subject)], [on(LOOK, subject), on(DIED, subject)]):
        assert (await gathered(env, subject, signals)).urls == []
    assert seen == []


async def test_gather_of_a_look_at_a_service_without_urls_checks_none(tmp_path):
    env = await graph_env(tmp_path)
    subject = Subject(kind="service", host_id="a", service_id=env.service_id("shop"))
    assert (await gathered(env, subject, [on(LOOK, subject)])).urls == []


async def test_investigate_and_diagnose_of_a_look_read_its_url_checks_as_untrusted(tmp_path):
    seen: list[str] = []
    env, subject = await url_env(tmp_path, seen)
    env.models.model = FakeChatModel(script=[DONE, reply(0.9)], structured_output_unsupported=True)
    state: dict = {"incident_id": "inc-1", "subject": subject, "signals": [on(LOOK, subject)]}
    state |= await gather.make(env.deps)(state)

    state |= await investigate.make(env.deps)(state)
    task = str(env.models.model.seen[0][1].content)
    section = task.split("## URL checks", 1)[1].split("## Host", 1)[0]
    assert '<untrusted source="url_checks"' in section
    assert "passed, answered 200: https://shop.example/health" in section
    assert "failed, expected status 200, got 502: https://shop.example/api/items" in section
    assert "a reverse proxy's that is not part of this service" in task

    await diagnose.make(env.deps)(state | {"investigate_rounds": 1})
    sent = "\n".join(str(m.content) for m in env.models.model.seen[1])
    assert "URL checks, which Bleep ran" in sent and "got 502" in sent
    assert "## URL checks" not in sent  # a label line, not a heading, in Diagnose
    assert "a failed URL check or errors in its own logs is a failure" in sent
    assert "no URLs in Bleep's settings" in sent and "no URLs in Bleep's settings" in task


async def test_diagnose_after_a_fix_leaves_the_looks_url_checks_out(tmp_path):
    seen: list[str] = []
    env, subject = await url_env(tmp_path, seen)
    env.models.model = FakeChatModel(script=[reply(0.9)], structured_output_unsupported=True)
    state: dict = {"incident_id": "inc-1", "subject": subject, "signals": [on(LOOK, subject)]}
    state |= await gather.make(env.deps)(state)
    assert state["context"].urls

    await diagnose.make(env.deps)(state | {"investigate_rounds": 1, "verify_loops": 1})
    sent = "\n".join(str(m.content) for m in env.models.model.seen[0])
    assert "URL checks, which Bleep ran" not in sent and "got 502" not in sent


async def test_investigate_of_a_failure_has_no_url_checks_section(tmp_path):
    env, state = await investigate_setup(tmp_path, [DONE])
    await investigate.make(env.deps)(state)
    assert "## URL checks" not in str(env.models.model.seen[0][1].content)


def test_a_url_line_gives_its_result_before_the_url_so_a_redacted_token_cannot_eat_it():
    text = describe_urls(
        [
            UrlReading(url="https://shop.example/h?token=s3cretvalue123456", ok=True, status=401),
            UrlReading(url="https://shop.example/a", ok=False, error="timed out after 10 s"),
        ]
    )
    assert "passed, answered 401: https://shop.example/h?token=" in text
    assert "s3cretvalue123456" not in text
    assert "failed, timed out after 10 s: https://shop.example/a" in text
    assert text.startswith('<untrusted source="url_checks"')
