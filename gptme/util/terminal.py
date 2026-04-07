"""Utilities for terminal manipulation."""

import importlib
import os
import sys
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


def set_current_conv_name(name: str | None) -> None:
    """Set the current conversation name."""
    global _current_conv_name
    _current_conv_name = name


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
    _set_raw_title(_make_title(state))


def reset_terminal_title() -> None:
    """Reset the terminal title to its default."""
    if not sys.stdout.isatty():
        return

    # Set default title with conversation name if available
    _set_raw_title(_make_title())
