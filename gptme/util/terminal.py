"""Utilities for terminal manipulation."""

import importlib
import os
import re
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


# CSI sequences are removed whole. For every other escape (ESC c, OSC, DCS, ...)
# dropping the control bytes leaves the rest as inert, visible text.
_CSI_SEQUENCE = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]")
# C0, DEL and C1, keeping tab, newline, vertical tab, form feed and carriage return.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0e-\x1f\x7f-\x9f]")


def strip_terminal_controls(text: str) -> str:
    """Remove terminal control sequences from untrusted text.

    A lone carriage return becomes a newline so text cannot overwrite the line
    it is printed on.
    """
    text = _CONTROL_CHARS.sub("", _CSI_SEQUENCE.sub("", text))
    return text.replace("\r\n", "\n").replace("\r", "\n")


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


# Full-screen apps (the TUI) set the title for their whole run and restore the
# previous one on exit via the xterm title stack (supported by xterm, tmux,
# VTE, kitty, …; ignored elsewhere, where the shell's next prompt resets it).
PUSH_TITLE = "\033[22;0t"
POP_TITLE = "\033[23;0t"

# Stable prefix, so window-title rules (tmux, ActivityWatch) can match gptme.
APP_TITLE_PREFIX = "gptme: "


def app_title(name: str | None) -> str:
    """Terminal title for a running gptme app showing conversation *name*."""
    return f"{APP_TITLE_PREFIX}{name}" if name else APP_TITLE_PREFIX.rstrip(": ")


def title_sequence(title: str) -> str:
    """The OSC 0 sequence setting the window/icon title to *title*.

    Control characters are stripped: the title can be model-generated, and an
    embedded ESC/BEL would end the sequence early and inject the rest.
    """
    safe = "".join(c for c in title if c.isprintable())
    return f"\033]0;{safe}\007"
