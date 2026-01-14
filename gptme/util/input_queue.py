"""
Background input queue for queueing prompts while the agent is working.

This allows users to type prompts while the agent is processing, with the
prompts being queued for execution after the current work completes.
"""

import logging
import queue
import sys
import threading
from collections.abc import Callable

from . import console

logger = logging.getLogger(__name__)

# Queue for storing background inputs
_input_queue: queue.Queue[str] = queue.Queue()

# Thread for background input reading
_input_thread: threading.Thread | None = None

# Flag to control the background thread
_stop_background_input = threading.Event()

# Lock for thread-safe operations
_input_lock = threading.Lock()

# Callback for when input is queued (for visual feedback)
_on_queued_callback: Callable[[str], None] | None = None


def set_on_queued_callback(callback: Callable[[str], None] | None) -> None:
    """Set callback to be called when input is queued."""
    global _on_queued_callback
    _on_queued_callback = callback


def _default_on_queued(text: str) -> None:
    """Default callback showing queued input confirmation."""
    preview = text[:60] + "..." if len(text) > 60 else text
    preview = preview.replace("\n", " ")
    console.print(f"\n[dim]✓ Queued:[/dim] [italic]{preview}[/italic]")


def _background_input_reader() -> None:
    """Background thread that reads input while agent is processing."""
    global _on_queued_callback

    while not _stop_background_input.is_set():
        try:
            # Check if stdin is available and a tty
            if not sys.stdin.isatty():
                _stop_background_input.wait(0.1)
                continue

            # Use select to check if input is available (non-blocking)
            import select

            # Wait for input with timeout
            ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            if not ready:
                continue

            # Read a line of input
            try:
                line = sys.stdin.readline()
                if not line:  # EOF (Ctrl-D)
                    continue

                line = line.rstrip("\n")
                if line:
                    _input_queue.put(line)
                    callback = _on_queued_callback or _default_on_queued
                    callback(line)
            except (EOFError, KeyboardInterrupt):
                continue

        except Exception as e:
            logger.debug(f"Background input reader error: {e}")
            _stop_background_input.wait(0.1)


def start_background_input() -> None:
    """Start the background input reader thread."""
    global _input_thread

    with _input_lock:
        if _input_thread is not None and _input_thread.is_alive():
            return  # Already running

        _stop_background_input.clear()
        _input_thread = threading.Thread(
            target=_background_input_reader, daemon=True, name="background-input"
        )
        _input_thread.start()
        logger.debug("Background input thread started")


def stop_background_input() -> None:
    """Stop the background input reader thread."""
    global _input_thread

    with _input_lock:
        if _input_thread is None:
            return

        _stop_background_input.set()
        _input_thread.join(timeout=0.5)
        _input_thread = None
        logger.debug("Background input thread stopped")


def get_queued_input() -> str | None:
    """Get the next queued input, if any.

    Returns:
        The next queued input string, or None if queue is empty.
    """
    try:
        return _input_queue.get_nowait()
    except queue.Empty:
        return None


def get_all_queued_inputs() -> list[str]:
    """Get all queued inputs as a list.

    Returns:
        List of all queued input strings.
    """
    inputs = []
    while True:
        try:
            inputs.append(_input_queue.get_nowait())
        except queue.Empty:
            break
    return inputs


def queue_size() -> int:
    """Get the current size of the input queue."""
    return _input_queue.qsize()


def clear_queue() -> None:
    """Clear all queued inputs."""
    while not _input_queue.empty():
        try:
            _input_queue.get_nowait()
        except queue.Empty:
            break


def is_background_input_active() -> bool:
    """Check if background input is currently active."""
    return _input_thread is not None and _input_thread.is_alive()
