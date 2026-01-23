"""
Background input collection for prompt queueing.

Allows users to type prompts while the agent is working,
storing them in a queue to be processed after the current response.
"""

import logging
import select
import sys
import threading
from collections.abc import Callable

from ..message import Message

logger = logging.getLogger(__name__)


class InputCollector:
    """
    Collect user input in a background thread while the agent is working.

    This enables "type-ahead" functionality where users can queue prompts
    without waiting for the current generation to complete.
    """

    def __init__(
        self,
        prompt_queue: list[Message],
        max_queue_size: int = 100,
        on_input_queued: Callable[[int], None] | None = None,
    ):
        """
        Initialize the input collector.

        Args:
            prompt_queue: The queue to append collected messages to
            max_queue_size: Maximum number of queued prompts
            on_input_queued: Callback when input is queued, receives queue size
        """
        self.prompt_queue = prompt_queue
        self.max_queue_size = max_queue_size
        self.on_input_queued = on_input_queued
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._partial_input = ""

    def start(self) -> None:
        """Start collecting input in background."""
        if not sys.stdin.isatty():
            logger.debug("stdin is not a TTY, skipping input collector")
            return

        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._collect_loop, daemon=True)
        self._thread.start()
        logger.debug("Input collector started")

    def stop(self) -> None:
        """Stop collecting input."""
        self._running = False
        logger.debug("Input collector stopped")
        # Don't join the thread - it may be blocked on input
        # The daemon flag ensures it won't prevent exit

    def get_queue_size(self) -> int:
        """Get the current number of queued prompts."""
        with self._lock:
            return len([m for m in self.prompt_queue if m.role == "user"])

    def _collect_loop(self) -> None:
        """Background loop to collect input."""
        while self._running:
            try:
                # Use select to check for input without blocking forever
                # Timeout allows us to check _running flag periodically
                if sys.platform == "win32":
                    # Windows doesn't support select on stdin
                    # Fall back to a simpler approach
                    self._collect_windows()
                else:
                    self._collect_unix()
            except Exception as e:
                logger.debug(f"Input collector error: {e}")
                break

    def _collect_unix(self) -> None:
        """Collect input on Unix systems using select."""
        # Check if there's input available (with timeout)
        readable, _, _ = select.select([sys.stdin], [], [], 0.1)
        if not readable:
            return

        # Read available input
        try:
            char = sys.stdin.read(1)
            if not char:
                return

            self._partial_input += char

            # Check for complete line (Enter pressed)
            if char == "\n":
                line = self._partial_input.strip()
                self._partial_input = ""

                if line:
                    self._queue_input(line)
        except Exception as e:
            logger.debug(f"Error reading input: {e}")

    def _collect_windows(self) -> None:
        """Collect input on Windows systems using msvcrt."""
        import msvcrt  # type: ignore[import-not-found]
        import time

        if msvcrt.kbhit():  # type: ignore[attr-defined]
            char = msvcrt.getwch()  # type: ignore[attr-defined]
            self._partial_input += char

            if char == "\r":  # Enter key on Windows
                line = self._partial_input.strip()
                self._partial_input = ""
                if line:
                    self._queue_input(line)
        else:
            time.sleep(0.1)

    def _queue_input(self, text: str) -> None:
        """Queue a completed input line."""
        with self._lock:
            if len(self.prompt_queue) >= self.max_queue_size:
                logger.warning(
                    f"Prompt queue full ({self.max_queue_size}), discarding input"
                )
                return

            msg = Message("user", text, quiet=True)
            self.prompt_queue.append(msg)
            # Count user messages in queue (don't call get_queue_size to avoid deadlock)
            queue_size = len([m for m in self.prompt_queue if m.role == "user"])

            logger.debug(f"Queued input: {text[:50]}... (queue size: {queue_size})")

            if self.on_input_queued:
                self.on_input_queued(queue_size)
