"""Tests for gptme-util status command."""

from __future__ import annotations

from click.testing import CliRunner

import gptme.cli.cmd_status as cmd_status
from gptme.cli.cmd_status import (
    _session_id,
    _strip_markdown,
    build_table_document,
    status,
)
from gptme.cli.util import main as util_main


def test_status_output_contains_expected_sections():
    """Verify the status output contains always-present sections."""
    runner = CliRunner()
    result = runner.invoke(status)
    assert result.exit_code == 0
    assert "# gptme Status" in result.output
    assert "## Active Work" in result.output
    assert "## PR Queue" in result.output
    # Services/blockers/ready sections are only included in Bob's workspace
    # (when gptme.toml + tasks/ are present); not asserted here.


def test_status_invoked_via_util_subcommand():
    """Verify gptme-util status dispatches correctly."""
    runner = CliRunner()
    result = runner.invoke(util_main, ["status"])
    assert result.exit_code == 0
    assert "# gptme Status" in result.output


def test_status_write_to_file(tmp_path):
    """Verify --write creates a file at the repo root equivalent."""
    runner = CliRunner()
    output_file = tmp_path / "handoff.md"
    result = runner.invoke(status, ["-o", str(output_file)])
    assert result.exit_code == 0
    assert output_file.exists()
    content = output_file.read_text()
    assert "# gptme Status" in content
    assert "## Active Work" in content


def test_status_no_markdown():
    """Verify --no-markdown strips heading markers from output."""
    runner = CliRunner()
    result = runner.invoke(status, ["--no-markdown"])
    assert result.exit_code == 0
    assert "# gptme Status" not in result.output
    assert "gptme Status" in result.output


def test_status_agent_name_from_env(monkeypatch):
    """Verify GPTME_AGENT_NAME env var is reflected in the header."""
    monkeypatch.setenv("GPTME_AGENT_NAME", "TestAgent")
    runner = CliRunner()
    result = runner.invoke(status)
    assert result.exit_code == 0
    assert "TestAgent" in result.output


def test_strip_markdown_removes_headings():
    """Unit-test the _strip_markdown helper."""
    doc = (
        "# Heading\n\nSome **bold** text and `code`.\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    )
    plain = _strip_markdown(doc)
    assert "# Heading" not in plain
    assert "Heading" in plain
    assert "**bold**" not in plain
    assert "bold" in plain
    assert "`code`" not in plain
    assert "code" in plain


def test_status_format_table():
    """Verify --format table outputs a markdown table with expected fields."""
    runner = CliRunner()
    result = runner.invoke(status, ["--format", "table"])
    assert result.exit_code == 0
    assert "| Field | Value |" in result.output
    assert "| session_id |" in result.output
    assert "| active_task |" in result.output
    assert "| last_commit |" in result.output
    assert "| pending_prs |" in result.output
    assert "| waiting_for |" in result.output
    assert "| disk_usage |" in result.output
    assert "| journal_entries |" in result.output


def test_status_format_table_via_util():
    """Verify gptme-util status --format table dispatches correctly."""
    runner = CliRunner()
    result = runner.invoke(util_main, ["status", "--format", "table"])
    assert result.exit_code == 0
    assert "| Field | Value |" in result.output
    assert "| session_id |" in result.output


def test_status_format_narrative_is_default():
    """Verify default format is narrative (not table)."""
    runner = CliRunner()
    result = runner.invoke(status)
    assert result.exit_code == 0
    assert "## Active Work" in result.output
    assert "| Field | Value |" not in result.output


def test_session_id_from_env(monkeypatch):
    """Verify _session_id reads from environment variables."""
    monkeypatch.setenv("GPTME_SESSION_ID", "abc123")
    assert _session_id() == "abc123"
    monkeypatch.delenv("GPTME_SESSION_ID")
    monkeypatch.setenv("BOB_SESSION_ID", "def456")
    assert _session_id() == "def456"


def test_session_id_fallback(monkeypatch):
    """Verify _session_id returns 'none' when no env var is set."""
    for key in (
        "GPTME_SESSION_ID",
        "BOB_SESSION_ID",
        "SESSION_ID",
        "GIT_COMMITTER_SESSION_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    assert _session_id() == "none"


def test_build_table_document_structure():
    """Verify build_table_document produces a markdown table."""
    doc = build_table_document()
    lines = doc.splitlines()
    assert any("| Field | Value |" in line for line in lines)
    assert any("| session_id |" in line for line in lines)
    assert any("| last_commit |" in line for line in lines)


def test_build_table_document_escapes_multiline_waiting_for(monkeypatch):
    """Verify multiline blockers stay inside a single markdown table row."""
    monkeypatch.setattr(cmd_status, "_is_bob_workspace", lambda: False)
    monkeypatch.setattr(cmd_status, "_active_tasks", lambda _limit=1: [])
    monkeypatch.setattr(cmd_status, "_recent_commits", lambda _limit=1: [])
    monkeypatch.setattr(cmd_status, "_pr_queue", lambda _tracked: [])
    monkeypatch.setattr(cmd_status, "_git_root", lambda: None)
    monkeypatch.setattr(cmd_status, "_disk_usage", lambda _root=None: "1G / 2G (50%)")
    monkeypatch.setattr(cmd_status, "_journal_entries", lambda _limit=5: [])
    monkeypatch.setattr(
        cmd_status,
        "_blockers",
        lambda _limit=1: [{"waiting_for": "first line\nsecond | line"}],
    )

    doc = build_table_document()

    assert "| waiting_for | first line second \\| line |" in doc
    assert "second | line" not in doc
