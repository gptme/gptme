"""Tests for gptme-util resume / gptme-resume command."""

from __future__ import annotations

import json
import time
from pathlib import Path  # noqa: TC003

import pytest
from click.testing import CliRunner

from gptme.cli.cmd_resume import (
    _list_sessions,
    _session_name,
    _user_message,
    resume,
    synthesize_prompt,
)
from gptme.cli.util import main as util_main

# ── fixtures ────────────────────────────────────────────────────────────


def _make_session(
    base: Path, name: str, *, task: str | None = None, mtime_offset: int = 0
) -> Path:
    """Create a minimal fake session directory under *base*."""
    session_dir = base / name
    session_dir.mkdir(parents=True)

    messages = []
    if task:
        messages.append({"role": "user", "content": task})
    messages.append({"role": "assistant", "content": "Working on it."})

    conv = session_dir / "conversation.jsonl"
    conv.write_text("\n".join(json.dumps(m) for m in messages) + "\n")

    # Adjust mtime so we can control sort order
    if mtime_offset:
        t = time.time() + mtime_offset
        import os

        os.utime(conv, (t, t))

    return session_dir


@pytest.fixture()
def logs_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


@pytest.fixture()
def populated_logs(logs_dir: Path) -> tuple[Path, list[Path]]:
    """Three sessions, newest first (offset 0, -10, -20)."""
    logs_dir.mkdir()
    s0 = _make_session(
        logs_dir, "session-alpha", task="Fix the bug in utils.py", mtime_offset=0
    )
    s1 = _make_session(logs_dir, "session-beta", task="Write tests", mtime_offset=-10)
    s2 = _make_session(logs_dir, "session-gamma", mtime_offset=-20)
    return logs_dir, [s0, s1, s2]


# ── unit tests ──────────────────────────────────────────────────────────


def test_list_sessions_sorted_newest_first(populated_logs: tuple[Path, list[Path]]):
    logs_dir, sessions = populated_logs
    result = _list_sessions(logs_dir, n=10)
    assert len(result) == 3
    # Most recently modified conv.jsonl should be first
    mtimes = [(r / "conversation.jsonl").stat().st_mtime for r in result]
    assert mtimes == sorted(mtimes, reverse=True)


def test_list_sessions_honours_n_limit(populated_logs: tuple[Path, list[Path]]):
    logs_dir, _ = populated_logs
    result = _list_sessions(logs_dir, n=2)
    assert len(result) == 2


def test_list_sessions_empty_dir(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _list_sessions(empty) == []


def test_session_name_from_config(tmp_path: Path):
    session = tmp_path / "run-autonomous-abc"
    session.mkdir()
    (session / "config.toml").write_text('name = "my-cool-session"\n')
    assert _session_name(session) == "my-cool-session"


def test_session_name_fallback_strips_prefix(tmp_path: Path):
    session = tmp_path / "run-autonomous-abc123"
    session.mkdir()
    assert _session_name(session) == "abc123"


def test_user_message_extracts_task(populated_logs: tuple[Path, list[Path]]):
    _, sessions = populated_logs
    msg = _user_message(sessions[0])
    assert msg is not None
    assert "Fix the bug" in msg


def test_user_message_none_for_no_user_role(logs_dir: Path):
    logs_dir.mkdir()
    session = logs_dir / "assistant-only"
    session.mkdir()
    conv = session / "conversation.jsonl"
    conv.write_text(json.dumps({"role": "assistant", "content": "hello"}) + "\n")
    assert _user_message(session) is None


def test_synthesize_prompt_structure(populated_logs: tuple[Path, list[Path]]):
    _, sessions = populated_logs
    prompt = synthesize_prompt(sessions[0])
    assert "<<RESUMED SESSION>>" in prompt
    assert "[SESSION:" in prompt
    assert "[LAST ACTIVE:" in prompt
    assert "[ORIGINAL TASK]" in prompt
    assert "Fix the bug" in prompt
    assert "Continue your work" in prompt


# ── CLI tests ───────────────────────────────────────────────────────────


def test_resume_no_logs_dir(tmp_path: Path, monkeypatch):
    """When the logs directory does not exist, exit with a clear error."""
    monkeypatch.setattr(
        "gptme.cli.cmd_resume._get_logs_dir",
        lambda: tmp_path / "nonexistent",
    )
    runner = CliRunner()
    result = runner.invoke(resume)
    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "not found" in (
        result.exception and str(result.exception) or ""
    )


def test_resume_empty_logs_dir(tmp_path: Path, monkeypatch):
    """Empty logs dir gives a clean error with no traceback."""
    empty = tmp_path / "logs"
    empty.mkdir()
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: empty)
    runner = CliRunner()
    result = runner.invoke(resume)
    assert result.exit_code != 0
    assert "No sessions found" in result.output


def test_resume_list(populated_logs: tuple[Path, list[Path]], monkeypatch):
    logs_dir, _ = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(resume, ["--list"])
    assert result.exit_code == 0
    assert "[0]" in result.output
    assert "[1]" in result.output
    assert "[2]" in result.output
    assert "session-alpha" in result.output


def test_resume_default_most_recent(
    populated_logs: tuple[Path, list[Path]], monkeypatch
):
    logs_dir, sessions = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(resume)
    assert result.exit_code == 0
    assert "<<RESUMED SESSION>>" in result.output
    assert "session-alpha" in result.output


def test_resume_last_index(populated_logs: tuple[Path, list[Path]], monkeypatch):
    logs_dir, sessions = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(resume, ["--last", "1"])
    assert result.exit_code == 0
    assert "session-beta" in result.output


def test_resume_explicit_session(populated_logs: tuple[Path, list[Path]], monkeypatch):
    logs_dir, sessions = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(resume, ["--session", str(sessions[2])])
    assert result.exit_code == 0
    assert "<<RESUMED SESSION>>" in result.output
    assert "session-gamma" in result.output


def test_resume_bad_session_path(populated_logs: tuple[Path, list[Path]], monkeypatch):
    logs_dir, _ = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(resume, ["--session", "/nonexistent/path"])
    assert result.exit_code != 0


def test_resume_last_out_of_range(populated_logs: tuple[Path, list[Path]], monkeypatch):
    logs_dir, _ = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(resume, ["--last", "99"])
    assert result.exit_code != 0
    assert "available" in result.output.lower() or "available" in (
        result.exception and str(result.exception) or ""
    )


def test_resume_output_json(populated_logs: tuple[Path, list[Path]], monkeypatch):
    logs_dir, _ = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(resume, ["--output", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "session" in data
    assert "name" in data
    assert "last_active" in data
    assert "has_task" in data
    assert isinstance(data["tools_used"], list)


def test_resume_invoked_via_util_subcommand(
    populated_logs: tuple[Path, list[Path]], monkeypatch
):
    logs_dir, _ = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(util_main, ["resume"])
    assert result.exit_code == 0
    assert "<<RESUMED SESSION>>" in result.output


def test_resume_last_negative(populated_logs: tuple[Path, list[Path]], monkeypatch):
    """Negative --last values should raise ClickException, not silently index from end."""
    logs_dir, _ = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(resume, ["--last", "-1"])
    assert result.exit_code != 0
    assert "available" in result.output.lower() or "available" in (
        result.exception and str(result.exception) or ""
    )


def test_user_message_empty_content_returns_none(logs_dir: Path):
    """Empty user message after stripping should return None, not \"\"."""
    logs_dir.mkdir()
    session = logs_dir / "empty-user-msg"
    session.mkdir()
    conv = session / "conversation.jsonl"
    conv.write_text(json.dumps({"role": "user", "content": ""}) + "\n")
    assert _user_message(session) is None


def test_resume_has_task_false_for_no_user_msg(
    populated_logs: tuple[Path, list[Path]], monkeypatch
):
    """--output json should report has_task: false for sessions with no user message."""
    logs_dir, sessions = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    # sessions[2] (session-gamma) has no user task
    runner = CliRunner()
    result = runner.invoke(resume, ["--session", str(sessions[2]), "--output", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["has_task"] is False
