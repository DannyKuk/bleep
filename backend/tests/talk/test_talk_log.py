"""The talk log (#411): the last 100 messages of each chat, with how Bleep read them, what the
surface showed and the press of an offer, and the Markdown transcript the person exports."""

from datetime import UTC, datetime, timedelta

from bleep import prompts
from bleep.schemas.talk import (
    Offer,
    TalkAction,
    TalkExchange,
    TalkLink,
    TalkReading,
    TalkSurface,
    TalkUsage,
)
from bleep.talk.log import EXCHANGES_KEPT, TalkLog, transcript

T0 = datetime(2026, 11, 10, 14, 2, tzinfo=UTC)


def exchange(log: TalkLog, surface: TalkSurface = "panel", **fields) -> TalkExchange:
    values = {"at": T0, "text": "how is shop?", "kind": "question", "answer": "shop is fine."}
    entry = TalkExchange(id=log.next_id(), surface=surface, **(values | fields))
    log.add(entry)
    return entry


def line(name: str, **values: str) -> str:
    return prompts.load(f"talk/{name}", **values).strip()


def test_each_surface_keeps_its_last_100_messages():
    log = TalkLog(lambda: T0)
    for n in range(EXCHANGES_KEPT + 5):
        exchange(log, text=f"panel {n}")
    exchange(log, "telegram", text="from the phone")

    kept = log.exchanges()

    panel = [e.text for e in kept if e.surface == "panel"]
    assert EXCHANGES_KEPT == 100 and len(panel) == 100
    assert panel[0] == "panel 5" and panel[-1] == "panel 104"
    assert [e.text for e in kept if e.surface == "telegram"] == ["from the phone"]


def test_what_the_surface_showed_and_the_first_press_are_kept_with_the_message():
    clock = [T0]
    log = TalkLog(lambda: clock[0])
    entry = exchange(log, kind="action", answer="the reading's line")
    link = TalkLink(kind="service", id="a/compose:shop", label="shop")

    log.shown("panel", entry.id, "🤫 Snooze shop until 16:04?", [link], "c1")
    clock[0] = T0 + timedelta(minutes=1)
    log.pressed("panel", "c1", True, "Snoozed until 16:04.")
    log.pressed("panel", "c1", True, "Already answered; this press changed nothing.")
    log.pressed("telegram", "c1", False, "a press on the other surface is not this one")

    [kept] = log.exchanges()
    assert (kept.answer, kept.links) == ("🤫 Snooze shop until 16:04?", [link])
    assert kept.press is not None
    assert (kept.press.at, kept.press.yes) == (T0 + timedelta(minutes=1), True)
    assert kept.press.line == "Snoozed until 16:04."


def test_the_export_is_a_copy_the_log_does_not_share():
    log = TalkLog(lambda: T0)
    exchange(log)
    log.exchanges()[0].answer = "changed by a reader"
    assert log.exchanges()[0].answer == "shop is fine."


def test_the_offers_id_never_leaves_the_log():
    log = TalkLog(lambda: T0)
    entry = exchange(log)
    log.shown("panel", entry.id, "ok", confirm_id="c1")
    assert "confirm_id" not in log.exchanges()[0].model_dump()


def test_the_transcript_shows_each_surface_with_how_each_message_was_read():
    log = TalkLog(lambda: T0)
    snooze = TalkAction(kind="snooze", subject="shop", minutes=120)
    offer = Offer(kind="snooze", scope="service", target_id="a/compose:shop", subject="shop")
    entry = exchange(
        log,
        text="leave shop alone\nfor 2 h",
        incident_id="inc_14",
        kind="action",
        reading=TalkReading(kind="action", action=snooze),
        offer=offer,
        usage=TalkUsage(
            model="openai/gpt-6-luna", calls=1, tokens_in=900, tokens_out=40, usd=0.0001
        ),
    )
    log.shown("panel", entry.id, "🤫 Snooze shop until 16:04?", confirm_id="c1")
    log.pressed("panel", "c1", True, "Snoozed until 16:04.")
    exchange(log, kind="refusal", reading=TalkReading(kind="refusal", refusal="change"),
             refused=None, answer="I can't do that from chat.")  # fmt: skip
    exchange(log, kind="failed", error="TimeoutError", answer="I couldn't read that just now.")

    md = transcript(log.exchanges(), T0)

    assert md.startswith("# ")
    assert md.index(line("log_panel")) < md.index(line("log_telegram"))
    assert line("log_empty") in md.split(line("log_telegram"))[1]
    assert "> leave shop alone\n> for 2 h" in md
    assert "2026-11-10T14:02:00+00:00 · " + line("log_about", incident="inc_14") in md
    assert '"kind":"snooze","subject":"shop","minutes":120' in md
    assert '"kind":"snooze","scope":"service","target_id":"a/compose:shop"' in md
    assert line("log_press", button="Yes", line="Snoozed until 16:04.") in md
    assert "openai/gpt-6-luna" in md and "$0.00010" in md
    assert line("log_unread", error="TimeoutError") in md
    assert md.count(line("log_no_call")) == 2


def test_a_check_that_refused_an_action_says_why_in_the_transcript():
    log = TalkLog(lambda: T0)
    why = "I don't know a service or host by that name."
    read = TalkReading(kind="action", action=TalkAction(kind="snooze", subject="nope"))
    exchange(log, "telegram", kind="action", reading=read, refused=why, answer=why)

    md = transcript(log.exchanges(), T0)

    assert line("log_refused", line=why) in md
    assert line("log_empty") in md.split(line("log_telegram"))[0]
