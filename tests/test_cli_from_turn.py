"""Tests for gptme --from-turn session branching."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest
from click.testing import CliRunner

from gptme.cli.main import _slice_at_turn
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


# ── --from-turn CLI integration tests ────────────────────────────────


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


def test_slice_at_turn_requires_name_or_resume():
    """--from-turn without --resume or --name should error."""
    from gptme.cli.main import main

    runner = CliRunner()
    result = runner.invoke(main, ["--from-turn", "2"])
    assert result.exit_code != 0
    assert "--resume" in result.output or "--name" in result.output


def test_from_turn_creates_branched_session(
    source_session: Path, monkeypatch, tmp_path
):
    """--name src --from-turn 2 creates a new session with 2 turns."""
    from gptme.cli.main import main

    logs_dir = source_session
    monkeypatch.setattr("gptme.cli.main.get_logs_dir", lambda: logs_dir)
    monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: logs_dir)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--name",
            "my-session",
            "--from-turn",
            "2",
            "--branch",
            "my-branch",
            "--non-interactive",
        ],
        catch_exceptions=False,
    )

    # Session was branched: new dir should exist with sliced messages
    branch_dir = logs_dir / "my-branch"
    assert branch_dir.exists(), f"Branch dir not created. Output:\n{result.output}"
    conv = branch_dir / "conversation.jsonl"
    assert conv.exists()
    messages = [json.loads(line) for line in conv.read_text().splitlines() if line]
    roles = [m["role"] for m in messages]
    # Should have system + 2 user+assistant pairs = 5 messages
    assert roles == ["system", "user", "assistant", "user", "assistant"]


def test_from_turn_branch_name_collision(monkeypatch):
    """--branch with an existing session name should error."""
    from gptme.cli.main import main

    runner = CliRunner()
    with runner.isolated_filesystem():
        # Create an existing session
        existing_dir = Path("logs/existing-session")
        _write_conversation(
            existing_dir,
            [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Question."},
                {"role": "assistant", "content": "Answer."},
            ],
        )
        # Create a source session
        src_dir = Path("logs/source-session")
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

        # Redirect get_logs_dir to use the isolated filesystem
        logs_dir = Path("logs")
        monkeypatch.setattr("gptme.cli.main.get_logs_dir", lambda: logs_dir)
        monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: logs_dir)

        # Try to branch into the existing session name
        result = runner.invoke(
            main,
            [
                "--name",
                "source-session",
                "--from-turn",
                "1",
                "--branch",
                "existing-session",
                "--non-interactive",
            ],
            catch_exceptions=False,
        )

        # Should error on branch-name collision
        assert result.exit_code != 0
        assert (
            "already exists" in result.output or "choose a different" in result.output
        )


def test_from_turn_branch_name_collision_stale_state(monkeypatch):
    """--branch into a dir with stale state (no conversation.jsonl) should also error."""
    from gptme.cli.main import main

    runner = CliRunner()
    with runner.isolated_filesystem():
        # Simulate an interrupted run: directory exists with config.toml but no conversation.jsonl
        stale_dir = Path("logs/stale-branch")
        stale_dir.mkdir(parents=True)
        (stale_dir / "config.toml").write_text("[session]\n")

        # Create a source session
        src_dir = Path("logs/source-session")
        _write_conversation(
            src_dir,
            [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Q1."},
                {"role": "assistant", "content": "A1."},
            ],
        )

        logs_dir = Path("logs")
        monkeypatch.setattr("gptme.cli.main.get_logs_dir", lambda: logs_dir)
        monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: logs_dir)

        result = runner.invoke(
            main,
            [
                "--name",
                "source-session",
                "--from-turn",
                "1",
                "--branch",
                "stale-branch",
                "--non-interactive",
            ],
            catch_exceptions=False,
        )

        # Stale state should be rejected the same as an existing conversation
        assert result.exit_code != 0
        assert (
            "already exists" in result.output or "choose a different" in result.output
        )
