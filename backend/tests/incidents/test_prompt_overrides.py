"""The dev panel's prompt overrides: checked like the loader, stored, and put in force (#42)."""

import logging
from datetime import UTC, datetime

import pytest

from bleep import prompts
from bleep.incidents.prompt_overrides import PromptOverrideStore, PromptRefused, UnknownPrompt
from bleep.store.db import Database

T0 = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
GITHUB_TOKEN = "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"


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
    store = PromptOverrideStore(db, clock=lambda: T0)
    file_text = prompts.load("watcher/recovery_line", logs="LOGS")

    await store.set("watcher/recovery_line", "Say in one line why it recovered: {logs}")
    assert prompts.load("watcher/recovery_line", logs="LOGS") == (
        "Say in one line why it recovered: LOGS"
    )
    assert store.overrides()["watcher/recovery_line"].updated_at == T0

    await store.reset("watcher/recovery_line")
    assert prompts.load("watcher/recovery_line", logs="LOGS") == file_text
    assert await stored(db) == {}


async def test_overrides_survive_a_restart(db):
    await PromptOverrideStore(db).set("graph/propose_unsure", "Be careful.")
    prompts.use_overrides({})

    await PromptOverrideStore(db).load()
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
    store = PromptOverrideStore(db)
    with pytest.raises(PromptRefused):
        await store.set("watcher/recovery_line", text)
    assert await stored(db) == {}
    assert "Why did it recover?" not in prompts.load("watcher/recovery_line", logs="x")


async def test_an_override_holding_a_secret_is_refused_without_quoting_it(db):
    store = PromptOverrideStore(db)
    with pytest.raises(PromptRefused) as refused:
        await store.set("graph/propose_unsure", f"Push with {GITHUB_TOKEN}")
    assert "ghp_" not in str(refused.value)
    assert await stored(db) == {}


@pytest.mark.parametrize("name", ["graph/nope", "../config", "evals/judge_system"])
async def test_only_the_apps_prompt_files_can_be_overridden(db, name):
    with pytest.raises(UnknownPrompt):
        await PromptOverrideStore(db).set(name, "anything")


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
    store = PromptOverrideStore(db)

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
    store = PromptOverrideStore(db)
    await store.reset("graph/propose_unsure")
    assert store.overrides() == {}


async def test_close_puts_every_file_back(db):
    store = PromptOverrideStore(db)
    await store.set("graph/propose_unsure", "Be careful.")
    store.close()
    assert prompts.load("graph/propose_unsure") == prompts.file_text("graph/propose_unsure")
