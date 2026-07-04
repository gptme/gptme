"""Tests for the sensitive-action confirmation gate (_computer_gate.py).

Validates that:
- The gate is disabled by default (back-compat)
- GPTME_COMPUTER_CONFIRM_SENSITIVE=1 blocks non-interactive sensitive actions
- GPTME_COMPUTER_CONFIRM_SENSITIVE=auto-allow permits sensitive actions without prompting
- Non-sensitive actions always pass through regardless of mode
- fill_element and left_click_drag are gated; left_click is not
"""

from __future__ import annotations

import pytest

from gptme.tools._computer_gate import (
    GATE_ACTIONS_BROWSER,
    GATE_ACTIONS_COMPUTER,
    sensitive_action_gate,
)

# ---------------------------------------------------------------------------
# Default behaviour: gate disabled
# ---------------------------------------------------------------------------


def test_gate_disabled_by_default_type(monkeypatch):
    """type() proceeds silently when GPTME_COMPUTER_CONFIRM_SENSITIVE is unset."""
    monkeypatch.delenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", raising=False)
    sensitive_action_gate("type", "hello")  # must not raise


def test_gate_disabled_by_default_key(monkeypatch):
    monkeypatch.delenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", raising=False)
    sensitive_action_gate("key", "Return")


def test_gate_disabled_by_default_fill_element(monkeypatch):
    monkeypatch.delenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", raising=False)
    sensitive_action_gate("fill_element", "secret", is_browser=True)


def test_gate_disabled_explicit_zero(monkeypatch):
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "0")
    sensitive_action_gate("type", "secret")  # must not raise


def test_gate_does_not_block_non_sensitive_actions(monkeypatch):
    """Non-sensitive actions are always allowed regardless of gate mode."""
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "1")
    for action in (
        "screenshot",
        "left_click",
        "scroll",
        "open_page",
        "cursor_position",
    ):
        sensitive_action_gate(action)  # must not raise


# ---------------------------------------------------------------------------
# auto-allow mode: gate enabled but approves unconditionally
# ---------------------------------------------------------------------------


def test_gate_auto_allow_permits_type(monkeypatch):
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "auto-allow")
    sensitive_action_gate("type", "hunter2")  # must not raise


def test_gate_auto_allow_permits_key(monkeypatch):
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "auto-allow")
    sensitive_action_gate("key", "ctrl+c")


def test_gate_auto_allow_permits_fill_element(monkeypatch):
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "auto-allow")
    sensitive_action_gate("fill_element", "password", is_browser=True)


def test_gate_auto_allow_permits_left_click_drag(monkeypatch):
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "auto-allow")
    sensitive_action_gate("left_click_drag")


# ---------------------------------------------------------------------------
# Enabled + non-interactive: sensitive actions are blocked
# ---------------------------------------------------------------------------


def test_gate_blocks_type_in_noninteractive(monkeypatch):
    """type() raises PermissionError in non-interactive mode when gate=1."""
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "1")
    monkeypatch.setattr("sys.stdin", _fake_noninteractive_stdin())
    with pytest.raises(PermissionError, match="type"):
        sensitive_action_gate("type", "hunter2")


def test_gate_blocks_key_in_noninteractive(monkeypatch):
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "1")
    monkeypatch.setattr("sys.stdin", _fake_noninteractive_stdin())
    with pytest.raises(PermissionError, match="key"):
        sensitive_action_gate("key", "Return")


def test_gate_blocks_left_click_drag_in_noninteractive(monkeypatch):
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "1")
    monkeypatch.setattr("sys.stdin", _fake_noninteractive_stdin())
    with pytest.raises(PermissionError, match="left_click_drag"):
        sensitive_action_gate("left_click_drag")


def test_gate_blocks_fill_element_in_noninteractive(monkeypatch):
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "1")
    monkeypatch.setattr("sys.stdin", _fake_noninteractive_stdin())
    with pytest.raises(PermissionError, match="fill_element"):
        sensitive_action_gate("fill_element", "secret", is_browser=True)


def test_gate_error_message_hides_content(monkeypatch):
    """The PermissionError message must not contain the actual text value."""
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "1")
    monkeypatch.setattr("sys.stdin", _fake_noninteractive_stdin())
    with pytest.raises(PermissionError) as exc_info:
        sensitive_action_gate("type", "mysecret")
    assert "mysecret" not in str(exc_info.value)


def test_gate_error_message_includes_char_count(monkeypatch):
    """The PermissionError message should include the character count."""
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "1")
    monkeypatch.setattr("sys.stdin", _fake_noninteractive_stdin())
    with pytest.raises(PermissionError) as exc_info:
        sensitive_action_gate("type", "hello")
    # "5 chars" or "5 chars" should appear
    assert "5" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Interactive mode: user approval / denial
# ---------------------------------------------------------------------------


def test_gate_interactive_allows_on_yes(monkeypatch, capsys):
    """'y' at the prompt allows the action."""
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "1")
    monkeypatch.setattr("sys.stdin", _fake_interactive_stdin("y"))
    # Must not raise
    sensitive_action_gate("type", "hello")


def test_gate_interactive_allows_on_yes_full(monkeypatch):
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "1")
    monkeypatch.setattr("sys.stdin", _fake_interactive_stdin("yes"))
    sensitive_action_gate("key", "Return")


def test_gate_interactive_blocks_on_no(monkeypatch):
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "1")
    monkeypatch.setattr("sys.stdin", _fake_interactive_stdin("n"))
    with pytest.raises(PermissionError, match="denied by user"):
        sensitive_action_gate("type", "hello")


def test_gate_interactive_blocks_on_empty(monkeypatch):
    """Empty reply defaults to N (deny)."""
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "1")
    monkeypatch.setattr("sys.stdin", _fake_interactive_stdin(""))
    with pytest.raises(PermissionError, match="denied by user"):
        sensitive_action_gate("type", "hello")


def test_gate_interactive_blocks_on_eof(monkeypatch):
    """EOFError at prompt (piped empty stdin) defaults to deny."""
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "1")
    monkeypatch.setattr("sys.stdin", _fake_interactive_stdin_eof())
    with pytest.raises(PermissionError, match="denied by user"):
        sensitive_action_gate("type", "hello")


# ---------------------------------------------------------------------------
# Action set membership
# ---------------------------------------------------------------------------


def test_gate_actions_computer_contents():
    assert "type" in GATE_ACTIONS_COMPUTER
    assert "key" in GATE_ACTIONS_COMPUTER
    assert "left_click_drag" in GATE_ACTIONS_COMPUTER
    assert "left_click" not in GATE_ACTIONS_COMPUTER
    assert "screenshot" not in GATE_ACTIONS_COMPUTER


def test_gate_actions_browser_contents():
    assert "fill_element" in GATE_ACTIONS_BROWSER
    assert "click_element" not in GATE_ACTIONS_BROWSER
    assert "open_page" not in GATE_ACTIONS_BROWSER


def test_is_browser_flag_routes_correctly(monkeypatch):
    """fill_element is only gated when is_browser=True."""
    monkeypatch.setenv("GPTME_COMPUTER_CONFIRM_SENSITIVE", "auto-allow")
    # With is_browser=True: gated (passes because auto-allow)
    sensitive_action_gate("fill_element", "val", is_browser=True)
    # With is_browser=False: fill_element is not in GATE_ACTIONS_COMPUTER → passes
    sensitive_action_gate("fill_element", "val", is_browser=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeStdin:
    """Fake stdin whose isatty() and readline() are controllable."""

    def __init__(self, *, is_tty: bool, line: str | None):
        self._is_tty = is_tty
        self._line = line  # None triggers EOFError

    def isatty(self) -> bool:
        return self._is_tty

    def readline(self) -> str:
        if self._line is None:
            raise EOFError
        return self._line + "\n"


def _fake_noninteractive_stdin() -> _FakeStdin:
    return _FakeStdin(is_tty=False, line=None)


def _fake_interactive_stdin(answer: str) -> _FakeStdin:
    return _FakeStdin(is_tty=True, line=answer)


def _fake_interactive_stdin_eof() -> _FakeStdin:
    return _FakeStdin(is_tty=True, line=None)
