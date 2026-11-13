"""The watcher's explanation of a recovery is one call, so it does not ask Anthropic for prompt
caching (#546); the request body of a real `ChatAnthropic` over a mock transport is read."""

from test_manager import Rig, recover_shop

from claude_fake import claude_answering, sent_no_cache_control


async def test_the_recovery_explanation_does_not_send_cache_control(tmp_path):
    model, bodies = claude_answering("It ran out of memory. Then it restarted.")
    r = await Rig(tmp_path, model).boot()

    await recover_shop(r)

    [recovery] = await r.repo.recoveries()
    assert recovery.explanation == "It ran out of memory."  # the model's reply was used
    assert sent_no_cache_control(bodies)
