"""Completion reports must survive native complete's empty parameter schema."""

import json
from pathlib import Path

import pytest

from gptme.message import Message
from gptme.tools.base import ToolUse
from gptme.tools.subagent.types import Subagent


def native_call(tool: str, call_id: str = "call_complete") -> str:
    # This is the persisted native Message representation, not a tool_calls field.
    return f"@{tool}({call_id}): {{}}"


def read_result(tmp_path: Path, messages: list[Message], fork_count: int = 0) -> str:
    logdir = tmp_path / "child"
    logdir.mkdir()
    (logdir / "conversation.jsonl").write_text(
        "".join(json.dumps(message.to_dict()) + "\n" for message in messages)
    )
    child = Subagent(
        agent_id="summary-regression",
        prompt="task",
        thread=None,
        logdir=logdir,
        model=None,
        fork_message_count=fork_count,
    )
    result = child._read_log()
    assert result.status == "success"
    assert isinstance(result.result, str)
    return result.result.split("\n\nFull log:")[0]


@pytest.mark.parametrize("same_message", [False, True])
def test_native_complete_recovers_final_prose(
    tmp_path: Path, same_message: bool
) -> None:
    report = "PR: https://github.com/gptme/gptme/pull/123\nTests passed; CI pending."
    completion = native_call("complete")
    parsed = list(ToolUse.iter_from_content(completion, tool_format_override="tool"))
    assert parsed[0].content is None
    assert parsed[0].kwargs == {}
    messages = [Message("assistant", "old report"), Message("user", "new task")]
    if same_message:
        messages.append(Message("assistant", report + "\n\n" + completion))
    else:
        messages.extend(
            [Message("assistant", report), Message("assistant", completion)]
        )
    messages.extend(
        [
            Message("system", "Task complete. Autonomous session finished."),
            Message("system", "Token usage warning"),
        ]
    )
    assert read_result(tmp_path, messages) == report


@pytest.mark.parametrize(
    "barrier",
    [
        Message("user", "new task"),
        Message("assistant", native_call("shell", "call_shell")),
        Message("assistant", "Running tests.\n```shell\npytest\n```"),
        Message("assistant", "```complete\nold explicit report\n```"),
    ],
)
def test_native_complete_does_not_reuse_older_report(
    tmp_path: Path, barrier: Message
) -> None:
    messages = [
        Message("assistant", "old report"),
        barrier,
        Message("system", "tool output or warning"),
        Message("assistant", native_call("complete")),
    ]
    assert read_result(tmp_path, messages) == "Task completed (no summary provided)"


def test_native_complete_does_not_reuse_forked_parent_report(tmp_path: Path) -> None:
    assert (
        read_result(
            tmp_path,
            [
                Message("assistant", "parent report"),
                Message("assistant", native_call("complete")),
            ],
            fork_count=1,
        )
        == "Task completed (no summary provided)"
    )


@pytest.mark.parametrize(
    "completion", ["```complete\nexplicit report\n```", "```complete\n\n```"]
)
def test_textual_complete_keeps_existing_summary_semantics(
    tmp_path: Path, completion: str
) -> None:
    result = read_result(
        tmp_path,
        [Message("assistant", "preceding prose"), Message("assistant", completion)],
    )
    assert result == (
        "explicit report"
        if "explicit report" in completion
        else "Task completed (no summary provided)"
    )
