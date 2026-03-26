"""Tests for util/terminal.py - terminal title manipulation and conversation state."""

import sys
from io import StringIO
from unittest.mock import patch

import pytest

from gptme.util.terminal import (
    _make_title,
    flush_stdin,
    get_current_conv_name,
    reset_terminal_title,
    set_current_conv_name,
    set_terminal_state,
    set_terminal_title,
    terminal_state_title,
)


@pytest.fixture(autouse=True)
def reset_conv_name():
    """Reset global conversation name state before each test."""
    set_current_conv_name(None)
    yield
    set_current_conv_name(None)


class TestMakeTitle:
    """Tests for _make_title() internal function."""

    def test_default_title(self):
        """Default title without state or conversation name is 'gptme'."""
        set_current_conv_name(None)
        assert _make_title() == "gptme"

    def test_title_with_state(self):
        """Title includes state when provided."""
        set_current_conv_name(None)
        result = _make_title(state="🤔 thinking")
        assert result == "gptme - 🤔 thinking"

    def test_title_with_conv_name(self):
        """Title includes conversation name when set."""
        set_current_conv_name("my-conversation")
        result = _make_title()
        assert result == "gptme - my-conversation"

    def test_title_with_state_and_conv_name(self):
        """Title includes both state and conversation name."""
        set_current_conv_name("my-conv")
        result = _make_title(state="✅ done")
        assert result == "gptme - ✅ done - my-conv"

    def test_title_with_none_state(self):
        """None state is treated as no state."""
        set_current_conv_name(None)
        result = _make_title(state=None)
        assert result == "gptme"

    def test_title_starts_with_gptme(self):
        """Title always starts with 'gptme'."""
        for state in (None, "thinking", "done"):
            result = _make_title(state=state)
            assert result.startswith("gptme")

    def test_title_conv_name_cleared(self):
        """Clearing conv name removes it from title."""
        set_current_conv_name("old-conv")
        assert "old-conv" in _make_title()
        set_current_conv_name(None)
        assert "old-conv" not in _make_title()


class TestConvNameManagement:
    """Tests for get/set_current_conv_name."""

    def test_default_conv_name_is_none(self):
        """Default conversation name is None."""
        set_current_conv_name(None)
        assert get_current_conv_name() is None

    def test_set_conv_name(self):
        """Can set conversation name."""
        set_current_conv_name("test-conversation")
        assert get_current_conv_name() == "test-conversation"

    def test_clear_conv_name(self):
        """Can clear conversation name by setting None."""
        set_current_conv_name("test")
        set_current_conv_name(None)
        assert get_current_conv_name() is None

    def test_update_conv_name(self):
        """Can update conversation name."""
        set_current_conv_name("first")
        set_current_conv_name("second")
        assert get_current_conv_name() == "second"

    def test_set_is_callable(self):
        """set_current_conv_name is callable without error."""
        set_current_conv_name("test")  # Should not raise

    def test_get_returns_string_or_none(self):
        """get_current_conv_name returns str or None."""
        assert get_current_conv_name() is None
        set_current_conv_name("hello")
        assert isinstance(get_current_conv_name(), str)


class TestSetRawTitle:
    """Tests for terminal title setting (no-ops when not a TTY)."""

    def test_set_terminal_title_no_output_when_not_tty(self):
        """set_terminal_title does nothing when stdout is not a TTY."""
        fake_stdout = StringIO()
        with patch.object(sys, "stdout", fake_stdout):
            set_terminal_title("Test Title")
        # No output written (not a TTY)
        assert fake_stdout.getvalue() == ""

    def test_set_terminal_state_no_output_when_not_tty(self):
        """set_terminal_state does nothing when stdout is not a TTY."""
        fake_stdout = StringIO()
        with patch.object(sys, "stdout", fake_stdout):
            set_terminal_state("🤔 thinking")
        assert fake_stdout.getvalue() == ""

    def test_reset_terminal_title_no_output_when_not_tty(self):
        """reset_terminal_title does nothing when stdout is not a TTY."""
        fake_stdout = StringIO()
        with patch.object(sys, "stdout", fake_stdout):
            reset_terminal_title()
        assert fake_stdout.getvalue() == ""

    def test_set_raw_title_writes_ansi_when_tty(self):
        """_set_raw_title writes ANSI escape when stdout is a TTY."""
        from gptme.util.terminal import _set_raw_title

        fake_stdout = StringIO()
        fake_stdout.isatty = lambda: True  # type: ignore[method-assign]
        with patch.object(sys, "stdout", fake_stdout):
            _set_raw_title("Test Title")
        output = fake_stdout.getvalue()
        assert "Test Title" in output
        assert "\033]0;" in output
        assert "\007" in output

    def test_set_terminal_title_writes_ansi_when_tty(self):
        """set_terminal_title writes ANSI escape when stdout is a TTY."""
        fake_stdout = StringIO()
        fake_stdout.isatty = lambda: True  # type: ignore[method-assign]
        with patch.object(sys, "stdout", fake_stdout):
            set_terminal_title("Custom Title")
        output = fake_stdout.getvalue()
        assert "Custom Title" in output

    def test_set_terminal_state_uses_make_title_format(self):
        """set_terminal_state writes title in expected format."""
        fake_stdout = StringIO()
        fake_stdout.isatty = lambda: True  # type: ignore[method-assign]
        set_current_conv_name("my-conv")
        with patch.object(sys, "stdout", fake_stdout):
            set_terminal_state("✅ done")
        output = fake_stdout.getvalue()
        assert "gptme" in output
        assert "✅ done" in output
        assert "my-conv" in output


class TestTerminalStateContextManager:
    """Tests for terminal_state_title context manager."""

    def test_context_manager_calls_reset_on_exit(self):
        """Context manager resets terminal title on exit."""
        reset_calls = []
        set_calls = []

        def mock_set_state(state=None):
            set_calls.append(state)

        def mock_reset():
            reset_calls.append(True)

        with (
            patch("gptme.util.terminal.set_terminal_state", mock_set_state),
            patch("gptme.util.terminal.reset_terminal_title", mock_reset),
            terminal_state_title("🤔 thinking"),
        ):
            pass

        assert len(set_calls) == 1
        assert set_calls[0] == "🤔 thinking"
        assert len(reset_calls) == 1

    def test_context_manager_resets_on_exception(self):
        """Context manager resets title even when exception occurs."""
        reset_calls = []

        def mock_reset():
            reset_calls.append(True)

        with (
            pytest.raises(ValueError, match="test error"),
            patch("gptme.util.terminal.set_terminal_state", lambda state=None: None),
            patch("gptme.util.terminal.reset_terminal_title", mock_reset),
            terminal_state_title("running"),
        ):
            raise ValueError("test error")

        assert len(reset_calls) == 1

    def test_context_manager_yields_none(self):
        """Context manager yields None (no value)."""
        with (
            patch("gptme.util.terminal.set_terminal_state", lambda state=None: None),
            patch("gptme.util.terminal.reset_terminal_title", lambda: None),
            terminal_state_title("state") as value,
        ):
            assert value is None

    def test_context_manager_no_state(self):
        """Context manager works with no state argument."""
        reset_calls = []

        def mock_reset():
            reset_calls.append(True)

        with (
            patch("gptme.util.terminal.set_terminal_state", lambda state=None: None),
            patch("gptme.util.terminal.reset_terminal_title", mock_reset),
            terminal_state_title(),
        ):
            pass

        assert len(reset_calls) == 1


class TestFlushStdin:
    """Tests for flush_stdin()."""

    def test_flush_stdin_no_error_when_not_tty(self):
        """flush_stdin doesn't raise when stdin is not a TTY."""
        fake_stdin = StringIO()
        with patch.object(sys, "stdin", fake_stdin):
            flush_stdin()  # Should not raise

    def test_flush_stdin_is_callable(self):
        """flush_stdin is callable."""
        assert callable(flush_stdin)

    def test_flush_stdin_no_exception(self):
        """flush_stdin completes without exception."""
        flush_stdin()  # Should not raise
