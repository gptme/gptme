"""Responses input remains replayable when tool execution produced no result."""

import copy

import pytest

from gptme.llm.openai_responses import (
    MessageDict,
    _messages_dicts_to_responses_input,
    _messages_to_responses_input,
)
from gptme.message import Message
from gptme.tools import ToolUse


@pytest.mark.parametrize("completed", [[], ["call_A"], ["call_A", "call_B"]])
@pytest.mark.parametrize("message_format", ["gptme", "dict"])
def test_missing_tool_results_are_paired_without_replacing_real_results(
    completed: list[str], message_format: str
) -> None:
    if message_format == "gptme":
        messages = [
            Message("system", "Be concise."),
            Message("user", "Run two commands."),
            Message(
                "assistant",
                '@shell(call_A): {"command": "echo A"}\n'
                '@shell(call_B): {"command": "echo B"}',
            ),
            *[Message("system", f"result {cid}", call_id=cid) for cid in completed],
            Message("user", "No tool call detected in last message. Continue."),
        ]
        original_messages = copy.deepcopy(messages)
        instructions, items = _messages_to_responses_input(messages)
        assert messages == original_messages
    else:
        messages_dicts: list[MessageDict] = [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Run two commands."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": cid,
                        "type": "function",
                        "function": {"name": "shell", "arguments": "{}"},
                    }
                    for cid in ("call_A", "call_B")
                ],
            },
            *[
                MessageDict(role="tool", content=f"result {cid}", tool_call_id=cid)
                for cid in completed
            ],
            {"role": "user", "content": "Continue."},
        ]
        original_dicts = copy.deepcopy(messages_dicts)
        instructions, items = _messages_dicts_to_responses_input(messages_dicts)
        assert messages_dicts == original_dicts

    assert instructions == "Be concise."
    calls = [item for item in items if item.get("type") == "function_call"]
    outputs = [item for item in items if item.get("type") == "function_call_output"]
    assert [item["call_id"] for item in calls] == ["call_A", "call_B"]
    assert len(outputs) == 2
    for call in calls:
        output = next(item for item in outputs if item["call_id"] == call["call_id"])
        assert items.index(call) < items.index(output) < len(items) - 1
        if call["call_id"] in completed:
            assert output["output"] == f"result {call['call_id']}"
        else:
            assert "No tool result was recorded" in output["output"]


def test_reused_call_id_does_not_hide_a_later_orphaned_call() -> None:
    _, items = _messages_to_responses_input(
        [
            Message("user", "Run twice."),
            Message("assistant", '@shell(call_dup): {"command": "echo 1"}'),
            Message("system", "result 1", call_id="call_dup"),
            Message("assistant", '@shell(call_dup): {"command": "echo 2"}'),
            Message("user", "Continue."),
        ]
    )
    types = [i["type"] for i in items if i.get("type", "message") != "message"]
    assert types == [
        "function_call",
        "function_call_output",
        "function_call",
        "function_call_output",
    ]
    outputs = [i for i in items if i.get("type") == "function_call_output"]
    assert outputs[0]["output"] == "result 1"
    assert "No tool result was recorded" in outputs[1]["output"]


def test_reused_call_id_result_pairs_with_nearest_preceding_call() -> None:
    _, items = _messages_to_responses_input(
        [
            Message("user", "Run twice."),
            Message("assistant", '@shell(call_dup): {"command": "echo 1"}'),
            Message("assistant", '@shell(call_dup): {"command": "echo 2"}'),
            Message("system", "result 2", call_id="call_dup"),
            Message("user", "Continue."),
        ]
    )
    typed = [i for i in items if i.get("type", "message") != "message"]
    assert [i["type"] for i in typed] == [
        "function_call",
        "function_call_output",
        "function_call",
        "function_call_output",
    ]
    assert "No tool result was recorded" in typed[1]["output"]
    assert typed[2]["arguments"] == '{"command": "echo 2"}'
    assert typed[3]["output"] == "result 2"


def test_markdown_execution_can_miss_a_call_that_responses_replay_recognizes() -> None:
    content = '@shell(call_missing): {"command": "echo hello"}'
    assert list(ToolUse.iter_from_content(content, "markdown")) == []
    _, items = _messages_to_responses_input(
        [
            Message("assistant", content),
            Message("user", "No tool call detected in last message. Continue."),
        ]
    )
    assert items[0]["type"] == "function_call"
    assert items[1]["type"] == "function_call_output"
    assert items[1]["call_id"] == "call_missing"
    assert items[2]["role"] == "user"


def test_orphaned_tool_result_without_preceding_call_is_dropped() -> None:
    """A function_call_output with no preceding function_call is dropped.

    This happens when the assistant message containing the tool call was skipped
    (e.g. due to a corrupt timestamp in the log, as in gptme#4182), but the
    tool result message survived. Passing the orphaned result to the Responses
    API would cause a 400 error.
    """
    # call_orphan has a result but no preceding call in the conversation
    _, items = _messages_to_responses_input(
        [
            Message("user", "Do something."),
            Message("system", "orphaned result", call_id="call_orphan"),
            Message("user", "Continue."),
        ]
    )
    types = [i.get("type", i.get("role")) for i in items]
    # The orphaned tool result must not appear as function_call_output
    assert "function_call_output" not in types
    assert all(i.get("call_id") != "call_orphan" for i in items)
    # Tool output must not be promoted to a user message.
    user_contents = [i.get("content", "") for i in items if i.get("role") == "user"]
    assert all("orphaned result" not in c for c in user_contents)


def test_reused_call_id_does_not_keep_result_after_call_was_consumed() -> None:
    """Each call occurrence can pair with at most one result."""
    _, items = _messages_to_responses_input(
        [
            Message("user", "Run once."),
            Message("assistant", '@shell(call_dup): {"command": "echo 1"}'),
            Message("system", "result 1", call_id="call_dup"),
            Message("system", "orphaned result 2", call_id="call_dup"),
            Message("user", "Continue."),
        ]
    )
    outputs = [i for i in items if i.get("type") == "function_call_output"]
    assert [i["output"] for i in outputs] == ["result 1"]


def test_result_before_later_same_id_call_is_orphaned() -> None:
    """A result that precedes any call with its call_id is orphaned and dropped.

    A global 'seen_calls' set would wrongly keep this result because the later
    call adds the ID to the set before the second pass. The positional check
    ensures only calls that actually precede the result count.
    """
    _, items = _messages_to_responses_input(
        [
            Message("user", "Run it."),
            Message("system", "early result", call_id="call_dup"),
            Message("assistant", '@shell(call_dup): {"command": "echo 2"}'),
            Message("user", "Continue."),
        ]
    )
    typed = [
        i for i in items if i.get("type") in ("function_call", "function_call_output")
    ]
    # The orphaned result (no preceding call) must be dropped;
    # the later call must be paired with a synthetic result
    assert [i["type"] for i in typed] == ["function_call", "function_call_output"]
    assert typed[0]["call_id"] == "call_dup"
    assert "No tool result was recorded" in typed[1]["output"]
    user_contents = [i.get("content", "") for i in items if i.get("role") == "user"]
    assert all("early result" not in c for c in user_contents)


def test_orphaned_result_dropped_while_valid_pairs_kept() -> None:
    """Dropping an orphaned result must not disturb valid call/result pairs."""
    _, items = _messages_to_responses_input(
        [
            Message("user", "Two calls, one orphan."),
            Message("assistant", '@shell(call_good): {"command": "echo ok"}'),
            Message("system", "ok", call_id="call_good"),
            Message("system", "orphaned", call_id="call_orphan"),
            Message("user", "Done."),
        ]
    )
    typed = [i for i in items if i.get("type", "message") != "message"]
    assert [i["type"] for i in typed] == ["function_call", "function_call_output"]
    assert typed[0]["call_id"] == "call_good"
    assert typed[1]["output"] == "ok"
