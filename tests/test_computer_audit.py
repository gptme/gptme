"""Tests for gptme-util computer audit-log (cmd_computer.py).

Validates that computer() and observe_desktop() calls are extracted from
synthetic JSONL trajectories, and that typed text is never logged raw.
"""

from __future__ import annotations

import json
import textwrap
from datetime import datetime, timezone
from pathlib import Path  # noqa: TC003 — used at runtime in _write_conv_jsonl

from click.testing import CliRunner

from gptme.cli.cmd_computer import _extract_computer_calls, audit_log
from gptme.message import Message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(role: str, content: str) -> Message:
    ts = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    return Message(role=role, content=content, timestamp=ts)  # type: ignore[arg-type,call-arg]


def _ipython_block(code: str) -> str:
    return f"```ipython\n{code}\n```"


# ---------------------------------------------------------------------------
# Unit tests for _extract_computer_calls
# ---------------------------------------------------------------------------


def test_screenshot_action_extracted():
    msgs = [_msg("assistant", _ipython_block("computer('screenshot')"))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "screenshot"
    assert "text_len" not in records[0]


def test_type_action_text_is_redacted():
    msgs = [_msg("assistant", _ipython_block("computer('type', text='hunter2')"))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "type"
    # Raw text must NOT appear in the record
    assert "text" not in records[0]
    # Only the length is recorded
    assert records[0]["text_len"] == len("hunter2")


def test_key_action_text_is_redacted():
    msgs = [_msg("assistant", _ipython_block("computer('key', text='Return')"))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "key"
    assert "text" not in records[0]
    assert records[0]["text_len"] == len("Return")


def test_click_with_coordinate():
    code = "computer('left_click', coordinate=(100, 200))"
    msgs = [_msg("assistant", _ipython_block(code))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "left_click"
    assert records[0]["coordinate"] == [100, 200]


def test_observe_desktop_captured():
    msgs = [_msg("assistant", _ipython_block("observe_desktop()"))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "screenshot"
    assert records[0]["source"] == "observe_desktop"


def test_multiple_observe_desktop_calls_counted():
    code = textwrap.dedent("""\
        observe_desktop()
        observe_desktop()
    """)
    msgs = [_msg("assistant", _ipython_block(code))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 2
    assert all(r["action"] == "screenshot" for r in records)
    assert all(r["source"] == "observe_desktop" for r in records)


def test_non_assistant_messages_ignored():
    msgs = [
        _msg("user", "please take a screenshot"),
        _msg("system", "computer('screenshot')"),  # system message, not assistant
    ]
    records = _extract_computer_calls(msgs)
    assert records == []


def test_multiple_actions_in_one_block():
    code = textwrap.dedent("""\
        computer('screenshot')
        computer('left_click', coordinate=(50, 75))
    """)
    msgs = [_msg("assistant", _ipython_block(code))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 2
    actions = {r["action"] for r in records}
    assert actions == {"screenshot", "left_click"}


def test_multiple_actions_in_one_block_scopes_fields_per_call():
    code = textwrap.dedent("""\
        computer('screenshot')
        computer('left_click', coordinate=(50, 75))
        computer('type', text='short')
        computer('type', text='much-longer')
    """)
    msgs = [_msg("assistant", _ipython_block(code))]
    records = _extract_computer_calls(msgs)

    assert records == [
        {
            "timestamp": "2026-07-01T12:00:00+00:00",
            "action": "screenshot",
        },
        {
            "timestamp": "2026-07-01T12:00:00+00:00",
            "action": "left_click",
            "coordinate": [50, 75],
        },
        {
            "timestamp": "2026-07-01T12:00:00+00:00",
            "action": "type",
            "text_len": len("short"),
        },
        {
            "timestamp": "2026-07-01T12:00:00+00:00",
            "action": "type",
            "text_len": len("much-longer"),
        },
    ]


def test_prose_mention_not_counted():
    # "I will call computer('screenshot')" in plain prose (no code block) should
    # not be counted — only runnable tool-use blocks count.
    msgs = [_msg("assistant", "I will call computer('screenshot') to see the screen.")]
    records = _extract_computer_calls(msgs)
    # No runnable tool-use block → nothing extracted
    assert records == []


# ---------------------------------------------------------------------------
# Integration test: audit-log CLI against a synthetic JSONL file
# ---------------------------------------------------------------------------


def _write_conv_jsonl(path: Path, messages: list[Message]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.writelines(json.dumps(msg.to_dict()) + "\n" for msg in messages)


def test_audit_log_cli_basic(tmp_path, monkeypatch):
    conv_dir = tmp_path / "test-conv-2026-07-01"
    jsonl = conv_dir / "conversation.jsonl"
    msgs = [
        _msg("user", "take a screenshot"),
        _msg("assistant", _ipython_block("computer('screenshot')")),
    ]
    _write_conv_jsonl(jsonl, msgs)
    monkeypatch.setattr("gptme.cli.cmd_computer.get_logs_dir", lambda: tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        audit_log, [str(jsonl.parent.name), "--json"], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data[0]["conversation"] == jsonl.parent.name
    assert data[0]["action"] == "screenshot"

    result2 = runner.invoke(
        audit_log,
        # Pass the JSONL path directly as the "conversation" arg
        [str(jsonl)],
        catch_exceptions=False,
    )
    assert result2.exit_code == 0, result2.output


def test_audit_log_cli_redacts_type(tmp_path):
    conv_dir = tmp_path / "secret-conv"
    jsonl = conv_dir / "conversation.jsonl"
    msgs = [
        _msg("assistant", _ipython_block("computer('type', text='mysecretpassword')")),
    ]
    _write_conv_jsonl(jsonl, msgs)

    runner = CliRunner()
    result = runner.invoke(audit_log, [str(jsonl), "--json"], catch_exceptions=False)
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    # Raw password must NOT appear anywhere in the output
    assert "mysecretpassword" not in result.output
    assert data[0]["text_len"] == len("mysecretpassword")
    assert "text" not in data[0]


def test_audit_log_cli_no_logs_dir(tmp_path, monkeypatch):
    missing_logs = tmp_path / "missing-logs"
    monkeypatch.setattr("gptme.cli.cmd_computer.get_logs_dir", lambda: missing_logs)

    runner = CliRunner()
    result = runner.invoke(audit_log, [], catch_exceptions=False)

    assert result.exit_code == 0
    assert "No conversations found." in result.output


# ---------------------------------------------------------------------------
# _slice_call edge cases (lines 27, 29, 43)
# ---------------------------------------------------------------------------

from gptme.cli.cmd_computer import _slice_call


def test_slice_call_handles_escaped_quote():
    """Backslash-escaped quote inside string is not treated as end-of-string (lines 27–29)."""
    code = "computer('type', text='pass\\'word')"
    result = _slice_call(code, 0)
    assert result == code


def test_slice_call_unclosed_paren_returns_remainder():
    """When no closing ')' is found the fallback returns the rest of the string (line 43)."""
    code = "computer('screenshot'"  # no closing paren
    result = _slice_call(code, 0)
    assert result == code


# ---------------------------------------------------------------------------
# _extract_computer_calls edge cases (lines 59, 84)
# ---------------------------------------------------------------------------


def test_type_action_without_text_param():
    """type() called without a text= argument sets text_len to None (line 84)."""
    msgs = [_msg("assistant", _ipython_block("computer('type')"))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "type"
    assert records[0]["text_len"] is None


# ---------------------------------------------------------------------------
# CLI: additional paths
# ---------------------------------------------------------------------------


def test_audit_log_cli_no_actions_found(tmp_path):
    """When conversation has no computer() calls a user-friendly message is printed (lines 171–172)."""
    conv_dir = tmp_path / "chat-conv"
    jsonl = conv_dir / "conversation.jsonl"
    msgs = [_msg("user", "hello"), _msg("assistant", "hi there")]
    _write_conv_jsonl(jsonl, msgs)

    runner = CliRunner()
    result = runner.invoke(audit_log, [str(jsonl)], catch_exceptions=False)
    assert result.exit_code == 0
    assert "No computer-use actions found." in result.output


def test_audit_log_cli_table_output(tmp_path):
    """Default (non-JSON) table output includes coordinate, redacted text, and observe_desktop details (lines 187, 189, 191)."""
    conv_dir = tmp_path / "table-conv"
    jsonl = conv_dir / "conversation.jsonl"
    msgs = [
        _msg(
            "assistant", _ipython_block("computer('left_click', coordinate=(100, 200))")
        ),
        _msg("assistant", _ipython_block("computer('type', text='hello')")),
        _msg("assistant", _ipython_block("observe_desktop()")),
    ]
    _write_conv_jsonl(jsonl, msgs)

    runner = CliRunner()
    result = runner.invoke(audit_log, [str(jsonl)], catch_exceptions=False)
    assert result.exit_code == 0
    assert "Timestamp" in result.output  # table header
    assert "@ [100, 200]" in result.output  # coordinate detail (line 187)
    assert "chars, redacted" in result.output  # text_len detail (line 189)
    assert "via observe_desktop()" in result.output  # source detail (line 191)


def test_audit_log_cli_named_conv_not_found(tmp_path, monkeypatch):
    """Named conversation not in logs_dir prints an error and exits 1 (lines 140–141)."""
    monkeypatch.setattr("gptme.cli.cmd_computer.get_logs_dir", lambda: tmp_path)

    runner = CliRunner()
    result = runner.invoke(audit_log, ["nonexistent-conv"], catch_exceptions=False)
    assert result.exit_code == 1
    assert "not found" in result.output


def test_audit_log_cli_scan_recent_conversations(tmp_path, monkeypatch):
    """--last N scans the N most-recent conversations from logs_dir (lines 148–156)."""
    monkeypatch.setattr("gptme.cli.cmd_computer.get_logs_dir", lambda: tmp_path)

    for name in ["conv-a", "conv-b"]:
        msgs = [_msg("assistant", _ipython_block("computer('screenshot')"))]
        _write_conv_jsonl(tmp_path / name / "conversation.jsonl", msgs)

    runner = CliRunner()
    result = runner.invoke(audit_log, ["--last", "2", "--json"], catch_exceptions=False)
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 2
    conv_names = {r["conversation"] for r in data}
    assert "conv-a" in conv_names
    assert "conv-b" in conv_names


def test_audit_log_cli_empty_logs_dir(tmp_path, monkeypatch):
    """logs_dir exists but has no conversation subdirectories → 'No conversations found.' (lines 155–156)."""
    monkeypatch.setattr("gptme.cli.cmd_computer.get_logs_dir", lambda: tmp_path)
    # tmp_path exists but is empty
    runner = CliRunner()
    result = runner.invoke(audit_log, [], catch_exceptions=False)
    assert result.exit_code == 0
    assert "No conversations found." in result.output


def test_audit_log_cli_corrupted_jsonl_warns(tmp_path, monkeypatch):
    """When _gen_read_jsonl raises, a warning is printed and processing continues (lines 162–164)."""
    conv_dir = tmp_path / "corrupt-conv"
    jsonl = conv_dir / "conversation.jsonl"
    _write_conv_jsonl(
        jsonl, [_msg("assistant", _ipython_block("computer('screenshot')"))]
    )

    def _explode(path):
        raise OSError("permission denied")

    monkeypatch.setattr("gptme.cli.cmd_computer._gen_read_jsonl", _explode)

    runner = CliRunner()
    result = runner.invoke(audit_log, [str(jsonl)], catch_exceptions=False)
    assert result.exit_code == 0
    assert "Warning: could not read" in result.output


def test_empty_code_block_skipped():
    """An ipython block with empty content hits the continue guard (line 59)."""
    msgs = [_msg("assistant", "```ipython\n\n```")]
    records = _extract_computer_calls(msgs)
    assert records == []
