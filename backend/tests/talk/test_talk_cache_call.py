"""A chat answer is one call, so it does not ask Anthropic for prompt caching (#546); the request
body of a real `ChatAnthropic` over a mock transport is read."""

import json

from test_conversation import FakeState, no_sleep

from bleep.talk.conversation import Talk
from claude_fake import claude_answering, sent_no_cache_control


async def test_a_chat_answer_does_not_send_cache_control():
    model, bodies = claude_answering(json.dumps({"kind": "question", "answer": "All fine."}))
    talk = Talk(FakeState(), lambda role: model, sleep=no_sleep)

    reply = await talk.reply("how do the services look?", surface="panel")

    assert reply.answer == "All fine."  # the model's reply was used
    assert sent_no_cache_control(bodies)
