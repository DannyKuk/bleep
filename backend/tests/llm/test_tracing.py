import json
import uuid

import pytest
from langchain_core.tracers.langchain import LangChainTracer
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
from pydantic import SecretStr

from bleep.config import SettingsError
from bleep.llm.tracing import (
    LangfuseTracing,
    LangSmithTracing,
    NoTracing,
    ReloadingTracer,
    TracingSettings,
    build_tracer,
)
from bleep.schemas.incidents import Incident, Subject
from bleep.schemas.settings import TracingChoice

PUBLIC, SECRET = "pk-lf-test-public", "sk-lf-test-secret"


def incident(service_id: str | None = "a/shop") -> Incident:
    kind = "service" if service_id else "host"
    return Incident.model_validate(
        {
            "id": "inc_01TEST",
            "subject": Subject(kind=kind, host_id="a", service_id=service_id),
            "status": "open",
            "opened_at": "2026-11-03T10:00:00+00:00",
            "title": "container died on a/shop",
        }
    )


def settings(**values) -> TracingSettings:
    return TracingSettings.model_validate(values)


@pytest.fixture
def no_tracing_env(monkeypatch):
    for name in ("TRACING", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGSMITH_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_tracing_is_off_by_default(no_tracing_env):
    tracer = build_tracer(TracingSettings())
    assert isinstance(tracer, NoTracing)


def test_off_adds_no_callbacks_and_no_link():
    tracer = NoTracing()
    assert tracer.run_config(incident()) == {}
    assert tracer.trace_url(incident()) is None


def test_an_unknown_tracing_value_is_refused():
    with pytest.raises(ValueError):
        settings(TRACING="datadog")


@pytest.mark.parametrize("missing", ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"])
def test_langfuse_without_both_keys_refuses_to_start(missing):
    values = {"TRACING": "langfuse", "LANGFUSE_PUBLIC_KEY": PUBLIC, "LANGFUSE_SECRET_KEY": SECRET}
    del values[missing]
    with pytest.raises(SettingsError, match=missing):
        build_tracer(settings(**values))


def test_langsmith_without_a_key_refuses_to_start():
    with pytest.raises(SettingsError, match="LANGSMITH_API_KEY"):
        build_tracer(settings(TRACING="langsmith"))


@pytest.fixture
def langfuse() -> LangfuseTracing:
    tracer = build_tracer(
        settings(
            TRACING="langfuse",
            LANGFUSE_PUBLIC_KEY=PUBLIC,
            LANGFUSE_SECRET_KEY=SECRET,
            LANGFUSE_BASE_URL="http://langfuse-web:3000",
            LANGFUSE_UI_URL="http://localhost:3001/",
            LANGFUSE_PROJECT_ID="bleep",
        )
    )
    assert isinstance(tracer, LangfuseTracing)
    return tracer


def test_langfuse_makes_the_incident_the_session_named_by_its_title_and_tagged(langfuse):
    config = langfuse.run_config(incident())
    assert config["run_name"] == "incident:container died on a/shop"
    assert config["metadata"]["langfuse_session_id"] == "inc_01TEST"
    assert config["metadata"]["langfuse_trace_name"] == "incident:container died on a/shop"
    assert config["tags"] == ["host:a", "service:a/shop"]
    [handler] = config["callbacks"]
    assert isinstance(handler, CallbackHandler)


def test_a_host_incident_is_tagged_with_its_host_only(langfuse):
    assert langfuse.run_config(incident(service_id=None))["tags"] == ["host:a"]


def test_every_run_of_one_incident_goes_into_the_same_trace(langfuse):
    """The first run and the resume after approval are separate graph invocations, possibly in
    separate processes: they share the trace because its id is derived from the incident id."""
    first = langfuse.run_config(incident())["callbacks"][0]
    resumed = langfuse.run_config(incident())["callbacks"][0]
    trace_id = Langfuse.create_trace_id(seed="inc_01TEST")
    assert first._trace_context == {"trace_id": trace_id}
    assert resumed._trace_context == {"trace_id": trace_id}
    other = incident().model_copy(update={"id": "inc_02OTHER"})
    assert langfuse.run_config(other)["callbacks"][0]._trace_context != {"trace_id": trace_id}


def test_the_langfuse_link_points_at_the_trace_in_the_ui_not_the_internal_api(langfuse):
    trace_id = Langfuse.create_trace_id(seed="inc_01TEST")
    url = f"http://localhost:3001/project/bleep/traces/{trace_id}"
    assert langfuse.trace_url(incident()) == url


def test_without_the_project_id_langfuse_traces_but_stores_no_link():
    tracer = build_tracer(
        settings(TRACING="langfuse", LANGFUSE_PUBLIC_KEY=PUBLIC, LANGFUSE_SECRET_KEY=SECRET)
    )
    assert tracer.run_config(incident())["callbacks"]
    assert tracer.trace_url(incident()) is None


def test_langsmith_groups_the_runs_of_an_incident_by_its_id(no_tracing_env):
    tracer = build_tracer(settings(TRACING="langsmith", LANGSMITH_API_KEY="lsv2_test_key"))
    assert isinstance(tracer, LangSmithTracing)
    config = tracer.run_config(incident())
    assert config["run_name"] == "incident:container died on a/shop"
    assert config["metadata"]["session_id"] == "inc_01TEST"
    assert config["tags"] == ["host:a", "service:a/shop"]
    [handler] = config["callbacks"]
    assert isinstance(handler, LangChainTracer)
    assert handler.project_name == "bleep"
    assert tracer.trace_url(incident()) is None


def test_no_key_appears_in_what_the_run_carries(langfuse):
    """Run metadata and tags reach the trace and the checkpoints; the keys must not."""
    config = langfuse.run_config(incident())
    carried = json.dumps({k: v for k, v in config.items() if k != "callbacks"})
    assert PUBLIC not in carried and SECRET not in carried
    assert SECRET not in repr(settings(LANGFUSE_SECRET_KEY=SECRET))


async def test_a_reloading_tracer_takes_a_key_changed_on_the_dashboard(no_tracing_env, monkeypatch):
    monkeypatch.setenv("TRACING", "langsmith")
    keys = {"LANGSMITH_API_KEY": SecretStr("lsv2_first")}
    tracer = ReloadingTracer(keys.get)
    first = tracer.current()
    closed = []

    async def close() -> None:
        closed.append(True)

    first.close = close  # type: ignore[method-assign]
    keys["LANGSMITH_API_KEY"] = SecretStr("lsv2_second")
    await tracer.reload()

    assert closed == [True] and tracer.current() is not first
    (callback,) = tracer.run_config(incident())["callbacks"]
    assert callback.client.api_key == "lsv2_second"


async def test_a_reloading_tracer_without_its_key_now_stops_tracing(no_tracing_env, monkeypatch):
    monkeypatch.setenv("TRACING", "langsmith")
    keys = {"LANGSMITH_API_KEY": SecretStr("lsv2_first")}
    tracer = ReloadingTracer(keys.get)
    keys.clear()
    await tracer.reload()
    assert tracer.run_config(incident()) == {}


def test_a_reloading_tracer_still_refuses_to_start_without_its_keys(no_tracing_env, monkeypatch):
    monkeypatch.setenv("TRACING", "langfuse")
    with pytest.raises(SettingsError, match="LANGFUSE_PUBLIC_KEY"):
        ReloadingTracer(lambda name: None)


async def test_a_tracer_that_fails_to_build_on_reload_stops_tracing_with_the_old_key(
    no_tracing_env, monkeypatch
):
    from bleep.llm import tracing

    monkeypatch.setenv("TRACING", "langsmith")
    keys = {"LANGSMITH_API_KEY": SecretStr("lsv2_first")}
    tracer = ReloadingTracer(keys.get)
    closed = []

    async def close() -> None:
        closed.append(True)

    tracer.current().close = close  # type: ignore[method-assign]

    def broken(settings):
        raise RuntimeError("client constructor failed")

    monkeypatch.setattr(tracing, "build_tracer", broken)
    keys["LANGSMITH_API_KEY"] = SecretStr("lsv2_second")
    await tracer.reload()
    assert tracer.run_config(incident()) == {} and closed == [True]


def test_off_adds_nothing_to_a_chat_reading():
    assert NoTracing().talk_config("panel") == {}


@pytest.mark.parametrize("surface", ["panel", "telegram"])
def test_langfuse_traces_each_chat_reading_on_its_own_in_its_surfaces_session(langfuse, surface):
    config = langfuse.talk_config(surface)

    assert config["run_name"] == f"talk-{surface}"
    assert config["metadata"] == {
        "langfuse_session_id": f"talk-{surface}",
        "langfuse_trace_name": f"talk-{surface}",
    }
    assert config["tags"] == ["talk", f"surface:{surface}"]
    [handler] = config["callbacks"]
    assert isinstance(handler, CallbackHandler)
    assert not handler._trace_context  # a trace of its own, not an incident's


def test_langsmith_groups_the_chat_readings_of_a_surface(no_tracing_env):
    tracer = build_tracer(settings(TRACING="langsmith", LANGSMITH_API_KEY="lsv2_test_key"))
    config = tracer.talk_config("telegram")
    assert config["metadata"] == {"session_id": "talk-telegram"}
    assert config["run_name"] == "talk-telegram"
    [handler] = config["callbacks"]
    assert isinstance(handler, LangChainTracer)


def test_no_key_appears_in_what_a_chat_reading_carries(langfuse):
    config = langfuse.talk_config("panel")
    carried = json.dumps({k: v for k, v in config.items() if k != "callbacks"})
    assert PUBLIC not in carried and SECRET not in carried


def test_a_reloading_tracer_traces_chat_readings_with_the_tracer_in_force(no_tracing_env):
    tracer = ReloadingTracer(lambda name: None)
    assert tracer.talk_config("panel") == {}


def _langfuse(public: str, base_url: str) -> TracingSettings:
    return settings(
        TRACING="langfuse",
        LANGFUSE_PUBLIC_KEY=public,
        LANGFUSE_SECRET_KEY=f"sk-lf-{uuid.uuid4().hex}",
        LANGFUSE_BASE_URL=base_url,
    )


def test_langfuse_keeps_one_client_per_public_key_whatever_address_a_second_names(
    no_tracing_env,
):
    """Why a running Langfuse tracer is never built again (#372): the second client for the same
    public key is the first one, with the first one's address and secret key."""
    public = f"pk-lf-{uuid.uuid4().hex}"
    first = LangfuseTracing(_langfuse(public, "http://langfuse-web:3000"))
    second = LangfuseTracing(_langfuse(public, "https://cloud.example.test"))
    assert second._client._resources is first._client._resources
    assert second._client._resources.base_url == "http://langfuse-web:3000"


async def test_a_running_langfuse_tracer_is_kept_and_a_change_waits_for_a_restart(
    no_tracing_env,
):
    public = f"pk-lf-{uuid.uuid4().hex}"
    chosen = TracingChoice(tracing="langfuse")
    keys = {"LANGFUSE_PUBLIC_KEY": SecretStr(public), "LANGFUSE_SECRET_KEY": SecretStr("sk-lf-1")}
    tracer = ReloadingTracer(keys.get, lambda: chosen)
    running = tracer.current()
    closed = []

    async def close() -> None:
        closed.append(True)

    running.close = close  # type: ignore[method-assign]
    chosen = TracingChoice(tracing="langfuse", langfuse_base_url="https://cloud.example.test")
    await tracer.reload()
    assert tracer.current() is running and closed == [] and tracer.restart_needed()

    chosen = TracingChoice(tracing="langfuse")
    await tracer.reload()
    assert tracer.current() is running and not tracer.restart_needed()


def test_a_reloading_tracer_builds_no_client_before_its_first_use(no_tracing_env, monkeypatch):
    """Built before the stored settings load, it only checks the keys: a Langfuse client made then
    would keep the default address for the whole process."""
    from bleep.llm import tracing

    built = []
    monkeypatch.setattr(tracing, "build_tracer", lambda s: built.append(s) or NoTracing())
    tracer = ReloadingTracer(
        lambda name: SecretStr("lsv2_k"), lambda: TracingChoice(tracing="langsmith")
    )
    assert built == []
    tracer.current()
    assert len(built) == 1


@pytest.mark.parametrize("change", ["off", "key cleared"])
async def test_langfuse_switched_off_or_without_a_key_stops_tracing_at_once(no_tracing_env, change):
    """Never trace on with a key that was cleared, Langfuse or not (#372)."""
    public = f"pk-lf-{uuid.uuid4().hex}"
    chosen = TracingChoice(tracing="langfuse")
    keys = {"LANGFUSE_PUBLIC_KEY": SecretStr(public), "LANGFUSE_SECRET_KEY": SecretStr("sk-1")}
    tracer = ReloadingTracer(keys.get, lambda: chosen)
    running = tracer.current()
    closed = []

    async def close() -> None:
        closed.append(True)

    running.close = close  # type: ignore[method-assign]
    if change == "off":
        chosen = TracingChoice(tracing="off")
    else:
        del keys["LANGFUSE_SECRET_KEY"]
    await tracer.reload()
    assert isinstance(tracer.current(), NoTracing) and closed == [True]
    assert not tracer.restart_needed()


async def test_langfuse_back_on_with_a_key_used_before_waits_for_a_restart(no_tracing_env):
    """Its cached client would come back with the address it was first made for."""
    public = f"pk-lf-{uuid.uuid4().hex}"
    chosen = TracingChoice(tracing="langfuse")
    keys = {"LANGFUSE_PUBLIC_KEY": SecretStr(public), "LANGFUSE_SECRET_KEY": SecretStr("sk-1")}
    tracer = ReloadingTracer(keys.get, lambda: chosen)
    tracer.current()
    chosen = TracingChoice(tracing="off")
    await tracer.reload()
    chosen = TracingChoice(tracing="langfuse", langfuse_base_url="https://cloud.example.test")
    await tracer.reload()
    assert isinstance(tracer.current(), NoTracing) and tracer.restart_needed()

    keys["LANGFUSE_PUBLIC_KEY"] = SecretStr(f"pk-lf-{uuid.uuid4().hex}")  # a key never used here
    await tracer.reload()
    assert isinstance(tracer.current(), LangfuseTracing) and not tracer.restart_needed()
    assert tracer.current()._client._resources.base_url == "https://cloud.example.test"
