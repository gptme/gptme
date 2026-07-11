"""Tests for gptme-util computer audit-log (cmd_computer.py).

Validates that computer(), observe_desktop(), and browser interaction calls
are extracted from synthetic JSONL trajectories, and that typed/sensitive
text is never logged raw.
"""

from __future__ import annotations

import json
import textwrap
from datetime import datetime, timezone
from pathlib import Path  # noqa: TC003 — used at runtime in _write_conv_jsonl
from typing import Literal

from click.testing import CliRunner

from gptme.cli.cmd_computer import (
    _extract_computer_calls,
    action_risk_level,
    audit_log,
)
from gptme.message import Message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(role: Literal["system", "user", "assistant"], content: str) -> Message:
    ts = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    return Message(role=role, content=content, timestamp=ts)


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
            "risk_level": "read",
        },
        {
            "timestamp": "2026-07-01T12:00:00+00:00",
            "action": "left_click",
            "coordinate": [50, 75],
            "risk_level": "write",
        },
        {
            "timestamp": "2026-07-01T12:00:00+00:00",
            "action": "type",
            "text_len": len("short"),
            "risk_level": "sensitive",
        },
        {
            "timestamp": "2026-07-01T12:00:00+00:00",
            "action": "type",
            "text_len": len("much-longer"),
            "risk_level": "sensitive",
        },
    ]


# ---------------------------------------------------------------------------
# Risk level classification (action_risk_level)
# ---------------------------------------------------------------------------


def test_risk_level_read_actions():
    """Observation-only actions are classified as 'read'."""
    for action in (
        "screenshot",
        "cursor_position",
        "accessibility_tree",
        "wait_for_change",
    ):
        assert action_risk_level(action) == "read", f"Expected 'read' for {action!r}"


def test_risk_level_browser_read_actions():
    """Browser observation functions are classified as 'read'."""
    for action in ("snapshot_url", "observe_web", "read_page_text", "observe_desktop"):
        assert action_risk_level(action) == "read", f"Expected 'read' for {action!r}"


def test_risk_level_write_actions():
    """Click/mouse/scroll actions are classified as 'write'."""
    for action in (
        "left_click",
        "right_click",
        "middle_click",
        "double_click",
        "mouse_move",
        "scroll",
        "window_focus",
        "click_element",
        "scroll_page",
        "open_page",
    ):
        assert action_risk_level(action) == "write", f"Expected 'write' for {action!r}"


def test_risk_level_sensitive_actions():
    """Keyboard input and form fill actions are classified as 'sensitive'."""
    for action in ("type", "key", "left_click_drag", "fill_element"):
        assert action_risk_level(action) == "sensitive", (
            f"Expected 'sensitive' for {action!r}"
        )


def test_risk_level_unknown_action_defaults_to_write():
    """Unknown actions default to 'write' (conservative)."""
    assert action_risk_level("some_unknown_action") == "write"


def test_risk_level_in_extracted_records():
    """Each extracted record includes a 'risk_level' key."""
    code = textwrap.dedent("""\
        computer('screenshot')
        computer('left_click', coordinate=(100, 200))
        computer('type', text='hello')
    """)
    msgs = [_msg("assistant", _ipython_block(code))]
    records = _extract_computer_calls(msgs)
    assert all("risk_level" in r for r in records), "All records must have risk_level"
    assert records[0]["risk_level"] == "read"
    assert records[1]["risk_level"] == "write"
    assert records[2]["risk_level"] == "sensitive"


def test_risk_level_in_act_and_observe_records():
    """act_and_observe records include 'risk_level'."""
    msgs = [
        _msg(
            "assistant",
            _ipython_block("act_and_observe('left_click', coordinate=(100, 200))"),
        )
    ]
    records = _extract_computer_calls(msgs)
    assert records[0]["risk_level"] == "write"


def test_risk_level_in_browser_records():
    """Browser interaction records include 'risk_level'."""
    code = textwrap.dedent("""\
        observe_web('https://example.com')
        fill_element('[name="q"]', 'hello')
        click_element('[type="submit"]')
    """)
    msgs = [_msg("assistant", _ipython_block(code))]
    records = _extract_computer_calls(msgs)
    assert records[0]["risk_level"] == "read"  # observe_web
    assert records[1]["risk_level"] == "sensitive"  # fill_element
    assert records[2]["risk_level"] == "write"  # click_element


def test_audit_log_cli_table_shows_risk_level(tmp_path):
    """Table output includes a Risk column."""
    conv_dir = tmp_path / "risk-conv"
    jsonl = conv_dir / "conversation.jsonl"
    msgs = [
        _msg("assistant", _ipython_block("computer('screenshot')")),
        _msg(
            "assistant", _ipython_block("computer('left_click', coordinate=(10, 20))")
        ),
        _msg("assistant", _ipython_block("computer('type', text='hello')")),
    ]
    _write_conv_jsonl(jsonl, msgs)

    runner = CliRunner()
    result = runner.invoke(audit_log, [str(jsonl)], catch_exceptions=False)
    assert result.exit_code == 0
    assert "Risk" in result.output
    assert "read" in result.output
    assert "write" in result.output
    assert "sensitive" in result.output


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


# ---------------------------------------------------------------------------
# Browser interaction call tracking (observe_web, snapshot_url, open_page,
# click_element, fill_element, read_page_text, scroll_page)
# ---------------------------------------------------------------------------


def test_observe_web_captured():
    msgs = [_msg("assistant", _ipython_block("observe_web('https://example.com')"))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "observe_web"
    assert records[0]["source"] == "browser"
    assert records[0]["url"] == "https://example.com"


def test_observe_web_double_quotes():
    msgs = [
        _msg("assistant", _ipython_block('observe_web("https://news.ycombinator.com")'))
    ]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["url"] == "https://news.ycombinator.com"


def test_snapshot_url_captured():
    msgs = [_msg("assistant", _ipython_block("snapshot_url('https://example.com')"))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "snapshot_url"
    assert records[0]["source"] == "browser"
    assert records[0]["url"] == "https://example.com"


def test_open_page_captured():
    msgs = [
        _msg("assistant", _ipython_block("open_page('https://httpbin.org/forms/post')"))
    ]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "open_page"
    assert records[0]["source"] == "browser"
    assert records[0]["url"] == "https://httpbin.org/forms/post"


def test_click_element_captured():
    msgs = [_msg("assistant", _ipython_block("click_element('[type=\"submit\"]')"))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "click_element"
    assert records[0]["source"] == "browser"
    assert records[0]["selector"] == '[type="submit"]'


def test_fill_element_selector_kept_value_redacted():
    msgs = [
        _msg(
            "assistant", _ipython_block("fill_element('[name=\"custname\"]', 'Alice')")
        )
    ]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    r = records[0]
    assert r["action"] == "fill_element"
    assert r["source"] == "browser"
    assert r["selector"] == '[name="custname"]'
    # Raw value must NOT appear
    assert "value" not in r
    assert r["value_len"] == len("Alice")


def test_fill_element_password_not_logged():
    code = "fill_element('[name=\"password\"]', 'supersecret')"
    msgs = [_msg("assistant", _ipython_block(code))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert "supersecret" not in str(records)
    assert records[0]["value_len"] == len("supersecret")


def test_fill_native_value_redacted():
    """fill_native must log only value length, never raw text."""
    code = "fill_native((300, 200), 'secret-password')"
    msgs = [_msg("assistant", _ipython_block(code))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    r = records[0]
    assert r["action"] == "fill_native"
    assert r["source"] == "computer"
    assert "secret-password" not in str(r), "raw text must not appear in audit record"
    assert r["value_len"] == len("secret-password")


def test_fill_native_risk_is_sensitive():
    """fill_native carries sensitive risk level (handles private text)."""
    code = "fill_native((100, 50), 'alice@example.com')"
    msgs = [_msg("assistant", _ipython_block(code))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["risk_level"] == "sensitive"


def test_fill_native_variable_text_is_audited():
    """fill_native with a variable text arg must still appear in the audit (value_len=None)."""
    code = "fill_native((300, 200), password)"
    msgs = [_msg("assistant", _ipython_block(code))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1, "variable-text fill_native must be audited"
    r = records[0]
    assert r["action"] == "fill_native"
    assert r["value_len"] is None, "value_len must be None when text is a variable"


def test_fill_native_keyword_arg_is_audited():
    """fill_native(coordinate=..., text=variable) must be audited."""
    code = "fill_native(coordinate=(300, 200), text=user_input)"
    msgs = [_msg("assistant", _ipython_block(code))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1, "keyword-arg fill_native must be audited"
    r = records[0]
    assert r["action"] == "fill_native"
    assert r["value_len"] is None


def test_read_page_text_captured():
    msgs = [_msg("assistant", _ipython_block("read_page_text()"))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "read_page_text"
    assert records[0]["source"] == "browser"


def test_scroll_page_captured():
    msgs = [_msg("assistant", _ipython_block("scroll_page('down')"))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "scroll_page"
    assert records[0]["source"] == "browser"
    assert records[0]["direction"] == "down"


def test_mixed_computer_and_browser_calls():
    """Full "Can it Tweet?" style pipeline: open_page + fill + click + read."""
    code = textwrap.dedent("""\
        open_page('https://httpbin.org/forms/post')
        fill_element('[name="custname"]', 'TestUser')
        fill_element('[name="custemail"]', 'test@example.com')
        click_element('[type="submit"]')
        read_page_text()
    """)
    msgs = [_msg("assistant", _ipython_block(code))]
    records = _extract_computer_calls(msgs)
    actions = [r["action"] for r in records]
    assert actions == [
        "open_page",
        "fill_element",
        "fill_element",
        "click_element",
        "read_page_text",
    ]
    # URL captured for open_page
    assert records[0]["url"] == "https://httpbin.org/forms/post"
    # Selectors captured for fill/click
    assert records[1]["selector"] == '[name="custname"]'
    assert records[2]["selector"] == '[name="custemail"]'
    assert records[3]["selector"] == '[type="submit"]'
    # Values redacted
    assert records[1]["value_len"] == len("TestUser")
    assert records[2]["value_len"] == len("test@example.com")


def test_mixed_desktop_browser_source_order():
    """Desktop and browser calls interleaved in one block emit in source order."""
    code = textwrap.dedent("""\
        observe_web('https://example.com')
        computer('screenshot')
        click_element('#btn')
        computer('click', coordinate=(100, 200))
    """)
    msgs = [_msg("assistant", _ipython_block(code))]
    records = _extract_computer_calls(msgs)
    actions = [r["action"] for r in records]
    assert actions == [
        "observe_web",
        "screenshot",
        "click_element",
        "click",
    ], f"Expected source order but got: {actions}"


def test_audit_log_cli_table_shows_browser_url(tmp_path, monkeypatch):
    """Table output shows URL for observe_web/open_page calls."""
    conv_dir = tmp_path / "browser-conv"
    jsonl = conv_dir / "conversation.jsonl"
    msgs = [
        _msg("assistant", _ipython_block("observe_web('https://example.com')")),
        _msg(
            "assistant", _ipython_block("open_page('https://httpbin.org/forms/post')")
        ),
    ]
    _write_conv_jsonl(jsonl, msgs)

    runner = CliRunner()
    result = runner.invoke(audit_log, [str(jsonl)], catch_exceptions=False)
    assert result.exit_code == 0
    assert "https://example.com" in result.output
    assert "https://httpbin.org/forms/post" in result.output


def test_audit_log_cli_table_long_url_truncated(tmp_path):
    """Table output truncates URLs >70 chars with ellipsis."""
    long_url = "https://example.com/" + "a" * 60
    assert len(long_url) > 70
    conv_dir = tmp_path / "long-url-conv"
    jsonl = conv_dir / "conversation.jsonl"
    msgs = [
        _msg("assistant", _ipython_block(f"observe_web('{long_url}')")),
    ]
    _write_conv_jsonl(jsonl, msgs)

    runner = CliRunner()
    result = runner.invoke(audit_log, [str(jsonl)], catch_exceptions=False)
    assert result.exit_code == 0
    # First 70 chars should be present
    assert long_url[:70] in result.output
    # Full URL must NOT appear (would mean no truncation happened)
    assert long_url not in result.output
    # Ellipsis character should appear at truncation boundary
    assert "…" in result.output


def test_audit_log_cli_table_shows_selector_and_value_len(tmp_path):
    """Table output shows selector and value length for fill_element."""
    conv_dir = tmp_path / "fill-conv"
    jsonl = conv_dir / "conversation.jsonl"
    msgs = [
        _msg(
            "assistant", _ipython_block("fill_element('[name=\"q\"]', 'hello world')")
        ),
        _msg("assistant", _ipython_block("click_element('[type=\"submit\"]')")),
    ]
    _write_conv_jsonl(jsonl, msgs)

    runner = CliRunner()
    result = runner.invoke(audit_log, [str(jsonl)], catch_exceptions=False)
    assert result.exit_code == 0
    # Should NOT contain the raw value
    assert "hello world" not in result.output
    # Should show the character count
    assert f"{len('hello world')} chars" in result.output
    # Selector shown for click_element
    assert '[type="submit"]' in result.output


# ---------------------------------------------------------------------------
# New browser functions (PRs #3095/#3104) — audit extraction coverage
# ---------------------------------------------------------------------------


def test_audit_log_cli_table_shows_press_key_and_select_option(tmp_path):
    """Table output shows key for press_key and selector+value for select_option."""
    conv_dir = tmp_path / "browser-actions"
    jsonl = conv_dir / "conversation.jsonl"
    msgs = [
        _msg("assistant", _ipython_block("press_key('Enter')")),
        _msg("assistant", _ipython_block("select_option('[name=\"size\"]', 'large')")),
    ]
    _write_conv_jsonl(jsonl, msgs)

    runner = CliRunner()
    result = runner.invoke(audit_log, [str(jsonl)], catch_exceptions=False)
    assert result.exit_code == 0
    assert "'Enter'" in result.output
    assert '[name="size"]' in result.output
    assert "'large'" in result.output


def test_hover_element_captured():
    """hover_element(selector) appears in the audit log with write risk level."""
    msgs = [_msg("assistant", _ipython_block("hover_element('nav > .dropdown')"))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "hover_element"
    assert records[0]["source"] == "browser"
    assert records[0]["risk_level"] == "write"
    assert records[0]["selector"] == "nav > .dropdown"


def test_press_key_captured():
    """press_key(key) appears in the audit log with write risk level."""
    msgs = [_msg("assistant", _ipython_block("press_key('Enter')"))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "press_key"
    assert records[0]["source"] == "browser"
    assert records[0]["risk_level"] == "write"
    assert records[0]["key"] == "Enter"


def test_select_option_captured():
    """select_option(selector, value) appears with write risk, value logged (not sensitive)."""
    msgs = [
        _msg("assistant", _ipython_block("select_option('[name=\"size\"]', 'large')"))
    ]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "select_option"
    assert records[0]["source"] == "browser"
    assert records[0]["risk_level"] == "write"
    assert records[0]["selector"] == '[name="size"]'
    assert records[0]["value"] == "large"


def test_wait_for_element_captured():
    """wait_for_element(selector) appears in the audit log with read risk level."""
    msgs = [
        _msg(
            "assistant",
            _ipython_block("wait_for_element('[data-testid=\"result\"]')"),
        )
    ]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "wait_for_element"
    assert records[0]["source"] == "browser"
    assert records[0]["risk_level"] == "read"
    assert records[0]["selector"] == '[data-testid="result"]'


def test_snapshot_page_captured():
    """snapshot_page() appears in the audit log with read risk level."""
    msgs = [_msg("assistant", _ipython_block("snapshot_page()"))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "snapshot_page"
    assert records[0]["source"] == "browser"
    assert records[0]["risk_level"] == "read"


def test_get_current_url_captured():
    """get_current_url() appears in the audit log with read risk level."""
    msgs = [_msg("assistant", _ipython_block("get_current_url()"))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "get_current_url"
    assert records[0]["source"] == "browser"
    assert records[0]["risk_level"] == "read"


def test_load_browser_state_captured():
    """load_browser_state(path) appears in the audit log with read risk level."""
    msgs = [
        _msg(
            "assistant",
            _ipython_block("load_browser_state('~/.config/gptme/twitter.json')"),
        )
    ]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "load_browser_state"
    assert records[0]["source"] == "browser"
    assert records[0]["risk_level"] == "read"


# ---------------------------------------------------------------------------
# act_and_observe() audit tracking
# The computer-use profile recommends act_and_observe() as the primary
# "act then look" primitive — it must appear in the audit trail.
# ---------------------------------------------------------------------------


def test_act_and_observe_click_captured():
    """act_and_observe('left_click', coordinate=...) is captured with source=act_and_observe."""
    msgs = [
        _msg(
            "assistant",
            _ipython_block("act_and_observe('left_click', coordinate=(760, 540))"),
        )
    ]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "left_click"
    assert records[0]["source"] == "act_and_observe"
    assert records[0]["coordinate"] == [760, 540]


def test_act_and_observe_type_redacts_text():
    """act_and_observe('type', text=...) logs text length, never raw text."""
    msgs = [
        _msg(
            "assistant",
            _ipython_block("act_and_observe('type', text='secret password')"),
        )
    ]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "type"
    assert records[0]["source"] == "act_and_observe"
    assert "text_len" in records[0]
    assert records[0]["text_len"] == len("secret password")
    # Raw text must not be present
    for v in records[0].values():
        assert "secret" not in str(v)


def test_act_and_observe_screenshot_passthrough_captured():
    """act_and_observe('screenshot') (observation-only) is still captured in the audit log."""
    msgs = [_msg("assistant", _ipython_block("act_and_observe('screenshot')"))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "screenshot"
    assert records[0]["source"] == "act_and_observe"


def test_act_and_observe_double_quotes():
    """Double-quoted action string is captured (act_and_observe("left_click", ...))."""
    msgs = [
        _msg(
            "assistant",
            _ipython_block('act_and_observe("left_click", coordinate=(100, 200))'),
        )
    ]
    records = _extract_computer_calls(msgs)
    assert len(records) == 1
    assert records[0]["action"] == "left_click"
    assert records[0]["source"] == "act_and_observe"
    assert records[0]["coordinate"] == [100, 200]


def test_act_and_observe_interleaved_with_computer():
    """act_and_observe() and computer() calls in the same block are emitted in source order."""
    code = textwrap.dedent("""\
        computer('screenshot')
        act_and_observe('left_click', coordinate=(50, 50))
        computer('type', text='hello')
    """)
    msgs = [_msg("assistant", _ipython_block(code))]
    records = _extract_computer_calls(msgs)
    assert len(records) == 3
    assert records[0]["action"] == "screenshot"
    assert records[0].get("source") is None  # native computer() call
    assert records[1]["action"] == "left_click"
    assert records[1]["source"] == "act_and_observe"
    assert records[2]["action"] == "type"
    assert records[2].get("source") is None  # native computer() call


def test_act_and_observe_table_output(tmp_path):
    """Table output shows 'via act_and_observe()' and coordinate for click actions."""
    conv_dir = tmp_path / "aao-conv"
    jsonl = conv_dir / "conversation.jsonl"

    msgs = [
        _msg(
            "assistant",
            _ipython_block("act_and_observe('left_click', coordinate=(760, 540))"),
        )
    ]
    _write_conv_jsonl(jsonl, msgs)

    runner = CliRunner()
    result = runner.invoke(audit_log, [str(jsonl)], catch_exceptions=False)
    assert result.exit_code == 0
    assert "via act_and_observe()" in result.output
    assert "[760, 540]" in result.output


# ---------------------------------------------------------------------------
# --jsonl output format (#216 audit export gap)
# ---------------------------------------------------------------------------


def test_audit_log_cli_jsonl_output(tmp_path, monkeypatch):
    """--jsonl outputs one JSON object per line (newline-delimited JSON)."""
    conv_dir = tmp_path / "jsonl-test-conv"
    jsonl = conv_dir / "conversation.jsonl"
    msgs = [
        _msg("assistant", _ipython_block("computer('screenshot')")),
        _msg(
            "assistant", _ipython_block("computer('left_click', coordinate=(100, 200))")
        ),
    ]
    _write_conv_jsonl(jsonl, msgs)
    monkeypatch.setattr("gptme.cli.cmd_computer.get_logs_dir", lambda: tmp_path)

    runner = CliRunner()
    result = runner.invoke(audit_log, [str(jsonl), "--jsonl"], catch_exceptions=False)
    assert result.exit_code == 0

    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 2

    record0 = json.loads(lines[0])
    assert record0["action"] == "screenshot"

    record1 = json.loads(lines[1])
    assert record1["action"] == "left_click"
    assert record1["coordinate"] == [100, 200]


def test_audit_log_cli_jsonl_each_line_valid_json(tmp_path, monkeypatch):
    """Every line in --jsonl output must be valid JSON independently."""
    conv_dir = tmp_path / "jsonl-validity-conv"
    jsonl = conv_dir / "conversation.jsonl"
    msgs = [
        _msg(
            "assistant",
            _ipython_block(
                "computer('type', text='hello')\ncomputer('key', text='Return')\ncomputer('screenshot')"
            ),
        )
    ]
    _write_conv_jsonl(jsonl, msgs)
    monkeypatch.setattr("gptme.cli.cmd_computer.get_logs_dir", lambda: tmp_path)

    runner = CliRunner()
    result = runner.invoke(audit_log, [str(jsonl), "--jsonl"], catch_exceptions=False)
    assert result.exit_code == 0

    for line in result.output.splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)  # raises if invalid JSON
        assert "action" in obj
        assert "risk_level" in obj


def test_audit_log_cli_jsonl_sensitive_text_redacted(tmp_path, monkeypatch):
    """--jsonl preserves the text_len redaction (text content never logged raw)."""
    conv_dir = tmp_path / "jsonl-redact-conv"
    jsonl = conv_dir / "conversation.jsonl"
    msgs = [_msg("assistant", _ipython_block("computer('type', text='mysecret')"))]
    _write_conv_jsonl(jsonl, msgs)
    monkeypatch.setattr("gptme.cli.cmd_computer.get_logs_dir", lambda: tmp_path)

    runner = CliRunner()
    result = runner.invoke(audit_log, [str(jsonl), "--jsonl"], catch_exceptions=False)
    assert result.exit_code == 0

    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["action"] == "type"
    assert "text_len" in record
    assert "mysecret" not in result.output


def test_audit_log_cli_jsonl_compact_no_whitespace(tmp_path, monkeypatch):
    """--jsonl emits compact JSON (no indentation), one object per line."""
    conv_dir = tmp_path / "jsonl-compact-conv"
    jsonl = conv_dir / "conversation.jsonl"
    msgs = [_msg("assistant", _ipython_block("computer('screenshot')"))]
    _write_conv_jsonl(jsonl, msgs)
    monkeypatch.setattr("gptme.cli.cmd_computer.get_logs_dir", lambda: tmp_path)

    runner = CliRunner()
    result = runner.invoke(audit_log, [str(jsonl), "--jsonl"], catch_exceptions=False)
    assert result.exit_code == 0

    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1
    # Compact: no newlines within the JSON object itself
    assert "\n" not in lines[0]
    # No 2-space indent (which --json uses)
    assert "  " not in lines[0]


def test_audit_log_cli_jsonl_and_json_mutually_exclusive(tmp_path, monkeypatch):
    """--json and --jsonl together exit with an error."""
    conv_dir = tmp_path / "jsonl-mutex-conv"
    jsonl = conv_dir / "conversation.jsonl"
    msgs = [_msg("assistant", _ipython_block("computer('screenshot')"))]
    _write_conv_jsonl(jsonl, msgs)
    monkeypatch.setattr("gptme.cli.cmd_computer.get_logs_dir", lambda: tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        audit_log, [str(jsonl), "--json", "--jsonl"], catch_exceptions=False
    )
    assert result.exit_code == 1


def test_audit_log_cli_jsonl_and_json_mutually_exclusive_empty_log(
    tmp_path, monkeypatch
):
    """--json and --jsonl conflict is rejected even when no records exist."""
    conv_dir = tmp_path / "jsonl-empty-mutex-conv"
    jsonl = conv_dir / "conversation.jsonl"
    _write_conv_jsonl(jsonl, [_msg("user", "no computer calls here")])
    monkeypatch.setattr("gptme.cli.cmd_computer.get_logs_dir", lambda: tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        audit_log, [str(jsonl), "--json", "--jsonl"], catch_exceptions=False
    )
    assert result.exit_code == 1
    assert "--json and --jsonl are mutually exclusive" in result.output
    assert "No computer-use actions found" not in result.output


def test_audit_log_cli_jsonl_risk_levels_present(tmp_path, monkeypatch):
    """--jsonl output includes risk_level for every record."""
    conv_dir = tmp_path / "jsonl-risk-conv"
    jsonl = conv_dir / "conversation.jsonl"
    code = (
        "computer('screenshot')\n"  # read
        "computer('left_click', coordinate=(10, 20))\n"  # write
        "computer('type', text='pw')"  # sensitive
    )
    msgs = [_msg("assistant", _ipython_block(code))]
    _write_conv_jsonl(jsonl, msgs)
    monkeypatch.setattr("gptme.cli.cmd_computer.get_logs_dir", lambda: tmp_path)

    runner = CliRunner()
    result = runner.invoke(audit_log, [str(jsonl), "--jsonl"], catch_exceptions=False)
    assert result.exit_code == 0

    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 3
    risks = [json.loads(line)["risk_level"] for line in lines]
    assert risks == ["read", "write", "sensitive"]


# ---------------------------------------------------------------------------
# --agent-id option: resolve computer_task() agent IDs to subagent conversations
# ---------------------------------------------------------------------------


def test_audit_log_agent_id_resolves_subagent_prefix(tmp_path, monkeypatch):
    """--agent-id computer-task-abc123 looks up suffixed subagent logs."""
    agent_id = "computer-task-abc12345"
    # Thread-mode subagent conversations add a random suffix to the logdir.
    conv_dir = tmp_path / f"subagent-{agent_id}-r3k9"
    jsonl = conv_dir / "conversation.jsonl"
    msgs = [
        _msg("user", "take a screenshot"),
        _msg("assistant", _ipython_block("computer('screenshot')")),
    ]
    _write_conv_jsonl(jsonl, msgs)
    monkeypatch.setattr("gptme.cli.cmd_computer.get_logs_dir", lambda: tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        audit_log, ["--agent-id", agent_id, "--json"], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    records = json.loads(result.output)
    assert len(records) == 1
    assert records[0]["action"] == "screenshot"


def test_audit_log_agent_id_rejects_multiple_matches(tmp_path, monkeypatch):
    """--agent-id does not silently choose between multiple matching logs."""
    agent_id = "computer-task-duplicate"
    msgs = [_msg("assistant", _ipython_block("computer('screenshot')"))]
    _write_conv_jsonl(
        tmp_path / f"subagent-{agent_id}-aaaa" / "conversation.jsonl", msgs
    )
    _write_conv_jsonl(
        tmp_path / f"subagent-{agent_id}-bbbb" / "conversation.jsonl", msgs
    )
    monkeypatch.setattr("gptme.cli.cmd_computer.get_logs_dir", lambda: tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        audit_log, ["--agent-id", agent_id, "--json"], catch_exceptions=False
    )
    assert result.exit_code != 0
    assert "multiple conversations found" in result.output
    assert f"subagent-{agent_id}-aaaa" in result.output
    assert f"subagent-{agent_id}-bbbb" in result.output


def test_audit_log_agent_id_missing_prints_error(tmp_path, monkeypatch):
    """--agent-id for a non-existent subagent prints an actionable error."""
    monkeypatch.setattr("gptme.cli.cmd_computer.get_logs_dir", lambda: tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        audit_log, ["--agent-id", "computer-task-nothere"], catch_exceptions=False
    )
    assert result.exit_code != 0
    assert "computer-task-nothere" in result.output


def test_audit_log_agent_id_fallback_bare_name(tmp_path, monkeypatch):
    """--agent-id falls back to a bare conversation name (no subagent- prefix)."""
    agent_id = "computer-task-fallback1"
    # Stored WITHOUT the "subagent-" prefix (e.g. legacy or subprocess mode)
    conv_dir = tmp_path / agent_id
    jsonl = conv_dir / "conversation.jsonl"
    msgs = [
        _msg(
            "assistant", _ipython_block("computer('left_click', coordinate=(10, 20))")
        ),
    ]
    _write_conv_jsonl(jsonl, msgs)
    monkeypatch.setattr("gptme.cli.cmd_computer.get_logs_dir", lambda: tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        audit_log, ["--agent-id", agent_id, "--json"], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    records = json.loads(result.output)
    assert len(records) == 1
    assert records[0]["action"] == "left_click"


def test_audit_log_agent_id_and_conversation_are_mutually_exclusive(
    tmp_path, monkeypatch
):
    """Passing both selectors is rejected instead of silently ignoring one."""
    monkeypatch.setattr("gptme.cli.cmd_computer.get_logs_dir", lambda: tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        audit_log,
        ["some-session", "--agent-id", "computer-task-abc12345"],
        catch_exceptions=False,
    )
    assert result.exit_code != 0
    assert "--agent-id and CONVERSATION are mutually exclusive" in result.output
