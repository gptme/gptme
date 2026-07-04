"""Tests for the ContextVar-based output format and quiet mode.

Verifies that:
- "quiet" is a valid format that suppresses print_msg output
- Thread-mode subagent threads have their own isolated ContextVar copy
  so set_output_format("quiet") in one thread does not affect another
"""

import threading
from io import StringIO
from unittest.mock import patch

from gptme.message import (
    Message,
    get_output_format,
    is_output_json,
    is_output_quiet,
    set_output_format,
)


def test_quiet_is_valid_format():
    saved = get_output_format()
    try:
        set_output_format("quiet")
        assert get_output_format() == "quiet"
    finally:
        set_output_format(saved)


def test_is_output_quiet_false_by_default():
    assert not is_output_quiet()


def test_is_output_quiet_true_when_set():
    saved = get_output_format()
    try:
        set_output_format("quiet")
        assert is_output_quiet()
        assert not is_output_json()
    finally:
        set_output_format(saved)


def test_invalid_format_raises():
    import pytest

    with pytest.raises(AssertionError):
        set_output_format("invalid")


def test_print_msg_suppressed_in_quiet_mode():
    """print_msg should produce no output when format is 'quiet'."""
    saved = get_output_format()
    try:
        set_output_format("quiet")
        msg = Message("assistant", "Should not appear")
        # Patch console.print so we can check it's never called
        with patch("gptme.message.console") as mock_console:
            from gptme.message import print_msg

            print_msg(msg)
            mock_console.print.assert_not_called()
    finally:
        set_output_format(saved)


def test_print_msg_not_suppressed_in_text_mode():
    """print_msg should produce output when format is 'text'."""
    saved = get_output_format()
    try:
        set_output_format("text")
        msg = Message("assistant", "Should appear")
        with (
            patch("gptme.message.console") as mock_console,
            patch("sys.stdout", new_callable=StringIO),
        ):
            from gptme.message import print_msg

            print_msg(msg)
            # console.print should have been called at least once
            assert mock_console.print.called
    finally:
        set_output_format(saved)


def test_thread_output_format_is_isolated():
    """Each thread should have its own ContextVar copy.

    Setting 'quiet' in a subagent thread must not affect the parent thread's
    format, which is the core requirement for thread-mode output concealment.
    """
    parent_formats: list[str] = []
    thread_formats: list[str] = []

    # Parent starts as 'text' (default)
    assert get_output_format() == "text"

    def subagent_work():
        # Simulate what execution.py does before calling chat()
        set_output_format("quiet")
        thread_formats.append(get_output_format())
        # Give parent thread time to check its own format
        import time

        time.sleep(0.05)
        thread_formats.append(get_output_format())

    t = threading.Thread(target=subagent_work)
    t.start()

    # Parent format must remain 'text' while subagent thread sets 'quiet'
    parent_formats.append(get_output_format())
    t.join()
    parent_formats.append(get_output_format())

    # Subagent thread saw 'quiet' both times
    assert thread_formats == ["quiet", "quiet"]
    # Parent thread always saw 'text'
    assert parent_formats == ["text", "text"]


def test_save_restore_pattern_still_works():
    """The existing save/restore pattern in chat.py must still work correctly."""
    saved = get_output_format()
    try:
        set_output_format("text")
        prev = get_output_format()
        set_output_format("json")
        assert get_output_format() == "json"
        set_output_format(prev)
        assert get_output_format() == "text"
    finally:
        set_output_format(saved)
