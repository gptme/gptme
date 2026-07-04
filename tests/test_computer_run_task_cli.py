"""Tests for `gptme-util computer run-task` (cmd_computer.py).

Unit-tests the run-task CLI command without spawning a real subagent or
requiring an API key.  The computer_task() call is monkey-patched to return
canned results.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from gptme.cli.cmd_computer import run_task


class TestRunTaskCmd:
    """Tests for `gptme-util computer run-task`."""

    def test_help_text_present(self):
        runner = CliRunner()
        result = runner.invoke(run_task, ["--help"])
        assert result.exit_code == 0
        assert "--timeout" in result.output
        assert "--model" in result.output
        assert "--json" in result.output

    def test_success_prints_status(self, monkeypatch):
        """A successful task prints '✓ Status: success' and exits 0."""

        def fake_computer_task(task, timeout=300, model=None):
            return {
                "status": "success",
                "result": "Screenshot saved to /tmp/desktop.png",
                "agent_id": "computer-task-deadbeef",
                "conversation": "subagent-computer-task-deadbeef",
                "logdir": "/tmp/gptme-logs/subagent-computer-task-deadbeef",
            }

        monkeypatch.setattr("gptme.tools.computer.computer_task", fake_computer_task)

        runner = CliRunner()
        result = runner.invoke(run_task, ["take a screenshot"])
        assert result.exit_code == 0
        assert "success" in result.output
        assert "Screenshot saved" in result.output

    def test_failure_exits_1(self, monkeypatch):
        """A failed task exits with code 1."""

        def fake_computer_task(task, timeout=300, model=None):
            return {
                "status": "failure",
                "result": "Could not open Firefox — no display available.",
                "agent_id": "computer-task-aabbccdd",
            }

        monkeypatch.setattr("gptme.tools.computer.computer_task", fake_computer_task)

        runner = CliRunner()
        result = runner.invoke(run_task, ["open firefox"])
        assert result.exit_code == 1
        assert "failure" in result.output

    def test_timeout_exits_1(self, monkeypatch):
        """A timed-out task exits with code 1."""

        def fake_computer_task(task, timeout=300, model=None):
            return {
                "status": "timeout",
                "result": "Auto-cancelled after 42s",
                "agent_id": "computer-task-11223344",
            }

        monkeypatch.setattr("gptme.tools.computer.computer_task", fake_computer_task)

        runner = CliRunner()
        result = runner.invoke(run_task, ["do something", "--timeout", "42"])
        assert result.exit_code == 1
        assert "timeout" in result.output

    def test_json_output(self, monkeypatch):
        """--json flag prints the raw result dict and exits 0 on success."""
        payload = {
            "status": "success",
            "result": "Done.",
            "agent_id": "computer-task-cafebabe",
            "conversation": "subagent-computer-task-cafebabe",
            "logdir": "/tmp/gptme-logs/subagent-computer-task-cafebabe",
        }

        def fake_computer_task(task, timeout=300, model=None):
            return payload

        monkeypatch.setattr("gptme.tools.computer.computer_task", fake_computer_task)

        runner = CliRunner()
        result = runner.invoke(run_task, ["do something", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["status"] == "success"
        assert parsed["agent_id"] == "computer-task-cafebabe"

    def test_json_failure_exits_1(self, monkeypatch):
        """--json flag with a failed task still exits with code 1."""

        def fake_computer_task(task, timeout=300, model=None):
            return {"status": "failure", "result": "Error", "agent_id": "x"}

        monkeypatch.setattr("gptme.tools.computer.computer_task", fake_computer_task)

        runner = CliRunner()
        result = runner.invoke(run_task, ["something", "--json"])
        assert result.exit_code == 1

    def test_model_is_forwarded(self, monkeypatch):
        """--model flag is forwarded to computer_task()."""
        captured: list[dict] = []

        def fake_computer_task(task, timeout=300, model=None):
            captured.append({"task": task, "model": model})
            return {"status": "success", "result": "ok", "agent_id": "x"}

        monkeypatch.setattr("gptme.tools.computer.computer_task", fake_computer_task)

        runner = CliRunner()
        runner.invoke(run_task, ["do something", "--model", "claude-opus-4-8"])
        assert len(captured) == 1
        assert captured[0]["model"] == "claude-opus-4-8"

    def test_timeout_is_forwarded(self, monkeypatch):
        """--timeout flag is forwarded to computer_task()."""
        captured: list[dict] = []

        def fake_computer_task(task, timeout=300, model=None):
            captured.append({"timeout": timeout})
            return {"status": "success", "result": "ok", "agent_id": "x"}

        monkeypatch.setattr("gptme.tools.computer.computer_task", fake_computer_task)

        runner = CliRunner()
        runner.invoke(run_task, ["do something", "--timeout", "60"])
        assert len(captured) == 1
        assert captured[0]["timeout"] == 60

    def test_audit_hint_shown_when_conversation_present(self, monkeypatch):
        """When result includes 'conversation', the output hints at audit-log."""

        def fake_computer_task(task, timeout=300, model=None):
            return {
                "status": "success",
                "result": "Done.",
                "agent_id": "computer-task-abc123",
                "conversation": "subagent-computer-task-abc123",
                "logdir": "/tmp/logs/subagent-computer-task-abc123",
            }

        monkeypatch.setattr("gptme.tools.computer.computer_task", fake_computer_task)

        runner = CliRunner()
        result = runner.invoke(run_task, ["do something"])
        assert "audit-log" in result.output
        assert "subagent-computer-task-abc123" in result.output
