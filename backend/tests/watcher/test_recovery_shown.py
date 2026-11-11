"""The recovery lines Bleep writes in place of the cheap model's, stored in English and shown in
the picked language (#455)."""

import asyncio

from langchain_core.messages import AIMessage
from test_detector import DownModel
from test_manager import Rig, held_loop_asking, lines, priced_reply, recover_shop

from bleep import prompts
from bleep.incidents.shown import recovery_in
from bleep.llm.guard import ModelGuard
from bleep.schemas.settings import Budgets
from bleep.watcher import detector, manager
from fakes import FakeChatModel

CAP_SPENT_DE = "Keine Erklärung: Die tägliche Kostengrenze ist aufgebraucht."


async def test_a_line_in_place_of_the_models_is_stored_in_english_and_shown_in_german(tmp_path):
    stored = {}
    for language in ("en", "de"):
        model = FakeChatModel(script=[priced_reply("It ran out of memory.")])
        r = await Rig(tmp_path / language, model, Budgets(daily_cost_usd=0.5)).boot()
        r.language = language
        await r.ledger.add("inc-1", "openai/gpt-6-luna", 1000, 100, 0.5)
        await recover_shop(r)
        [stored[language]] = await r.repo.recoveries()

    assert stored["de"].explanation == stored["en"].explanation == manager.CAP_SPENT_EXPLANATION
    assert stored["de"].explanation_shown == manager.CAP_SPENT_SHOWN
    assert recovery_in(stored["de"], "de").explanation == CAP_SPENT_DE
    assert recovery_in(stored["en"], "en").explanation == manager.CAP_SPENT_EXPLANATION


async def test_no_logs_and_a_failed_model_keep_their_templates(tmp_path):
    r = await Rig(tmp_path / "logs", FakeChatModel(script=[])).boot()
    r.engine.log_text = {}
    await recover_shop(r)
    failing = await Rig(tmp_path / "failing", FakeChatModel(script=[])).boot()
    await recover_shop(failing)

    [no_logs] = await r.repo.recoveries()
    [failed] = await failing.repo.recoveries()
    assert (no_logs.explanation, no_logs.explanation_shown) == (
        detector.NO_LOGS_LINE,
        detector.NO_LOGS,
    )
    assert recovery_in(no_logs, "de").explanation == (
        "Es gab keine Logs, die den Neustart erklären könnten."
    )
    assert failed.explanation_shown == manager.NO_EXPLANATION_SHOWN
    assert recovery_in(failed, "de").explanation == (
        "Für diesen Neustart ließ sich keine Erklärung erstellen."
    )


async def test_a_death_in_a_held_loop_is_said_in_german(tmp_path):
    model = lines(10)
    r = await Rig(tmp_path, model).boot()
    await held_loop_asking(r, model)

    held = [e for e in await r.repo.recoveries() if e.explanation_shown is not None]

    assert held and {e.explanation for e in held} == {manager.HELD_LOOP_EXPLANATION}
    assert {recovery_in(e, "de").explanation for e in held} == {
        "Der Container ist in seiner Absturzschleife wieder ausgefallen, und seine Neustart-Regel "
        "hat ihn zurückgeholt."
    }


async def test_the_models_own_line_is_shown_as_it_wrote_it(tmp_path):
    r = await Rig(tmp_path, FakeChatModel(script=[AIMessage("It ran out of memory.")])).boot()
    r.language = "de"
    await recover_shop(r)

    [recovery] = await r.repo.recoveries()

    assert recovery.explanation_shown is None
    assert recovery_in(recovery, "de").explanation == "It ran out of memory."


async def test_a_model_that_cannot_be_reached_gives_the_plain_line_and_its_template():
    guard = ModelGuard(1, retries=0, sleep=lambda _: asyncio.sleep(0))

    line = await detector.recovery_line(DownModel(script=[]), "OOM killed", guard=guard)
    english = await detector.explain_recovery(DownModel(script=[]), "OOM killed", guard=guard)

    assert line == detector.MODEL_DOWN
    assert english == detector.MODEL_DOWN_LINE == prompts.show(line, "en")
    assert prompts.show(line, "de") == (
        "Der Container ist ausgefallen, und seine Neustart-Regel hat ihn zurückgeholt."
    )
