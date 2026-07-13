"""Tests for `gptme-util chats fork` session forking."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest
from click.testing import CliRunner

from gptme.cli.cmd_chats import _slice_at_turn
from gptme.message import Message

# ── _slice_at_turn unit tests ─────────────────────────────────────────

_Role = Literal["system", "user", "assistant"]


def _msgs(*roles: _Role) -> list[Message]:
    """Build a minimal message list from a role sequence."""
    role_content = {
        "system": "You are helpful.",
        "user": "A question.",
        "assistant": "An answer.",
    }
    return [Message(r, role_content[r]) for r in roles]


def test_slice_turn_0_keeps_only_system():
    msgs = _msgs("system", "user", "assistant", "user", "assistant")
    result = _slice_at_turn(msgs, 0)
    assert [m.role for m in result] == ["system"]


def test_slice_turn_0_no_system():
    """Turn 0 with no leading system messages returns empty list."""
    msgs = _msgs("user", "assistant", "user", "assistant")
    result = _slice_at_turn(msgs, 0)
    assert result == []


def test_slice_turn_1_through_first_exchange():
    msgs = _msgs("system", "user", "assistant", "user", "assistant")
    result = _slice_at_turn(msgs, 1)
    assert [m.role for m in result] == ["system", "user", "assistant"]


def test_slice_turn_2_through_second_exchange():
    msgs = _msgs(
        "system", "user", "assistant", "user", "assistant", "user", "assistant"
    )
    result = _slice_at_turn(msgs, 2)
    assert [m.role for m in result] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_slice_turn_equals_total_returns_all():
    msgs = _msgs("system", "user", "assistant", "user", "assistant")
    result = _slice_at_turn(msgs, 2)
    assert result == msgs


def test_slice_turn_exceeds_total_returns_all():
    msgs = _msgs("system", "user", "assistant")
    result = _slice_at_turn(msgs, 99)
    assert result == msgs


def test_slice_turn_1_last_turn_includes_all():
    """When there's only one user turn, from-turn 1 returns everything."""
    msgs = _msgs("system", "user", "assistant")
    result = _slice_at_turn(msgs, 1)
    assert result == msgs


def test_slice_negative_turn_raises_error():
    """Negative turn values should raise ValueError."""
    msgs = _msgs("system", "user", "assistant")
    with pytest.raises(ValueError, match="Turn must be non-negative"):
        _slice_at_turn(msgs, -1)


def test_slice_negative_turn_large_raises_error():
    """Large negative turn values should also raise ValueError."""
    msgs = _msgs("system", "user", "assistant")
    with pytest.raises(ValueError, match="Turn must be non-negative"):
        _slice_at_turn(msgs, -100)


def test_slice_preserves_content():
    msgs = [
        Message("system", "System prompt"),
        Message("user", "Hello"),
        Message("assistant", "Hi there"),
        Message("user", "Goodbye"),
        Message("assistant", "Bye"),
    ]
    result = _slice_at_turn(msgs, 1)
    assert result[1].content == "Hello"
    assert result[2].content == "Hi there"


def test_slice_multi_assistant_same_turn():
    """Multiple assistant messages in one turn (tool calls) all get included."""
    msgs = [
        Message("system", "You are helpful."),
        Message("user", "Do a thing."),
        Message("assistant", "Let me run this."),
        Message("assistant", "Tool result received."),
        Message("user", "Thanks."),
        Message("assistant", "Done."),
    ]
    result = _slice_at_turn(msgs, 1)
    # Should include everything up to (not including) the second user message
    assert len(result) == 4
    assert result[-1].content == "Tool result received."


# ── gptme-util chats fork CLI integration tests ───────────────────────


def _write_conversation(logdir: Path, messages: list[dict]) -> None:
    logdir.mkdir(parents=True, exist_ok=True)
    conv = logdir / "conversation.jsonl"
    conv.write_text("\n".join(json.dumps(m) for m in messages) + "\n")


@pytest.fixture()
def source_session(tmp_path: Path) -> Path:
    """A source session with 3 user turns in a temp logs dir."""
    logs_dir = tmp_path / "logs"
    src_dir = logs_dir / "my-session"
    _write_conversation(
        src_dir,
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Turn 1 question."},
            {"role": "assistant", "content": "Turn 1 answer."},
            {"role": "user", "content": "Turn 2 question."},
            {"role": "assistant", "content": "Turn 2 answer."},
            {"role": "user", "content": "Turn 3 question."},
            {"role": "assistant", "content": "Turn 3 answer."},
        ],
    )
    return logs_dir


def test_chats_fork_creates_forked_session(source_session: Path, monkeypatch):
    """chats fork my-session --at-turn 2 --name my-fork creates a new session."""
    from gptme.cli.cmd_chats import chats_fork

    logs_dir = source_session
    monkeypatch.setattr("gptme.cli.cmd_chats.get_logs_dir", lambda: logs_dir)

    runner = CliRunner()
    result = runner.invoke(
        chats_fork,
        ["my-session", "--at-turn", "2", "--name", "my-fork"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, f"Exit {result.exit_code}. Output:\n{result.output}"
    fork_dir = logs_dir / "my-fork"
    assert fork_dir.exists(), "Fork dir not created"
    conv = fork_dir / "conversation.jsonl"
    assert conv.exists()
    messages = [json.loads(line) for line in conv.read_text().splitlines() if line]
    roles = [m["role"] for m in messages]
    # system + 2 user+assistant pairs = 5 messages
    assert roles == ["system", "user", "assistant", "user", "assistant"]


def test_chats_fork_name_collision(monkeypatch):
    """Forking into an existing session name should error."""
    from gptme.cli.cmd_chats import chats_fork

    runner = CliRunner()
    with runner.isolated_filesystem():
        logs_dir = Path("logs")
        existing_dir = logs_dir / "existing-session"
        _write_conversation(
            existing_dir,
            [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Question."},
                {"role": "assistant", "content": "Answer."},
            ],
        )
        src_dir = logs_dir / "source-session"
        _write_conversation(
            src_dir,
            [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Q1."},
                {"role": "assistant", "content": "A1."},
                {"role": "user", "content": "Q2."},
                {"role": "assistant", "content": "A2."},
            ],
        )

        monkeypatch.setattr("gptme.cli.cmd_chats.get_logs_dir", lambda: logs_dir)

        result = runner.invoke(
            chats_fork,
            ["source-session", "--at-turn", "1", "--name", "existing-session"],
            catch_exceptions=False,
        )

        assert result.exit_code != 0
        assert "already exists" in result.output


def test_chats_fork_name_collision_stale_state(monkeypatch):
    """Forking into a dir with stale state (no conversation.jsonl) should also error."""
    from gptme.cli.cmd_chats import chats_fork

    runner = CliRunner()
    with runner.isolated_filesystem():
        logs_dir = Path("logs")
        stale_dir = logs_dir / "stale-fork"
        stale_dir.mkdir(parents=True)
        (stale_dir / "config.toml").write_text("[session]\n")

        src_dir = logs_dir / "source-session"
        _write_conversation(
            src_dir,
            [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Q1."},
                {"role": "assistant", "content": "A1."},
            ],
        )

        monkeypatch.setattr("gptme.cli.cmd_chats.get_logs_dir", lambda: logs_dir)

        result = runner.invoke(
            chats_fork,
            ["source-session", "--at-turn", "1", "--name", "stale-fork"],
            catch_exceptions=False,
        )

        assert result.exit_code != 0
        assert "already exists" in result.output


def test_chats_fork_out_of_range_turn_errors(monkeypatch):
    """Forking with --at-turn beyond the session's turn count should error."""
    from gptme.cli.cmd_chats import chats_fork

    runner = CliRunner()
    with runner.isolated_filesystem():
        logs_dir = Path("logs")
        src_dir = logs_dir / "source-session"
        _write_conversation(
            src_dir,
            [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Q1."},
                {"role": "assistant", "content": "A1."},
                {"role": "user", "content": "Q2."},
                {"role": "assistant", "content": "A2."},
            ],
        )
        monkeypatch.setattr("gptme.cli.cmd_chats.get_logs_dir", lambda: logs_dir)

        result = runner.invoke(
            chats_fork,
            ["source-session", "--at-turn", "99", "--name", "my-fork"],
            catch_exceptions=False,
        )

        assert result.exit_code != 0
        assert "out of range" in result.output


def test_chats_fork_copies_files_dir(monkeypatch):
    """Forking copies the source session's files/ directory to the fork."""
    from gptme.cli.cmd_chats import chats_fork

    runner = CliRunner()
    with runner.isolated_filesystem():
        logs_dir = Path("logs")
        src_dir = logs_dir / "source-session"
        _write_conversation(
            src_dir,
            [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Q1."},
                {"role": "assistant", "content": "A1."},
            ],
        )
        # Create a files/ subdirectory with a stored attachment
        files_dir = src_dir / "files"
        files_dir.mkdir()
        (files_dir / "abc123.png").write_bytes(b"\x89PNG\r\n")

        monkeypatch.setattr("gptme.cli.cmd_chats.get_logs_dir", lambda: logs_dir)

        result = runner.invoke(
            chats_fork,
            ["source-session", "--at-turn", "1", "--name", "my-fork"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Exit {result.exit_code}. Output:\n{result.output}"
        )
        fork_files = logs_dir / "my-fork" / "files"
        assert fork_files.exists(), "files/ dir not copied to fork"
        assert (fork_files / "abc123.png").exists(), "attachment not copied"
