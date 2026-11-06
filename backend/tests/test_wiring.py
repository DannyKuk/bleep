from datetime import UTC, datetime

from pydantic import SecretStr

from bleep.hosts.registry import Registry
from bleep.schemas.graph import Decision, PlanStep, Proposal
from bleep.schemas.incidents import Signal, Subject
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
