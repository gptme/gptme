"""Tests for gptme-util status command."""

from __future__ import annotations

from click.testing import CliRunner

from gptme.cli.cmd_status import _strip_markdown, status
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
