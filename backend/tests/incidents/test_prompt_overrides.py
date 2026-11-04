"""The dev panel's prompt overrides: checked like the loader, stored, put in force, versioned and
recorded in the guard log (#42, #141)."""

import logging
from datetime import UTC, datetime

import pytest

from bleep import prompts
from bleep.incidents.prompt_overrides import (
    PromptOverrideStore,
    PromptRefused,
    PromptStale,
    UnknownPrompt,
)
from bleep.store.db import Database

T0 = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
GITHUB_TOKEN = "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"


class RecordingGuardLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, str]] = []

    def record(self, incident_id: str, kind: str, detail: str) -> None:
        self.records.append((incident_id, kind, detail))


def new_store(db: Database, guard_log: RecordingGuardLog | None = None, **kwargs):
    return PromptOverrideStore(db, guard_log or RecordingGuardLog(), **kwargs)


async def save(store: PromptOverrideStore, name: str, text: str):
    return await store.set(name, text, version=store.version(name))


async def reset(store: PromptOverrideStore, name: str) -> None:
    await store.reset(name, version=store.version(name))


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(tmp_path / "bleep.db")
    await database.migrate()
    return database


async def stored(db: Database) -> dict[str, str]:
    conn = await db.connect()
    try:
        async with conn.execute("SELECT name, text FROM prompt_overrides") as cursor:
            return {row["name"]: row["text"] for row in await cursor.fetchall()}
    finally:
        await conn.close()


async def test_a_saved_override_is_what_load_returns_and_reset_brings_the_file_back(db):
    store = new_store(db, clock=lambda: T0)
    file_text = prompts.load("watcher/recovery_line", logs="LOGS")

    await save(store, "watcher/recovery_line", "Say in one line why it recovered: {logs}")
    assert prompts.load("watcher/recovery_line", logs="LOGS") == (
        "Say in one line why it recovered: LOGS"
    )
    assert store.overrides()["watcher/recovery_line"].updated_at == T0

    await reset(store, "watcher/recovery_line")
    assert prompts.load("watcher/recovery_line", logs="LOGS") == file_text
    assert await stored(db) == {}


async def test_overrides_survive_a_restart(db):
    await save(new_store(db), "graph/propose_unsure", "Be careful.")
    prompts.use_overrides({})

    await new_store(db).load()
    assert prompts.load("graph/propose_unsure") == "Be careful."


@pytest.mark.parametrize(
    "text",
    [
        "Why did it recover?",  # {logs} missing
        "Why did it recover? {logs} {container}",  # a placeholder no caller fills
        "Why did it recover? {logs",  # a stray brace
    ],
)
async def test_an_override_the_loader_would_refuse_is_not_saved(db, text):
    store = new_store(db)
    with pytest.raises(PromptRefused):
        await save(store, "watcher/recovery_line", text)
    assert await stored(db) == {}
    assert "Why did it recover?" not in prompts.load("watcher/recovery_line", logs="x")


async def test_an_override_holding_a_secret_is_refused_without_quoting_it(db):
    store = new_store(db)
    with pytest.raises(PromptRefused) as refused:
        await save(store, "graph/propose_unsure", f"Push with {GITHUB_TOKEN}")
    assert "ghp_" not in str(refused.value)
    assert await stored(db) == {}


@pytest.mark.parametrize("name", ["graph/nope", "../config", "evals/judge_system"])
async def test_only_the_apps_prompt_files_can_be_overridden(db, name):
    with pytest.raises(UnknownPrompt):
        await save(new_store(db), name, "anything")


async def test_a_stored_override_that_no_longer_fits_is_left_out_on_load(db, caplog):
    conn = await db.connect()
    rows = [
        ("graph/propose_unsure", "Still fine.", T0.isoformat()),
        ("watcher/recovery_line", "the file's placeholder was dropped", T0.isoformat()),
        ("graph/gone", "a prompt file that was removed", T0.isoformat()),
        ("graph/diagnose_missing", f"{{root_cause}} {{missing}} {GITHUB_TOKEN}", T0.isoformat()),
    ]
    await conn.executemany("INSERT INTO prompt_overrides VALUES (?, ?, ?)", rows)
    await conn.commit()
    await conn.close()
    store = new_store(db)

    with caplog.at_level(logging.WARNING):
        await store.load()

    assert set(store.overrides()) == {"graph/propose_unsure"}
    assert prompts.load("graph/propose_unsure") == "Still fine."
    assert prompts.load("watcher/recovery_line", logs="x") == prompts.file_text(
        "watcher/recovery_line"
    ).format(logs="x")
    assert "ghp_" not in caplog.text
    assert caplog.text.count("left out") == 3


async def test_reset_of_a_prompt_without_an_override_changes_nothing(db):
    store = new_store(db)
    await reset(store, "graph/propose_unsure")
    assert store.overrides() == {}


async def test_close_puts_every_file_back(db):
    store = new_store(db)
    await save(store, "graph/propose_unsure", "Be careful.")
    store.close()
    assert prompts.load("graph/propose_unsure") == prompts.file_text("graph/propose_unsure")


async def test_a_change_built_from_an_older_version_is_refused_and_nothing_changes(db):
    store = new_store(db)
    read = store.version("graph/propose_unsure")
    await store.set("graph/propose_unsure", "First.", version=read)

    with pytest.raises(PromptStale) as stale:
        await store.set("graph/propose_unsure", "Second.", version=read)
    assert stale.value.current == store.version("graph/propose_unsure") != read
    with pytest.raises(PromptStale):
        await store.reset("graph/propose_unsure", version=read)
    assert prompts.load("graph/propose_unsure") == "First."
    assert await stored(db) == {"graph/propose_unsure": "First."}


async def test_the_version_comes_from_the_text_so_it_holds_across_a_restart(db):
    store = new_store(db)
    none = store.version("graph/propose_unsure")
    await save(store, "graph/propose_unsure", "Be careful.")
    saved = store.version("graph/propose_unsure")

    again = new_store(db)
    await again.load()
    assert again.version("graph/propose_unsure") == saved != none
    await reset(again, "graph/propose_unsure")
    assert again.version("graph/propose_unsure") == none


async def test_a_save_and_a_reset_are_recorded_by_name_never_with_the_text(db):
    guard_log = RecordingGuardLog()
    store = new_store(db, guard_log)
    await save(store, "graph/propose_unsure", "Words only this override holds.")
    await reset(store, "graph/propose_unsure")

    assert guard_log.records == [
        ("dev-panel", "prompt_override", "override of graph/propose_unsure saved"),
        ("dev-panel", "prompt_override", "override of graph/propose_unsure reset"),
    ]


async def test_a_refused_save_and_a_reset_of_nothing_record_nothing(db):
    guard_log = RecordingGuardLog()
    store = new_store(db, guard_log)
    with pytest.raises(PromptRefused):
        await save(store, "watcher/recovery_line", "no placeholder")
    await reset(store, "graph/propose_unsure")
    assert guard_log.records == []


@pytest.mark.parametrize("name", ["graph/system", "notify/proposal", "notify/answer_approved"])
async def test_a_stored_override_of_a_prompt_no_longer_overridable_is_left_out(db, name):
    conn = await db.connect()
    text = prompts.file_text(name)
    row = (name, text, T0.isoformat())
    await conn.execute("INSERT INTO prompt_overrides VALUES (?, ?, ?)", row)
    await conn.commit()
    await conn.close()
    store = new_store(db)
    await store.load()
    assert store.overrides() == {}
    with pytest.raises(UnknownPrompt):
        await save(store, name, text)
