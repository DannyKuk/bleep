"""A look on request is offered only for a service Bleep can look at now, and each reason it
cannot is a fixed line; what a press opened, or why it opened nothing, is said from Bleep's own
names (#403)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from bleep import prompts
from bleep.incidents.looks import Look
from bleep.schemas.incidents import Incident, Subject
from bleep.schemas.quiet import QuietState
from bleep.schemas.talk import HostLine, QuietLine, ServiceLine, Snapshot, TalkAction
from bleep.talk.looks import look_offer, look_said
from bleep.talk.offers import confirm_line

NOW = datetime(2026, 11, 9, 13, 12, tzinfo=UTC)  # 14:12 in Vienna
VIENNA = ZoneInfo("Europe/Vienna")
SNOOZED = QuietLine(mode="snooze", until="2026-11-09 16:04", since="2026-11-09 14:04")
LEFT = QuietLine(mode="leave_alone", since="2026-11-09 10:00")
SNAPSHOT = Snapshot(
    now="2026-11-09 14:12",
    zone="Europe/Vienna",
    hosts=[
        HostLine(id="laptop", name="Laptop", health="down", reachable=True),
        HostLine(id="m1", name="M1", health="ok", reachable=True, quiet=LEFT),
        HostLine(id="nas", name="NAS", health="unknown", reachable=False),
    ],
    services=[
        ServiceLine(id="laptop/compose:shop-web", name="shop-web", host="laptop", health="down"),
        ServiceLine(
            id="laptop/compose:shop", name="shop", host="laptop", health="down",
            open_incident="shop #14",
        ),
        ServiceLine(
            id="laptop/compose:backup", name="backup", host="laptop", health="ok", quiet=SNOOZED
        ),
        ServiceLine(id="laptop/compose:cron", name="cron", host="laptop", health="ok", quiet=LEFT),
        ServiceLine(
            id="m1/compose:grafana", name="grafana", host="m1", health="ok",
            quiet=LEFT.model_copy(update={"through_host": True}),
        ),
        ServiceLine(id="nas/compose:files", name="files", host="nas", health="unknown"),
        ServiceLine(id="laptop/compose:web", name="web", host="laptop", health="ok"),
        ServiceLine(id="m1/compose:web", name="web", host="m1", health="ok"),
    ],
)  # fmt: skip


def line(name: str, **values: str) -> str:
    return prompts.load(f"talk/{name}", **values).strip()


def offer(subject: str | None, spent: bool = False):
    action = TalkAction(kind="look_again", subject=subject)
    return look_offer(action, SNAPSHOT, NOW, VIENNA, spent)


def test_a_service_bleep_can_look_at_is_offered_with_what_it_costs():
    offered = offer("shop-web")

    assert not isinstance(offered, str)
    assert (offered.kind, offered.scope, offered.target_id, offered.subject) == (
        "look_again",
        "service",
        "laptop/compose:shop-web",
        "shop-web",
    )
    confirm = confirm_line(offered, NOW, VIENNA)
    assert confirm == line("confirm_look_again", subject="shop-web")
    assert confirm.startswith("🔎 Look at shop-web now?") and "model run" in confirm


@pytest.mark.parametrize(
    ("said", "answer"),
    [
        (None, line("no_subject")),
        ("shoop", line("no_subject")),
        ("bleep", line("no_subject")),  # Bleep's own containers are no service it lists
        ("web", line("ambiguous_subject", subject="web")),
        ("laptop", line("look_host", host="laptop")),
        ("shop", line("look_open", incident="shop #14", subject="shop")),
        ("backup", line("look_snoozed", subject="backup", until="16:04")),
        ("cron", line("look_left_alone", subject="cron")),
        ("grafana", line("look_quiet_host", subject="grafana", host="m1")),
        ("files", line("look_unreachable", subject="files", host="nas")),
    ],
)
def test_a_service_bleep_cannot_look_at_now_is_answered_and_offered_nothing(said, answer):
    assert offer(said) == answer


def test_a_spent_daily_cap_is_answered_and_offered_nothing():
    assert offer("shop-web", spent=True) == line("look_spent")


SHOP = Subject(kind="service", host_id="laptop", service_id="laptop/compose:shop")
OPENED = Incident(
    id="inc_15", subject=SHOP, status="open", opened_at=NOW, title="t", short_name="shop #15"
)


def looked(outcome: str, **found) -> Look:
    return Look(outcome, "laptop/compose:shop", "shop", "laptop", **found)  # type: ignore[arg-type]


def quiet(scope: str, mode: str, until: datetime | None) -> QuietState:
    target = "laptop" if scope == "host" else "laptop/compose:shop"
    return QuietState(scope=scope, target_id=target, mode=mode, until=until, since=NOW)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("look", "answer"),
    [
        (
            looked("opened", incident=OPENED),
            line("look_started", subject="shop", incident="shop #15"),
        ),
        (looked("open", incident=OPENED), line("look_open", incident="shop #15", subject="shop")),
        (
            looked("quiet", quiet=quiet("service", "snooze", NOW + timedelta(hours=2))),
            line("look_snoozed", subject="shop", until="16:12"),
        ),
        (
            looked("quiet", quiet=quiet("service", "leave_alone", None)),
            line("look_left_alone", subject="shop"),
        ),
        (
            looked("quiet", quiet=quiet("host", "leave_alone", None)),
            line("look_quiet_host", subject="shop", host="laptop"),
        ),
        (looked("unreachable"), line("look_unreachable", subject="shop", host="laptop")),
        (looked("spent"), line("look_spent")),
        (Look("unknown", "laptop/compose:gone"), line("look_unknown")),
    ],
)  # fmt: skip
def test_a_press_is_answered_with_what_came_of_it(look, answer):
    assert look_said(look, NOW, VIENNA) == answer
