"""Tests for gptme-util status command."""

from __future__ import annotations

from click.testing import CliRunner

from gptme.cli.cmd_status import status
from gptme.cli.util import main as util_main


def test_status_output_contains_expected_sections():
    """Verify the status output contains standard sections."""
    runner = CliRunner()
    result = runner.invoke(status)
    assert result.exit_code == 0
    assert "# gptme Status" in result.output
    assert "## Active Work" in result.output
    assert "## PR Queue" in result.output
    assert "## Services" in result.output
    assert "## Top Blockers" in result.output
    assert "## Ready Next" in result.output


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
