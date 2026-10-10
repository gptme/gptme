"""Responses input remains replayable when tool execution produced no result."""

import copy

import pytest

from gptme.llm.openai_responses import (
    MessageDict,
    _messages_dicts_to_responses_input,
    _messages_to_responses_input,
    _pair_missing_tool_results,
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


def test_output_before_its_call_is_dropped() -> None:
    """Output whose call_id appears later (not preceding) must be dropped.

    Sequence: output("x"), call("x"), output("x")
    The first output has no preceding call, so it is an orphan and must be
    dropped. The second output is properly paired and must be kept.
    Using seen_call_ids instead of matched_output_indices incorrectly retains
    the first output because "x" exists somewhere in the call list.
    """
    items = _pair_missing_tool_results(
        [
            {"type": "function_call_output", "call_id": "x", "output": "orphan"},
            {
                "type": "function_call",
                "call_id": "x",
                "name": "shell",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "x", "output": "proper"},
        ]
    )
    types = [i["type"] for i in items]
    assert types == ["function_call", "function_call_output"], (
        "orphaned output before its call must be dropped"
    )
    assert items[1]["output"] == "proper"


def test_extra_output_after_earlier_call_already_paired_is_dropped() -> None:
    """Duplicate output for a call that was already matched must be dropped."""
    items = _pair_missing_tool_results(
        [
            {
                "type": "function_call",
                "call_id": "x",
                "name": "shell",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "x", "output": "first"},
            {"type": "function_call_output", "call_id": "x", "output": "second"},
        ]
    )
    types = [i["type"] for i in items]
    assert types == ["function_call", "function_call_output"], (
        "extra output after an already-paired call must be dropped"
    )
    assert items[1]["output"] == "first"
