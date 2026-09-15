"""Real shell completions must keep an otherwise idle chat loop alive."""

import errno
import json
import os
import shlex
import sys
import time
from collections.abc import Generator
from pathlib import Path
from unittest.mock import Mock

import pytest

from gptme.chat import _run_chat_loop
from gptme.hooks import (
    HookRegistry,
    HookType,
    current_conversation_id,
    get_registry,
    register_hook,
    set_registry,
    trigger_hook,
)
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tools import base, shell
from gptme.tools.shell_background import (
    background_job_completion_hook,
    get_background_job,
    reset_background_jobs,
    start_background_job,
)


def _write_fifo(path: Path, data: str, timeout: float = 5.0) -> None:
    """Write to a FIFO without blocking the test suite if the reader never opens."""
    deadline = time.monotonic() + timeout
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno not in (errno.ENXIO, errno.EAGAIN):
                raise
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"FIFO writer timed out waiting for reader: {path}"
                ) from exc
            time.sleep(0.05)
    try:
        os.write(fd, data.encode())
    finally:
        os.close(fd)


@pytest.fixture
def shell_loop(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Use the shipped shell hooks without unrelated autonomous extensions."""
    registry = get_registry()
    set_registry(HookRegistry())
    shell.tool.register_hooks()
    reset_background_jobs()
    chat_module = sys.modules["gptme.chat"]
    monkeypatch.setattr(chat_module, "_start_auto_naming_thread", lambda *a: None)
    monkeypatch.setattr(base, "tool_format", "tool")
    monkeypatch.setenv("GPTME_WATCH_IDLE_MAX", "3")
    monkeypatch.setenv("GPTME_TRACK_TOKENS", "false")
    monkeypatch.setenv("GPTME_COSTS", "false")
    monkeypatch.setenv("GPTME_CHECK", "false")
    try:
        yield
    finally:
        reset_background_jobs()
        set_registry(registry)


@pytest.mark.skipif(os.name == "nt", reason="Requires a POSIX FIFO release gate")
def test_noninteractive_loop_receives_delayed_owned_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shell_loop: None
) -> None:
    """A plain assistant reply must not end the session before its job completes."""
    monkeypatch.chdir(tmp_path)
    foreign = LogManager(logdir=tmp_path / "foreign", lock=False)
    token = current_conversation_id.set(foreign.chat_id)
    try:
        foreign_job = start_background_job("printf FOREIGN_RESULT")
        foreign_job.process.wait(timeout=5)
        assert foreign_job._reader_thread is not None
        foreign_job._reader_thread.join(timeout=5)
        assert not foreign_job._reader_thread.is_alive()
    finally:
        current_conversation_id.reset(token)

    owner = LogManager(logdir=tmp_path / "owner", lock=False)
    release_gate = tmp_path / "release-job"
    os.mkfifo(release_gate)
    # The child cannot finish before the second reply, even on a busy worker.
    command = (
        f"read -r release < {shlex.quote(str(release_gate))}; "
        "sleep 0.1; printf OWN_RESULT"
    )
    replies = [
        Message(
            "assistant",
            "@shell(start-job): "
            + json.dumps({"command": command, "background": True}),
        ),
        Message("assistant", "The job is running."),
        Message("assistant", "The job finished successfully."),
    ]
    observed_prompts: list[list[Message]] = []

    def reply(messages: list[Message], *args: object, **kwargs: object) -> Message:
        observed_prompts.append(list(messages))
        assert len(observed_prompts) <= len(replies), "Unexpected extra model turn"
        if len(observed_prompts) == 2:
            job = get_background_job(1)
            assert job is not None and job.is_running(), (
                "Job must remain blocked until the second reply releases it"
            )
            _write_fifo(release_gate, "continue\n")
        return replies[len(observed_prompts) - 1]

    mock_reply = Mock(side_effect=reply)
    monkeypatch.setattr(sys.modules["gptme.chat"], "reply", mock_reply)
    token = current_conversation_id.set(owner.chat_id)
    try:
        _run_chat_loop(
            manager=owner,
            prompt_queue=[Message("user", "Start the work and report its result.")],
            stream=False,
            tool_format="tool",
            model="local/test",
            interactive=False,
            no_confirm=True,
        )
    finally:
        current_conversation_id.reset(token)

    assert mock_reply.call_count == 3, "Idle loop exited before consuming completion"
    completions = [
        msg
        for msg in owner.log.messages
        if msg.role == "system" and msg.content.startswith("Background shell job #")
    ]
    assert len(completions) == 1
    assert "```stdout\nOWN_RESULT\n```" in completions[0].content
    assert "exit code 0" in completions[0].content
    assert completions[0] in observed_prompts[2]
    assert not any("FOREIGN_RESULT" in msg.content for msg in owner.log.messages)
    # The owner did not steal or discard the other conversation's queued result.
    foreign_results = list(background_job_completion_hook(foreign, False, []))
    assert len(foreign_results) == 1
    assert "```stdout\nFOREIGN_RESULT\n```" in foreign_results[0].content
    assert list(background_job_completion_hook(foreign, False, [])) == []
    assert [m.content for m in owner.log.messages if m.role == "assistant"] == [
        m.content for m in replies
    ]


def test_loop_drains_before_generation_and_ignores_foreign_running_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shell_loop: None
) -> None:
    """Existing output enters the next step; another conversation cannot delay exit."""
    monkeypatch.chdir(tmp_path)
    foreign = LogManager(logdir=tmp_path / "foreign-running", lock=False)
    token = current_conversation_id.set(foreign.chat_id)
    try:
        foreign_job = start_background_job("sleep 30")
    finally:
        current_conversation_id.reset(token)

    owner = LogManager(logdir=tmp_path / "owner-ready", lock=False)
    token = current_conversation_id.set(owner.chat_id)
    try:
        own_job = start_background_job("printf READY_RESULT")
        own_job.process.wait(timeout=5)
        assert own_job._reader_thread is not None
        own_job._reader_thread.join(timeout=5)
        assert not own_job._reader_thread.is_alive()

        def reply(messages: list[Message], *args: object, **kwargs: object) -> Message:
            completions = [
                m for m in messages if m.content.startswith("Background shell job #")
            ]
            assert len(completions) == 1, "Completed output must precede generation"
            assert "```stdout\nREADY_RESULT\n```" in completions[0].content
            return Message("assistant", "The result is ready.")

        mock_reply = Mock(side_effect=reply)
        monkeypatch.setattr(sys.modules["gptme.chat"], "reply", mock_reply)
        _run_chat_loop(
            manager=owner,
            prompt_queue=[Message("user", "Report the result.")],
            stream=False,
            tool_format="tool",
            model="local/test",
            interactive=False,
            no_confirm=True,
        )
        assert mock_reply.call_count == 1
        assert foreign_job.is_running()
        assert list(background_job_completion_hook(owner, False, [])) == []
    finally:
        current_conversation_id.reset(token)


def test_idle_completion_preempts_lower_priority_loop_control(
    tmp_path: Path, shell_loop: None
) -> None:
    """Pending shell work must be handled before auto-reply or stuck detection."""
    manager = LogManager(logdir=tmp_path / "hook-order", lock=False)
    token = current_conversation_id.set(manager.chat_id)
    try:
        start_background_job("sleep 0.1; printf ORDERED_RESULT")
        lower_priority = Mock(
            side_effect=AssertionError("Auto-reply ran before completion")
        )
        register_hook(
            "test_auto_reply", HookType.LOOP_CONTINUE, lower_priority, priority=1000
        )
        messages = list(
            trigger_hook(
                HookType.LOOP_CONTINUE,
                manager=manager,
                interactive=False,
                prompt_queue=[],
                no_confirm=True,
            )
        )
        assert len(messages) == 1
        assert "```stdout\nORDERED_RESULT\n```" in messages[0].content
        lower_priority.assert_not_called()
    finally:
        current_conversation_id.reset(token)


@pytest.mark.skipif(os.name == "nt", reason="Requires a POSIX FIFO release gate")
def test_noninteractive_cli_receives_background_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shell_loop: None
) -> None:
    """Exercise real `gptme -n` parsing, startup, chat and shell without a provider."""
    from click.testing import CliRunner

    from gptme.cli.main import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path / "logs"))
    monkeypatch.setenv("GPTME_TELEMETRY_ENABLED", "false")
    release_gate = tmp_path / "cli-release"
    os.mkfifo(release_gate)
    command = (
        f"read -r release < {shlex.quote(str(release_gate))}; "
        "sleep 0.1; printf CLI_RESULT"
    )
    call_count = 0

    def reply(messages: list[Message], *args: object, **kwargs: object) -> Message:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return Message(
                "assistant",
                "@shell(cli-job): "
                + json.dumps({"command": command, "background": True}),
            )
        if call_count == 2:
            job = get_background_job(1)
            assert job is not None and job.is_running()
            _write_fifo(release_gate, "continue\n")
            return Message("assistant", "The command is running.")
        assert call_count == 3, "Unexpected extra model turn"
        completions = [
            message
            for message in messages
            if message.role == "system"
            and message.content.startswith("Background shell job #")
        ]
        assert len(completions) == 1
        assert "```stdout\nCLI_RESULT\n```" in completions[0].content
        return Message(
            "assistant",
            "Received CLI_RESULT; the command succeeded.\n\n```complete\n\n```",
        )

    monkeypatch.setattr(sys.modules["gptme.llm"], "_reply_after_hooks", reply)
    result = CliRunner().invoke(
        main,
        [
            "-n",
            "--no-stream",
            "--model",
            "local/test",
            "--tools",
            "shell",
            "--tool-format",
            "tool",
            "--name",
            "cli-completion",
            "--workspace",
            str(tmp_path),
            "Start a background command and report its completion.",
        ],
    )
    assert result.exit_code == 0, f"{result.output}\n{result.exception!r}"
    assert call_count == 3
    transcripts = list((tmp_path / "logs").glob("*/conversation.jsonl"))
    assert len(transcripts) == 1
    saved = [json.loads(line) for line in transcripts[0].read_text().splitlines()]
    assert any(
        message.get("content", "").startswith(
            "Received CLI_RESULT; the command succeeded."
        )
        for message in saved
    )
