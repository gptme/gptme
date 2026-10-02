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
