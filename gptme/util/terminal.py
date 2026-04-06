"""Utilities for terminal manipulation."""

import importlib
import os
import shutil
import sys
import unicodedata
from contextlib import contextmanager
from typing import Any

# Platform-specific imports for stdin flushing
try:
    termios: Any = importlib.import_module("termios")
except ImportError:
    termios = None

_msvcrt: Any = None
if os.name == "nt":
    try:
        import msvcrt as _msvcrt
    except ImportError:
        pass


def flush_stdin() -> None:
    """Flush stdin to clear any buffered input before prompting."""
    if termios and sys.stdin.isatty():
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    elif _msvcrt:
        while _msvcrt.kbhit():
            _msvcrt.getch()


# Global state for conversation name
_current_conv_name: str | None = None
_current_terminal_state: str | None = None


@contextmanager
def terminal_state_title(state: str | None = None):
    """Context manager for setting terminal title with state.

    Args:
        state: Current state (with emoji)
    """
    try:
        set_terminal_state(state)
        yield
    finally:
        reset_terminal_title()


def _status_line_enabled() -> bool:
    """Return True when the experimental status line should be rendered."""
    return sys.stdout.isatty() and os.environ.get("GPTME_STATUS_LINE", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _char_display_width(char: str) -> int:
    """Return the terminal column width for a single character."""
    if unicodedata.combining(char):
        return 0
    if unicodedata.east_asian_width(char) in {"F", "W"}:
        return 2
    return 1


def _display_width(text: str) -> int:
    """Return the terminal column width for a string."""
    return sum(_char_display_width(char) for char in text)


def _truncate_status_line(text: str, width: int) -> str:
    """Clamp the status line to terminal width, preserving readability."""
    if width <= 0:
        return ""
    if _display_width(text) <= width:
        return text
    if width <= 3:
        truncated = ""
        current_width = 0
        for char in text:
            char_width = _char_display_width(char)
            if current_width + char_width > width:
                break
            truncated += char
            current_width += char_width
        return truncated

    max_width = width - 3
    truncated = ""
    current_width = 0
    for char in text:
        char_width = _char_display_width(char)
        if current_width + char_width > max_width:
            break
        truncated += char
        current_width += char_width
    return truncated + "..."


def _get_default_model_name() -> str | None:
    """Get the current default model name without creating a hard dependency."""
    try:
        from gptme.llm.models import get_default_model

        model = get_default_model()
        return model.full if model else None
    except Exception:
        return None


def _make_status_line(state: str | None = None) -> str:
    """Build a compact one-line status summary for interactive sessions."""
    parts = ["gptme"]
    if model := _get_default_model_name():
        parts.append(model)
    if state:
        parts.append(state)
    if _current_conv_name:
        parts.append(_current_conv_name)
    return " | ".join(parts)


def _render_status_line() -> None:
    """Render the experimental bottom status line without moving the cursor."""
    if not _status_line_enabled():
        return

    text = _truncate_status_line(
        _make_status_line(_current_terminal_state),
        shutil.get_terminal_size((80, 24)).columns,
    )
    print(
        f"\0337\033[999;1H\033[2K{text}\0338",
        end="",
        flush=True,
    )


def clear_status_line() -> None:
    """Clear the experimental bottom status line if enabled."""
    if not _status_line_enabled():
        return
    print("\0337\033[999;1H\033[2K\0338", end="", flush=True)


def set_current_conv_name(
    name: str | None, *, refresh_status_line: bool = True
) -> None:
    """Set the current conversation name and refresh terminal UI."""
    global _current_conv_name
    _current_conv_name = name
    _set_raw_title(_make_title(_current_terminal_state))
    if refresh_status_line:
        _render_status_line()


def get_current_conv_name() -> str | None:
    """Get the current conversation name."""
    return _current_conv_name


def _make_title(state: str | None = None) -> str:
    """Create a consistent terminal title.

    Args:
        state: Current state (with emoji)
    """
    result = "gptme"
    if state:
        result += f" - {state}"
    if _current_conv_name:
        result += f" - {_current_conv_name}"
    return result


def _set_raw_title(raw_title: str) -> None:
    """Set the terminal title using ANSI escape sequences.

    Works in most terminal emulators that support ANSI escape sequences.
    """
    if not sys.stdout.isatty():
        return

    # Different terminals use different escape sequences
    # This one is widely supported
    print(f"\033]0;{raw_title}\007", end="", flush=True)


def set_terminal_title(raw_title: str) -> None:
    """Set the terminal title directly."""
    _set_raw_title(raw_title)


def set_terminal_state(state: str | None = None) -> None:
    """Set the terminal title with a state and current conversation name."""
    global _current_terminal_state
    _current_terminal_state = state
    _set_raw_title(_make_title(state))
    _render_status_line()


def reset_terminal_title() -> None:
    """Reset the terminal title to its default."""
    global _current_terminal_state
    _current_terminal_state = None
    if not sys.stdout.isatty():
        return

    # Set default title with conversation name if available
    _set_raw_title(_make_title())
    _render_status_line()
