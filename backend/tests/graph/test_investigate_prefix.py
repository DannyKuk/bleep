"""The prompt cache (#529) reads back a prefix only when it is byte for byte what the last call
sent: the tool list, the system prompt and every earlier message must not change within a run."""

from langchain_core.utils.function_calling import convert_to_openai_tool
from test_investigate import DONE, asks, call, run, setup


def tool_schemas(model) -> list[dict]:
    return [convert_to_openai_tool(t) for t in model.bound_tools]


def as_sent(messages) -> list[tuple[str, str, str]]:
    return [
        (type(m).__name__, str(m.content), repr(getattr(m, "tool_calls", ""))) for m in messages
    ]


async def test_each_call_of_a_run_sends_the_calls_before_it_unchanged(tmp_path):
    script = [asks(call("list_containers", 1)), asks(call("list_containers", 2)), DONE]
    env, state = await setup(tmp_path, script)

    await run(env, state)

    sent = env.models.model.seen
    assert len(sent) == 3
    for earlier, later in zip(sent, sent[1:], strict=False):
        assert as_sent(later[: len(earlier)]) == as_sent(earlier)


async def test_the_second_round_keeps_the_first_rounds_prefix_and_its_tool_list(tmp_path):
    env, state = await setup(tmp_path, [asks(call("list_containers", 1)), DONE, DONE])
    first = await run(env, state)
    first_tools = tool_schemas(env.models.model)
    state = state | first | {"messages": first["messages"]}

    await run(env, state)

    sent = env.models.model.seen
    assert as_sent(sent[2][: len(sent[1])]) == as_sent(sent[1])
    assert tool_schemas(env.models.model) == first_tools


async def test_the_system_prompt_and_the_tools_hold_no_time_or_id(tmp_path):
    env, state = await setup(tmp_path, [DONE])

    await run(env, state)

    [sent] = env.models.model.seen
    system = str(sent[0].content)
    assert state["incident_id"] not in system
    assert str(state["context"].at.year) not in system
    assert state["incident_id"] not in repr(tool_schemas(env.models.model))
