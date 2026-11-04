"""Batch stop classification must follow the chat producer's structured marker.

Messages use the real JSON stdout serializer; only malformed wire-shape tests
mutate its output. No test starts a provider or a child gptme session.
"""

from __future__ import annotations

import importlib
import json
import subprocess
from contextlib import redirect_stdout
from io import StringIO
from typing import TYPE_CHECKING, Any, Literal, cast

import pytest

from gptme.cli import cmd_batch
from gptme.constants import MAX_STEPS_STOP_PREFIX
from gptme.message import (
    Message,
    MessageMetadata,
    get_output_format,
    print_msg,
    set_output_format,
)

if TYPE_CHECKING:
    from pathlib import Path


def _message_stdout(*messages: Message) -> str:
    """Exercise print_msg's maintained JSON contract, restoring caller state."""
    previous_format = get_output_format()
    output = StringIO()
    try:
        set_output_format("json")
        with redirect_stdout(output):
            assert print_msg(list(messages)) == len(messages)
    finally:
        set_output_format(previous_format)
    return output.getvalue()


def _summarize(stdout: str, *, returncode: int = 0, stderr: str = "") -> dict[str, Any]:
    return cmd_batch._summarize_child_output(
        index=3,
        prompt="inspect the issue",
        duration_s=1.23456,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


@pytest.mark.parametrize("role", ["user", "assistant", "system"])
def test_stop_phrase_in_prose_is_not_a_stop(
    role: Literal["user", "assistant", "system"],
) -> None:
    stdout = _message_stdout(
        Message(role, f"The issue quotes '{MAX_STEPS_STOP_PREFIX} (1)'.")
    )

    assert _summarize(stdout)["exit_reason"] == "done"


@pytest.mark.parametrize("content", ["Step budget exhausted; returning control.", ""])
def test_structured_stop_does_not_depend_on_display_text(content: str) -> None:
    stdout = _message_stdout(
        Message("system", content, metadata={"max_steps_stop": True})
    )

    assert _summarize(stdout)["exit_reason"] == "max_turns"


@pytest.mark.parametrize("returncode", [1, 7])
def test_child_error_wins_over_a_structured_stop(returncode: int) -> None:
    stdout = _message_stdout(
        Message(
            "system",
            f"{MAX_STEPS_STOP_PREFIX} (1)",
            metadata={"max_steps_stop": True},
        ),
        Message("system", "A later hook printed a status line."),
    )

    record = _summarize(
        stdout,
        returncode=returncode,
        stderr="Earlier diagnostic\nfinal child error\n\n",
    )

    # Check diagnostics independently, so a reason mismatch cannot mask loss.
    assert record["returncode"] == returncode
    assert record["error"] == "final child error"
    assert record["exit_reason"] == "error"


@pytest.mark.parametrize(
    "value",
    [False, None, "true", "True", "false", "", 0, 1, -1, 0.0, 1.0, [], {}],
)
def test_only_boolean_true_is_a_stop(value: Any) -> None:
    stdout = _message_stdout(
        Message(
            "system",
            f"{MAX_STEPS_STOP_PREFIX} (1)",
            # Deliberately violate the TypedDict to exercise runtime input.
            metadata=cast(MessageMetadata, {"max_steps_stop": value}),
        )
    )

    assert _summarize(stdout)["exit_reason"] == "done"


@pytest.mark.parametrize("metadata", [None, {}, {"tool": "shell"}])
def test_missing_marker_is_not_a_stop(metadata: MessageMetadata | None) -> None:
    stdout = _message_stdout(
        Message("system", f"{MAX_STEPS_STOP_PREFIX} (1)", metadata=metadata)
    )

    assert _summarize(stdout)["exit_reason"] == "done"


@pytest.mark.parametrize("metadata", [None, False, 1, "max_steps_stop", [], [True]])
def test_malformed_wire_metadata_is_not_a_stop(metadata: Any) -> None:
    event = json.loads(
        _message_stdout(Message("system", f"{MAX_STEPS_STOP_PREFIX} (1)"))
    )
    # print_msg emits metadata dictionaries, but child stdout is untrusted.
    event["metadata"] = metadata

    assert _summarize(json.dumps(event) + "\n")["exit_reason"] == "done"


@pytest.mark.parametrize("role", ["user", "assistant"])
def test_marker_on_the_wrong_role_is_not_a_stop(
    role: Literal["user", "assistant"],
) -> None:
    stdout = _message_stdout(
        Message(
            role,
            f"{MAX_STEPS_STOP_PREFIX} (1)",
            metadata={"max_steps_stop": True},
        )
    )

    assert _summarize(stdout)["exit_reason"] == "done"


@pytest.mark.parametrize("event_type", [None, "status", "usage"])
def test_marker_on_non_message_event_is_not_a_stop(event_type: str | None) -> None:
    event = json.loads(
        _message_stdout(
            Message(
                "system",
                f"{MAX_STEPS_STOP_PREFIX} (1)",
                metadata={"max_steps_stop": True},
            )
        )
    )
    if event_type is None:
        del event["type"]
    else:
        event["type"] = event_type

    assert _summarize(json.dumps(event) + "\n")["exit_reason"] == "done"


def test_native_tool_result_marker_is_not_a_stop() -> None:
    # The chat loop excludes system messages with call_id: they are tool results.
    stdout = _message_stdout(
        Message(
            "system",
            f"{MAX_STEPS_STOP_PREFIX} (1)",
            call_id="call_shell_1",
            metadata={"max_steps_stop": True},
        )
    )
    assert json.loads(stdout)["call_id"] == "call_shell_1"

    assert _summarize(stdout)["exit_reason"] == "done"


@pytest.mark.parametrize("stopped", [False, True])
def test_mixed_stdout_preserves_usage_and_tool_counts(stopped: bool) -> None:
    stdout = (
        "startup diagnostic\n\n{malformed json\n[]\nnull\n42\n"
        + _message_stdout(
            Message(
                "user",
                f"Explain '{MAX_STEPS_STOP_PREFIX}'.",
                metadata={"usage": {"input_tokens": 10}},
            ),
            Message(
                "assistant",
                "```shell\npwd\n```\n\n```save result.txt\nhello\n```",
                metadata={
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 3,
                        "cache_read_tokens": 4,
                        "cache_creation_tokens": 5,
                    }
                },
            ),
            Message(
                "system", "Tool finished.", call_id="call_1", metadata={"tool": "shell"}
            ),
        )
        + "trailing non-JSON output\n"
    )
    if stopped:
        stdout += _message_stdout(
            Message(
                "system", "Step budget exhausted.", metadata={"max_steps_stop": True}
            )
        )
    stdout += _message_stdout(
        Message("assistant", "Finished.", metadata={"usage": {"output_tokens": 6}}),
        Message("system", "Post-process hook finished."),
    )

    record = _summarize(stdout)

    assert record == {
        "index": 3,
        "prompt": "inspect the issue",
        "duration_s": 1.235,
        "tokens": 48,
        "tool_calls": 2,
        "exit_reason": "max_turns" if stopped else "done",
    }


@pytest.mark.parametrize("bytes_output", [False, True])
def test_timeout_wins_even_when_partial_stdout_contains_stop(
    monkeypatch: pytest.MonkeyPatch, bytes_output: bool
) -> None:
    stdout = _message_stdout(
        Message(
            "system",
            f"{MAX_STEPS_STOP_PREFIX} (1)",
            metadata={"max_steps_stop": True, "usage": {"output_tokens": 9}},
        )
    )

    def timeout_run(cmd: list[str], **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(
            cmd,
            kwargs["timeout"],
            output=stdout.encode() if bytes_output else stdout,
            stderr="partial child diagnostic",
        )

    monkeypatch.setattr(cmd_batch.subprocess, "run", timeout_run)
    record = cmd_batch._run_one_prompt(
        index=3, prompt="inspect the issue", model=None, max_turns=1, timeout=2.5
    )

    assert record["exit_reason"] == "timeout"
    assert record["returncode"] is None
    assert record["error"] == "timed out after 2.5s"
    assert record["tokens"] == 0
    assert record["tool_calls"] == 0


def test_chat_producer_stop_survives_reload_and_json_reduction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gptme.logmanager import LogManager

    chat = importlib.import_module("gptme.chat")
    manager = LogManager([Message("user", "run tools")], logdir=tmp_path / "chat")
    monkeypatch.setenv("GPTME_MAX_STEPS", "1")
    monkeypatch.setattr(
        chat, "step", lambda *_a, **_k: iter([Message("assistant", "working")])
    )
    monkeypatch.setattr(chat, "trigger_hook", lambda *_a, **_k: [])
    monkeypatch.setattr(chat, "_run_post_tool_compaction", lambda *_a: False)
    monkeypatch.setattr(chat, "get_default_model", lambda: None)

    chat._process_message_conversation(manager, False, "markdown", None)

    stop = manager.log[-1]
    assert stop.role == "system"
    assert not stop.call_id
    assert stop.metadata == {"max_steps_stop": True}
    manager.write()
    reloaded = LogManager.load(manager.logdir)
    stdout = _message_stdout(*reloaded.log.messages)
    assert json.loads(stdout.splitlines()[-1])["metadata"]["max_steps_stop"] is True
    assert _summarize(stdout)["exit_reason"] == "max_turns"
