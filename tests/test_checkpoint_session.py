"""Tests for durable conversation checkpoints and their CLI."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING
from unittest.mock import ANY, patch

import pytest
from click.testing import CliRunner

from gptme.checkpoint_session import (
    ConversationCheckpointError,
    checkpoint_resume_prompt,
    default_summary,
    list_conversation_checkpoints,
    load_conversation_checkpoint,
    recent_tool_calls,
    save_conversation_checkpoint,
    working_tree_changes,
)
from gptme.cli.cmd_checkpoint_session import checkpoint_session
from gptme.commands.base import CommandContext
from gptme.commands.session_checkpoint import cmd_session_checkpoint
from gptme.logmanager import LogManager
from gptme.message import Message

if TYPE_CHECKING:
    from pathlib import Path


def _make_session(logs_dir: Path, name: str = "test-conversation") -> Path:
    logdir = logs_dir / name
    logdir.mkdir(parents=True)
    messages = [
        Message("user", "Refactor the API client without losing the current plan."),
        Message(
            "assistant",
            "I'll inspect it.\n\n```shell\ngit status --short\n```\n\n"
            "Then save the change.\n\n```save gptme/api.py\nprint('ok')\n```",
            metadata={"model": "openai/gpt-5"},
        ),
        Message("system", "Saved gptme/api.py"),
        Message(
            "assistant", "The implementation is ready; run the focused tests next."
        ),
    ]
    (logdir / "conversation.jsonl").write_text(
        "".join(json.dumps(message.to_dict()) + "\n" for message in messages),
        encoding="utf-8",
    )
    (logdir / "config.toml").write_text(
        f'[chat]\nname = "{name}"\nworkspace = "{logdir}"\n', encoding="utf-8"
    )
    return logdir


@pytest.fixture()
def logs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "logs"
    path.mkdir()
    monkeypatch.setenv("GPTME_LOGS_HOME", str(path))
    monkeypatch.setenv("GPTME_TIKTOKEN_TIMEOUT", "0")
    return path


def test_save_list_load_and_resume_round_trip(logs_dir: Path) -> None:
    logdir = _make_session(logs_dir)

    checkpoint, path = save_conversation_checkpoint(
        logdir,
        "before-api-refactor",
        model_limit=200_000,
    )

    assert path == logdir / "checkpoints" / "before-api-refactor.json"
    assert checkpoint.version == 1
    assert checkpoint.conversation_id == "test-conversation"
    assert checkpoint.message_count == 4
    assert checkpoint.model == "openai/gpt-5"
    assert checkpoint.context_boundary.total_tokens > 0
    assert checkpoint.context_boundary.model_limit == 200_000
    assert checkpoint.context_boundary.pct_used is not None
    assert "Original task" in checkpoint.summary
    assert "print('ok')" not in checkpoint.summary
    assert "[tool call: save]" in checkpoint.summary
    assert [call.tool for call in checkpoint.last_tool_calls] == ["shell", "save"]
    assert checkpoint.last_tool_calls[-1].file == "gptme/api.py"

    assert list_conversation_checkpoints(logdir) == [checkpoint]
    assert load_conversation_checkpoint(logdir, checkpoint.label) == checkpoint

    prompt = checkpoint_resume_prompt(checkpoint)
    assert prompt.startswith("<<RESUMED SESSION>>")
    assert "[CHECKPOINT: before-api-refactor]" in prompt
    assert "## Recent tool calls" in prompt
    assert "Verify the working tree" in prompt


def test_save_infers_known_model_limit(logs_dir: Path) -> None:
    logdir = _make_session(logs_dir)

    checkpoint, _ = save_conversation_checkpoint(logdir, "model-limit")

    assert checkpoint.context_boundary.model_limit is not None
    assert checkpoint.context_boundary.pct_used is not None


def test_save_refuses_duplicate_without_overwrite(logs_dir: Path) -> None:
    logdir = _make_session(logs_dir)
    save_conversation_checkpoint(logdir, "milestone")

    with pytest.raises(ConversationCheckpointError, match="already exists"):
        save_conversation_checkpoint(logdir, "milestone")

    replaced, _ = save_conversation_checkpoint(
        logdir, "milestone", summary="Replacement", overwrite=True
    )
    assert replaced.summary == "Replacement"


def test_concurrent_save_without_overwrite_has_one_winner(logs_dir: Path) -> None:
    logdir = _make_session(logs_dir)
    barrier = threading.Barrier(2)
    real_link = os.link

    def synchronized_link(source: Path, destination: Path) -> None:
        barrier.wait(timeout=5)
        real_link(source, destination)

    def save(summary: str) -> str:
        checkpoint, _ = save_conversation_checkpoint(
            logdir, "milestone", summary=summary
        )
        return checkpoint.summary

    with (
        patch("gptme.checkpoint_session.os.link", side_effect=synchronized_link),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        futures = [executor.submit(save, summary) for summary in ("first", "second")]
        outcomes: list[str | ConversationCheckpointError] = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except ConversationCheckpointError as exc:
                outcomes.append(exc)

    successes = [item for item in outcomes if isinstance(item, str)]
    failures = [
        item for item in outcomes if isinstance(item, ConversationCheckpointError)
    ]
    assert len(successes) == 1
    assert len(failures) == 1
    assert "already exists" in str(failures[0])
    assert load_conversation_checkpoint(logdir, "milestone").summary == successes[0]


@pytest.mark.parametrize("label", ["../escape", "two words", "", "/absolute"])
def test_save_rejects_unsafe_labels(logs_dir: Path, label: str) -> None:
    logdir = _make_session(logs_dir)
    with pytest.raises(ConversationCheckpointError, match="labels must"):
        save_conversation_checkpoint(logdir, label)


def test_list_skips_corrupt_checkpoint(logs_dir: Path) -> None:
    logdir = _make_session(logs_dir)
    saved, _ = save_conversation_checkpoint(logdir, "good")
    (logdir / "checkpoints" / "broken.json").write_text("not json")

    assert list_conversation_checkpoints(logdir) == [saved]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("message_count", "4"),
        ("context_boundary.total_tokens", "100"),
        ("context_boundary.model_limit", False),
        ("context_boundary.pct_used", "0.5"),
        ("last_tool_calls", [{"tool": 1, "description": "shell"}]),
        ("file_changes", [{"path": "x", "action": [], "lines": None}]),
    ],
)
def test_list_skips_checkpoint_with_invalid_field_types(
    logs_dir: Path, field: str, value: object
) -> None:
    logdir = _make_session(logs_dir)
    saved, _ = save_conversation_checkpoint(logdir, "good")
    data = saved.to_dict()
    data["label"] = "broken"
    target = data
    parts = field.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    (logdir / "checkpoints" / "broken.json").write_text(json.dumps(data))

    assert list_conversation_checkpoints(logdir) == [saved]


def test_load_rejects_unknown_schema_version(logs_dir: Path) -> None:
    logdir = _make_session(logs_dir)
    _, path = save_conversation_checkpoint(logdir, "future")
    data = json.loads(path.read_text())
    data["version"] = 99
    path.write_text(json.dumps(data))

    with pytest.raises(ConversationCheckpointError, match="Unsupported"):
        load_conversation_checkpoint(logdir, "future")


def test_recent_native_tool_calls_do_not_merge() -> None:
    content = (
        '@shell(call-1): {"command": "echo 1"}\n@shell(call-2): {"command": "echo 2"}'
    )

    calls = recent_tool_calls([Message("assistant", content)])

    assert [call.command for call in calls] == ["echo 1", "echo 2"]


def test_ordinary_code_fences_are_preserved_in_summary() -> None:
    content = "Use this implementation:\n\n```python\nprint('keep me')\n```"

    summary = default_summary([Message("user", content)])

    assert "```python" in summary
    assert "print('keep me')" in summary
    assert recent_tool_calls([Message("assistant", content)]) == []


def test_recent_tool_calls_respects_limit() -> None:
    messages = [
        Message("assistant", f"```shell\necho {index}\n```") for index in range(7)
    ]
    calls = recent_tool_calls(messages, limit=5)
    assert len(calls) == 5
    assert calls[0].command == "echo 2"
    assert calls[-1].command == "echo 6"


def test_working_tree_changes_preserves_unusual_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.email", "bob@superuserlabs.org"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=workspace, check=True)
    subprocess.run(["git", "switch", "-c", "test-paths"], cwd=workspace, check=True)
    original = workspace / "before.txt"
    original.write_text("tracked\n")
    subprocess.run(["git", "add", "before.txt"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=workspace, check=True)

    renamed = workspace / "after -> Δ.txt"
    subprocess.run(["git", "mv", "before.txt", renamed.name], cwd=workspace, check=True)
    (workspace / "line\nbreak.txt").write_text("untracked\n")

    changes = working_tree_changes(workspace)

    assert any(
        change.path == "after -> Δ.txt" and change.action == "renamed"
        for change in changes
    )
    assert any(change.path == "line\nbreak.txt" for change in changes)


def test_cli_save_list_and_resume(logs_dir: Path) -> None:
    logdir = _make_session(logs_dir)
    runner = CliRunner()

    saved = runner.invoke(
        checkpoint_session,
        ["save", "handoff", "--session", str(logdir), "--summary", "Keep this."],
    )
    assert saved.exit_code == 0, saved.output
    assert "Saved checkpoint 'handoff'" in saved.output

    listed = runner.invoke(
        checkpoint_session, ["list", "--session", "test-conversation"]
    )
    assert listed.exit_code == 0, listed.output
    assert "handoff" in listed.output
    assert "4 messages" in listed.output

    resumed = runner.invoke(
        checkpoint_session,
        ["resume", "handoff", "--session", "test-conversation"],
    )
    assert resumed.exit_code == 0, resumed.output
    assert "<<RESUMED SESSION>>" in resumed.output
    assert "Keep this." in resumed.output


def test_cli_resume_strips_terminal_controls(logs_dir: Path) -> None:
    logdir = _make_session(logs_dir)
    checkpoint, _ = save_conversation_checkpoint(
        logdir,
        "hostile",
        summary="safe\x1b]0;spoofed\x07 text\nnext line",
    )
    assert "\x1b" in checkpoint.summary  # Structured data remains lossless.

    resumed = CliRunner().invoke(
        checkpoint_session,
        ["resume", "hostile", "--session", str(logdir)],
    )

    assert resumed.exit_code == 0, resumed.output
    assert "\x1b" not in resumed.output
    assert "\x07" not in resumed.output
    assert "safe]0;spoofed text\nnext line" in resumed.output


def test_cli_prefers_active_logdir(
    logs_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = _make_session(logs_dir, "active-conversation")
    newer = _make_session(logs_dir, "newer-conversation")
    conversation = newer / "conversation.jsonl"
    conversation.touch()
    monkeypatch.setenv("GPTME_LOGDIR", str(active))

    result = CliRunner().invoke(checkpoint_session, ["save", "active"])

    assert result.exit_code == 0, result.output
    assert (active / "checkpoints" / "active.json").exists()
    assert not (newer / "checkpoints" / "active.json").exists()


def test_slash_command_saves_current_conversation(
    logs_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logdir = _make_session(logs_dir)
    manager = LogManager.load(logdir, lock=False)
    # The command framework removes the slash-command message before invoking
    # the handler. This in-memory message is the latest durable conversation state.
    manager.log = manager.log.append(Message("assistant", "unsaved but checkpointed"))
    context = CommandContext(
        args=["save", "before-change"],
        full_args="save before-change",
        manager=manager,
    )

    cmd_session_checkpoint(context)

    path = logdir / "checkpoints" / "before-change.json"
    assert path.exists()
    assert load_conversation_checkpoint(logdir, "before-change").message_count == 5
    assert "Saved conversation checkpoint" in capsys.readouterr().out


def test_util_dispatch_exposes_checkpoint_session(logs_dir: Path) -> None:
    _make_session(logs_dir)
    from gptme.cli.util import main as util_main

    result = CliRunner().invoke(util_main, ["checkpoint-session", "--help"])
    assert result.exit_code == 0, result.output
    assert "durable conversation checkpoints" in result.output


def test_main_cli_forwards_checkpoint_session() -> None:
    from gptme.cli.main import main

    with (
        patch("gptme.cli.main.shutil.which", return_value="/usr/local/bin/gptme-util"),
        patch("gptme.cli.main.subprocess.call", return_value=0) as call,
    ):
        result = CliRunner().invoke(main, ["checkpoint-session", "list"])

    assert result.exit_code == 0
    call.assert_called_once_with(
        ["/usr/local/bin/gptme-util", "checkpoint-session", "list"], env=ANY
    )
