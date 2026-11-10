from datetime import UTC, datetime

from pydantic import SecretStr

from bleep.hosts.registry import Registry
from bleep.llm.tracing import NoTracing
from bleep.schemas.graph import Decision, PlanStep, Proposal
from bleep.schemas.incidents import Signal, Subject
from bleep.schemas.settings import SettingsUpdate
from bleep.wiring import build_parts

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
PLAN = Proposal(
    kind="act", steps=[PlanStep(action="docker.restart", host_id="demo", target="web")], reason="x"
)


async def test_the_app_clears_telegram_buttons_when_an_incident_ends_or_a_plan_is_decided(
    settings,
):
    on = settings.model_copy(
        update={"telegram_bot_token": SecretStr("123:abc"), "telegram_chat_id": "42"}
    )
    parts = build_parts(on, Registry([]))
    await parts.db.migrate()
    notifier = parts.notify.notifier
    cleared: list[int] = []

    async def record(chat_id: str, message_id: int) -> None:
        cleared.append(message_id)

    notifier.telegram.clear_buttons = record
    subject = Subject(kind="service", host_id="demo", service_id="demo/web")
    signals = [Signal(subject=subject, kind="container_died", at=T0)]
    first, _ = await parts.incidents.open_or_attach(subject, signals)
    request = await parts.approvals.request(first.id, PLAN, [])
    await notifier.messages.add(1, first.id, request.id, T0)
    await parts.approvals.resolve(
        first.id, Decision(kind="reject", via="dashboard", at=T0), request.id
    )
    await notifier.drain()
    assert cleared == [1]

    await notifier.messages.add(2, first.id, "", T0)
    await parts.incidents.close_unfinished(first.id)
    await notifier.drain()
    assert sorted(cleared[1:]) == [1, 2]
    await parts.notify.close()


async def test_the_time_zone_chosen_on_the_settings_page_reaches_the_chat_snapshot(settings):
    parts = build_parts(settings.model_copy(update={"timezone": "Europe/London"}), Registry([]))
    await parts.db.migrate()
    await parts.settings_store.load()
    parts.talk.state.clock = lambda: T0
    assert (await parts.talk.state.snapshot()).now == "2026-11-02 12:00"  # London is UTC then

    update = SettingsUpdate.model_validate({"general": {"timezone": "Asia/Tokyo"}})
    await parts.settings_store.update(update, version=parts.settings_store.version())

    snapshot = await parts.talk.state.snapshot()
    assert (snapshot.zone, snapshot.now) == ("Asia/Tokyo", "2026-11-02 21:00")
    await parts.notify.close()


def test_the_conversation_traces_its_readings_with_the_parts_tracer(settings):
    tracer = NoTracing()
    parts = build_parts(settings, Registry([]), tracer=tracer)

    assert parts.talk.tracer is parts.tracer is tracer
    assert parts.panel.talk is parts.talk


def test_a_tracer_that_traces_incidents_only_leaves_the_conversation_untraced(settings):
    class IncidentsOnly:
        def run_config(self, incident):
            return {}

        def trace_url(self, incident):
            return None

        async def close(self) -> None:
            return None

    parts = build_parts(settings, Registry([]), tracer=IncidentsOnly())

    assert parts.talk.tracer is None


async def test_the_embedder_is_built_from_the_settings_in_force_at_its_first_use(settings):
    """Built after the stored settings load, so it follows the settings page; a change saved
    later shows as needing a restart (#372)."""
    parts = build_parts(settings, Registry([]))
    await parts.db.migrate()
    store = parts.settings_store
    await store.load()
    chosen = SettingsUpdate.model_validate({"embeddings": {"min_score": 0.42}})
    await store.update(chosen, version=store.version())

    assert parts.memory.embedder.min_score == 0.42
    assert parts.knowledge.embedder is parts.memory.embedder
    assert store.restart_needed() == []
    later = SettingsUpdate.model_validate({"embeddings": {"min_score": 0.3}})
    await store.update(later, version=store.version())
    assert parts.memory.embedder.min_score == 0.42
    assert store.restart_needed() == ["embeddings"]
    await parts.notify.close()


def test_the_model_servers_addresses_may_be_set_from_the_dashboard_bound_to_their_keys(settings):
    names = {entry.name: entry for entry in build_parts(settings, Registry([])).secrets.names()}
    assert names["LOCAL_BASE_URL"].kind == "address" and names["LOCAL_BASE_URL"].private
    embeddings = names["EMBEDDINGS_BASE_URL"]
    assert embeddings.private and embeddings.carries == "EMBEDDINGS_API_KEY"
    assert not {"LANGSMITH_ENDPOINT", "LANGFUSE_BASE_URL"} & set(names)  # settings, not secrets
