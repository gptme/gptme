"""Tests for the vent tool (friction signal emitter)."""

import json

import pytest

from gptme.tools.vent import _reset_vent_limit, _vent_this_turn, execute_vent


@pytest.fixture(autouse=True)
def reset_rate_limit():
    """Reset per-turn rate-limit flag before each test."""
    _vent_this_turn.set(False)
    yield
    _vent_this_turn.set(False)


@pytest.fixture()
def ledger_path(tmp_path, monkeypatch):
    """Point the vent ledger at a temp directory."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return tmp_path / "gptme" / "friction-ledger.jsonl"


class TestExecuteVent:
    def test_records_entry(self, ledger_path):
        msg = execute_vent("Stuck on import resolution", None, None)
        assert "recorded" in msg.content.lower()
        assert ledger_path.exists()
        entries = [json.loads(line) for line in ledger_path.read_text().splitlines()]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["message"] == "Stuck on import resolution"
        assert "timestamp" in entry
        assert "workspace" in entry

    def test_strips_whitespace(self, ledger_path):
        execute_vent("  trailing spaces  \n", None, None)
        entry = json.loads(ledger_path.read_text().strip())
        assert entry["message"] == "trailing spaces"

    def test_empty_message_no_write(self, ledger_path):
        msg = execute_vent("", None, None)
        assert "no message" in msg.content.lower()
        assert not ledger_path.exists()

    def test_whitespace_only_no_write(self, ledger_path):
        msg = execute_vent("   \n\t  ", None, None)
        assert "no message" in msg.content.lower()
        assert not ledger_path.exists()

    def test_none_message_no_write(self, ledger_path):
        execute_vent(None, None, None)
        assert not ledger_path.exists()

    def test_rate_limit_blocks_second_vent(self, ledger_path):
        execute_vent("First vent", None, None)
        msg = execute_vent("Second vent", None, None)
        assert "rate limit" in msg.content.lower()
        # Only first vent recorded
        entries = [json.loads(line) for line in ledger_path.read_text().splitlines()]
        assert len(entries) == 1
        assert entries[0]["message"] == "First vent"

    def test_rate_limit_resets_after_hook(self, ledger_path):
        execute_vent("First vent", None, None)
        # Simulate step_pre hook firing between turns
        list(_reset_vent_limit(None))  # type: ignore[arg-type]
        msg = execute_vent("Second vent after reset", None, None)
        assert "recorded" in msg.content.lower()
        entries = [json.loads(line) for line in ledger_path.read_text().splitlines()]
        assert len(entries) == 2

    def test_entry_jsonl_format(self, ledger_path):
        execute_vent("Line 1", None, None)
        list(_reset_vent_limit(None))  # type: ignore[arg-type]
        execute_vent("Line 2", None, None)
        lines = ledger_path.read_text().splitlines()
        assert len(lines) == 2
        for line in lines:
            json.loads(line)  # must be valid JSON

    def test_ledger_appends_across_calls(self, ledger_path):
        """Each vent appends; earlier entries are not overwritten."""
        for i in range(3):
            list(_reset_vent_limit(None))  # type: ignore[arg-type]
            execute_vent(f"Vent {i}", None, None)
        entries = [json.loads(line) for line in ledger_path.read_text().splitlines()]
        assert len(entries) == 3
        assert [e["message"] for e in entries] == ["Vent 0", "Vent 1", "Vent 2"]
