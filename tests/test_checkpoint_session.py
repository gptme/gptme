"""Tests for durable conversation checkpoints and their CLI."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from gptme.checkpoint_session import (
    ConversationCheckpointError,
    checkpoint_resume_prompt,
    list_conversation_checkpoints,
    load_conversation_checkpoint,
    recent_tool_calls,
    save_conversation_checkpoint,
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


@pytest.mark.parametrize("label", ["../escape", "two words", "", "/absolute"])
def test_save_rejects_unsafe_labels(logs_dir: Path, label: str) -> None:
    logdir = _make_session(logs_dir)
    with pytest.raises(ConversationCheckpointError, match="labels must"):
        save_conversation_checkpoint(logdir, label)


def test_load_rejects_unknown_schema_version(logs_dir: Path) -> None:
    logdir = _make_session(logs_dir)
    _, path = save_conversation_checkpoint(logdir, "future")
    data = json.loads(path.read_text())
    data["version"] = 99
    path.write_text(json.dumps(data))

    with pytest.raises(ConversationCheckpointError, match="Unsupported"):
        load_conversation_checkpoint(logdir, "future")


def test_recent_tool_calls_respects_limit() -> None:
    messages = [
        Message("assistant", f"```shell\necho {index}\n```") for index in range(7)
    ]
    calls = recent_tool_calls(messages, limit=5)
    assert len(calls) == 5
    assert calls[0].command == "echo 2"
    assert calls[-1].command == "echo 6"


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
    context = CommandContext(
        args=["save", "before-change"],
        full_args="save before-change",
        manager=manager,
    )

    cmd_session_checkpoint(context)

    assert (logdir / "checkpoints" / "before-change.json").exists()
    assert "Saved conversation checkpoint" in capsys.readouterr().out


def test_util_dispatch_exposes_checkpoint_session(logs_dir: Path) -> None:
    _make_session(logs_dir)
    from gptme.cli.util import main as util_main

    result = CliRunner().invoke(util_main, ["checkpoint-session", "--help"])
    assert result.exit_code == 0, result.output
    assert "durable conversation checkpoints" in result.output
