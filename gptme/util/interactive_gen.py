"""
Interactive generation support - allows user input during LLM generation.

Provides a simple mechanism to detect when the user types input during
generation and offer them the choice to interrupt, queue, or cancel.
"""

import logging
import select
import sys
from enum import Enum, auto

from . import console

logger = logging.getLogger(__name__)


class InputAction(Enum):
    """Action to take when user submits input during generation."""

    INTERRUPT = auto()  # Interrupt generation, use input immediately
    QUEUE = auto()  # Queue input for after generation completes
    CANCEL = auto()  # Discard input, continue generation


# Global state for queued input and interrupt content
_queued_input: str | None = None
_interrupt_content: str | None = None


def check_stdin_has_data() -> bool:
    """
    Check if there's data available on stdin without blocking.

    Returns True if the user has typed something and pressed Enter.
    """
    if not sys.stdin.isatty():
        return False

    try:
        readable, _, _ = select.select([sys.stdin], [], [], 0)
        return bool(readable)
    except Exception:
        return False


def read_stdin_nonblocking() -> str | None:
    """
    Read available data from stdin without blocking.

    Returns the input string if available, None otherwise.
    """
    if not sys.stdin.isatty():
        return None

    try:
        readable, _, _ = select.select([sys.stdin], [], [], 0)
        if not readable:
            return None

        # Read all available characters
        chars = []
        while True:
            readable, _, _ = select.select([sys.stdin], [], [], 0)
            if not readable:
                break
            char = sys.stdin.read(1)
            if not char:
                break
            chars.append(char)

        if chars:
            return "".join(chars).strip()
    except Exception as e:
        logger.debug(f"Error reading stdin: {e}")

    return None


def show_input_dialog(input_text: str) -> tuple[InputAction, str]:
    """
    Show dialog asking user how to handle their input during generation.

    Returns (action, input_text).
    """
    preview = input_text[:50] + ("..." if len(input_text) > 50 else "")

    console.print()
    console.print(f"[bold yellow]Input received:[/bold yellow] {preview}")
    console.print("  [bold](i)[/bold] Interrupt and send now")
    console.print("  [bold](q)[/bold] Queue for after generation")
    console.print("  [bold](c)[/bold] Cancel input, continue generation")

    while True:
        try:
            choice = input("Choice [i/q/c]: ").strip().lower()
            if choice in ("i", "interrupt"):
                return InputAction.INTERRUPT, input_text
            elif choice in ("q", "queue"):
                return InputAction.QUEUE, input_text
            elif choice in ("c", "cancel", ""):
                return InputAction.CANCEL, input_text
            else:
                console.print("[dim]Please enter 'i', 'q', or 'c'[/dim]")
        except (EOFError, KeyboardInterrupt):
            return InputAction.CANCEL, input_text


def check_and_handle_input() -> tuple[InputAction, str] | None:
    """
    Check for user input during generation and handle it.

    Call this periodically during generation (e.g., after each line of output).

    Returns:
        None if no input is pending
        (action, text) if user input was detected and handled
    """
    if not check_stdin_has_data():
        return None

    input_text = read_stdin_nonblocking()
    if not input_text:
        return None

    return show_input_dialog(input_text)


def queue_input(text: str) -> None:
    """Queue input for use after generation completes."""
    global _queued_input
    _queued_input = text
    console.print(f"[dim]Input queued: {text[:50]}{'...' if len(text) > 50 else ''}[/dim]")


def get_queued_input() -> str | None:
    """Get and clear any queued input."""
    global _queued_input
    text = _queued_input
    _queued_input = None
    return text


def has_queued_input() -> bool:
    """Check if there's queued input waiting."""
    return _queued_input is not None


def set_interrupt_content(text: str) -> None:
    """Set the interrupt content to be used as the next user message."""
    global _interrupt_content
    _interrupt_content = text


def get_interrupt_content() -> str | None:
    """Get and clear any interrupt content."""
    global _interrupt_content
    text = _interrupt_content
    _interrupt_content = None
    return text


def has_interrupt_content() -> bool:
    """Check if there's interrupt content waiting."""
    return _interrupt_content is not None
