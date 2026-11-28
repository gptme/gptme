"""Responses input remains replayable when tool execution produced no result."""

import copy
from typing import Any

import pytest

from gptme.llm.openai_responses import (
    MessageDict,
    _messages_dicts_to_responses_input,
    _messages_to_responses_input,
    _pair_missing_tool_results,
)
from gptme.message import Message, MessageMetadata
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


@pytest.mark.parametrize("message_format", ["gptme", "dict"])
def test_mid_conversation_system_messages_keep_instructions_prefix_stable(
    message_format: str,
) -> None:
    """Injected system notices must not rewrite ``instructions`` (prefix cache).

    Regression for gptme/gptme#4882: hidden ``<system_warning>`` / time notices
    were folded into ``instructions``, changing the top of the prompt each time
    one appeared and collapsing the provider's cache read to the static prefix.
    """
    base: list[tuple[Any, str]] = [
        ("system", "Static system prompt."),
        ("system", "Workspace context."),
        ("user", "Do the thing."),
        ("assistant", "Sure."),
    ]
    notice: tuple[Any, str] = (
        "system",
        "<system_warning>Retained log estimate: 1/2</system_warning>",
    )

    def convert(pairs: list[tuple[Any, str]]):
        if message_format == "gptme":
            return _messages_to_responses_input([Message(r, c) for r, c in pairs])
        return _messages_dicts_to_responses_input(
            [{"role": r, "content": c} for r, c in pairs]
        )

    before_instructions, before_items = convert(base)
    after_instructions, after_items = convert([*base, notice, ("user", "go on")])

    assert before_instructions == "Static system prompt.\n\nWorkspace context."
    assert after_instructions == before_instructions
    # Earlier items are an exact prefix of the later request's items.
    assert after_items[: len(before_items)] == before_items
    assert after_items[len(before_items)] == {
        "role": "developer",
        "content": notice[1],
    }


@pytest.mark.parametrize("tools", [[], None])
def test_tool_output_kept_as_text_without_tools(tools) -> None:
    """Without tools, a call_id tool result must reach the input as text.

    Autocompact summaries call ``reply(..., tools=[])``; the assistant's
    ``@tool(call_id)`` stays text, so the result has no function_call to pair
    with and used to be dropped as an orphan (gptme/gptme#5218 item 12).
    """
    from gptme.llm.llm_openai import _prepare_messages_for_responses_api

    output = "UNIQUE-TOOL-OUTPUT-42: 3 tests failed in test_foo.py"
    instructions, items, responses_tools = _prepare_messages_for_responses_api(
        [
            Message("system", "You are gptme."),
            Message("user", "run the tests"),
            Message("assistant", 'Running.\n@shell(call_abc): {"command": "pytest"}'),
            Message("system", output, call_id="call_abc"),
            Message("user", "Summarize the conversation so far."),
        ],
        "openai/gpt-5",
        tools,
    )
    assert responses_tools is None
    assert not any(i.get("type") == "function_call_output" for i in items)
    # Tool output must not gain instruction/developer priority.
    assert output not in (instructions or "")
    assert not any(
        i.get("role") == "developer" and output in str(i.get("content")) for i in items
    )
    result_items = [i for i in items if output in str(i.get("content"))]
    assert len(result_items) == 1, items
    assert result_items[0]["role"] == "user"
    assert result_items[0]["content"].startswith("Tool result:\n")


def test_native_replayed_call_keeps_its_result_without_tools() -> None:
    """A result paired with a replayed native call stays a function_call_output."""
    from gptme.llm.llm_openai import _prepare_messages_for_responses_api
    from gptme.llm.responses_phase import _digest

    content = 'Running.\n@shell(call_abc): {"command": "pytest"}'
    output = "UNIQUE-TOOL-OUTPUT-42: 3 tests failed in test_foo.py"
    metadata: MessageMetadata = {
        "responses_output": {
            "content_digest": _digest(content),
            "items": [
                {"type": "message", "text": "Running.\n"},
                {"type": "function_call", "call_id": "call_abc"},
            ],
        }
    }
    _, items, responses_tools = _prepare_messages_for_responses_api(
        [
            Message("system", "You are gptme."),
            Message("user", "run the tests"),
            Message("assistant", content, metadata=metadata),
            Message("system", output, call_id="call_abc"),
            Message("user", "Summarize the conversation so far."),
        ],
        "openai/gpt-5",
        [],
    )
    assert responses_tools is None
    outputs = [i for i in items if i.get("type") == "function_call_output"]
    assert [i["call_id"] for i in outputs] == ["call_abc"]
    assert outputs[0]["output"] == output
    assert not any("Tool result:" in str(i.get("content")) for i in items)


def test_extra_result_for_one_replayed_call_stays_as_text() -> None:
    """A second result for one call must reach the summary, not be dropped."""
    from gptme.llm.llm_openai import _prepare_messages_for_responses_api
    from gptme.llm.responses_phase import _digest

    content = 'Running.\n@shell(call_abc): {"command": "pytest"}'
    first = "first result"
    second = "SECOND-RESULT-42: more output"
    metadata: MessageMetadata = {
        "responses_output": {
            "content_digest": _digest(content),
            "items": [
                {"type": "message", "text": "Running.\n"},
                {"type": "function_call", "call_id": "call_abc"},
            ],
        }
    }
    _, items, _ = _prepare_messages_for_responses_api(
        [
            Message("system", "You are gptme."),
            Message("user", "run the tests"),
            Message("assistant", content, metadata=metadata),
            Message("system", first, call_id="call_abc"),
            Message("system", second, call_id="call_abc"),
            Message("user", "Summarize the conversation so far."),
        ],
        "openai/gpt-5",
        [],
    )
    outputs = [i for i in items if i.get("type") == "function_call_output"]
    assert [i["output"] for i in outputs] == [first]
    texts = [i for i in items if second in str(i.get("content"))]
    assert len(texts) == 1, items
    assert texts[0]["role"] == "user"
    assert texts[0]["content"] == f"Tool result:\n{second}"


@pytest.mark.parametrize("first_answered", [True, False])
def test_result_pairs_only_with_a_call_from_its_own_turn(first_answered: bool) -> None:
    """A reused call_id in a later text-only turn must not steal an earlier call."""
    from gptme.llm.llm_openai import _prepare_messages_for_responses_api
    from gptme.llm.responses_phase import _digest

    first_content = 'Running.\n@shell(call_x): {"command": "echo 1"}'
    second_content = '@shell(call_x): {"command": "echo 2"}'
    metadata: MessageMetadata = {
        "responses_output": {
            "content_digest": _digest(first_content),
            "items": [
                {"type": "message", "text": "Running.\n"},
                {"type": "function_call", "call_id": "call_x"},
            ],
        }
    }
    messages = [
        Message("system", "You are gptme."),
        Message("user", "run it"),
        Message("assistant", first_content, metadata=metadata),
        *(
            [Message("system", "first result", call_id="call_x")]
            if first_answered
            else []
        ),
        Message("assistant", second_content),
        Message("system", "SECOND-RESULT-42", call_id="call_x"),
        Message("user", "Summarize."),
    ]
    original_messages = copy.deepcopy(messages)
    _, items, _ = _prepare_messages_for_responses_api(messages, "openai/gpt-5", [])
    assert messages == original_messages
    outputs = [i for i in items if i.get("type") == "function_call_output"]
    assert [i["call_id"] for i in outputs] == ["call_x"]
    if first_answered:
        assert outputs[0]["output"] == "first result"
    else:
        assert "No tool result was recorded" in outputs[0]["output"]
    second = [i for i in items if "SECOND-RESULT-42" in str(i.get("content"))]
    assert len(second) == 1, items
    assert second[0]["role"] == "user"
    assert second[0]["content"] == "Tool result:\nSECOND-RESULT-42"
