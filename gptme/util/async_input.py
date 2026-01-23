"""
Async input collection using prompt_toolkit's patch_stdout.

This module provides input collection that keeps the prompt visible at the
bottom of the terminal while output (like LLM responses) streams above it.

The approach:
1. Run prompt_toolkit's prompt_async() in a background thread with its own event loop
2. Use patch_stdout() context so that any stdout writes appear above the prompt
3. Main thread continues with LLM streaming, output appears above the active prompt
4. When user presses Enter, input is queued for processing

Thread-safety: Uses queue.Queue for safe cross-thread communication.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import sys
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout

if TYPE_CHECKING:
    from ..message import Message

logger = logging.getLogger(__name__)


class VisiblePromptCollector:
    """
    Collects user input while keeping the prompt visible at the bottom.

    Uses prompt_toolkit's patch_stdout to display output above the active
    prompt. This allows users to see and edit their input while LLM
    responses stream above.

    Thread-safety: Uses queue.Queue for safe cross-thread communication.
    The collector owns the queue and provides thread-safe accessor methods.

    Usage:
        collector = VisiblePromptCollector()
        collector.start()
        msg = collector.get_message()  # Check for queued input
        collector.stop()
    """

    def __init__(
        self,
        on_input: Callable[[str], None] | None = None,
        max_queue_size: int = 100,
    ):
        """
        Initialize the visible prompt collector.

        Args:
            on_input: Optional callback when input is collected
            max_queue_size: Maximum number of prompts to queue
        """
        self._message_queue: queue.Queue[Message] = queue.Queue(maxsize=max_queue_size)
        self._on_input = on_input
        self._max_queue_size = max_queue_size
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()  # Protects _running and _thread state
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

    def _create_session(self) -> PromptSession:
        """Create a prompt session with appropriate keybindings."""
        kb = KeyBindings()

        @kb.add("c-j")
        def handle_newline(event):
            event.current_buffer.insert_text("\n")

        @kb.add("escape", "enter")
        def handle_meta_enter(event):
            event.current_buffer.insert_text("\n")

        @kb.add("c-c")
        def handle_interrupt(event):
            self._stop_event.set()
            event.app.exit(exception=KeyboardInterrupt())

        return PromptSession(
            history=InMemoryHistory(),
            auto_suggest=AutoSuggestFromHistory(),
            multiline=True,
            key_bindings=kb,
            enable_suspend=True,
        )

    async def _async_collect_loop(self):
        """Async loop for collecting input with visible prompt."""
        from ..message import Message

        session = self._create_session()

        with patch_stdout(raw=True):
            while not self._stop_event.is_set():
                try:
                    prompt_text = to_formatted_text(
                        ANSI("\033[90m[type-ahead] \033[0m> ")
                    )
                    result = await session.prompt_async(
                        prompt_text,
                        handle_sigint=False,
                    )

                    if result and result.strip():
                        try:
                            msg = Message("user", result.strip(), quiet=True)
                            # put_nowait raises queue.Full if full
                            self._message_queue.put_nowait(msg)
                            queue_size = self._message_queue.qsize()
                            print(
                                f"\033[92mQueued\033[0m "
                                f"({queue_size} prompt{'s' if queue_size > 1 else ''} ready)",
                                file=sys.stderr,
                            )
                        except queue.Full:
                            logger.warning(
                                f"Prompt queue full ({self._max_queue_size}), "
                                "dropping input"
                            )
                            continue

                        if self._on_input:
                            try:
                                self._on_input(result)
                            except Exception as e:
                                logger.debug(f"Input callback error: {e}")

                except KeyboardInterrupt:
                    self._stop_event.set()
                    break
                except EOFError:
                    self._stop_event.set()
                    break
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug(f"Error in visible prompt collection: {e}")
                    if self._stop_event.is_set():
                        break
                    await asyncio.sleep(0.1)

    def _thread_main(self):
        """Thread entry point - runs the async event loop."""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._task = self._loop.create_task(self._async_collect_loop())
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass  # Expected when stop() cancels the task
        except Exception as e:
            logger.debug(f"Collector thread error: {e}")
        finally:
            if self._loop:
                self._loop.close()
                self._loop = None
            self._task = None

    def start(self) -> None:
        """Start visible prompt collection in a background thread.

        Thread-safe: Can be called multiple times safely.
        """
        with self._state_lock:
            if self._running:
                return

            if not sys.stdin.isatty():
                logger.debug("Skipping visible prompt collector: stdin is not a TTY")
                return

            self._running = True
            self._stop_event.clear()

            self._thread = threading.Thread(
                target=self._thread_main,
                daemon=True,
                name="VisiblePromptCollector",
            )
            self._thread.start()
            logger.debug("Started visible prompt collector")

    def stop(self) -> None:
        """Stop visible prompt collection.

        Thread-safe: Properly cancels the async task before stopping the loop.
        """
        with self._state_lock:
            if not self._running:
                return

            self._running = False
            self._stop_event.set()

            # Cancel the task properly before stopping the loop
            if self._loop and self._task and not self._task.done():
                self._loop.call_soon_threadsafe(self._task.cancel)

            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=1.0)
                if self._thread.is_alive():
                    logger.warning("Collector thread did not stop cleanly")

            self._thread = None
            logger.debug("Stopped visible prompt collector")

    def get_message(
        self, block: bool = False, timeout: float | None = None
    ) -> Message | None:
        """Get a message from the queue.

        Thread-safe accessor method for the internal queue.

        Args:
            block: If True, block until a message is available
            timeout: If block is True, timeout after this many seconds

        Returns:
            Message or None if queue is empty (or timeout reached)
        """
        try:
            return self._message_queue.get(block=block, timeout=timeout)
        except queue.Empty:
            return None

    def has_messages(self) -> bool:
        """Check if there are any messages in the queue.

        Thread-safe: Uses queue.Queue's thread-safe qsize().
        """
        return not self._message_queue.empty()

    def get_queue_size(self) -> int:
        """Get number of prompts in queue.

        Thread-safe: Uses queue.Queue's thread-safe qsize().
        """
        return self._message_queue.qsize()

    def clear_queue(self) -> None:
        """Clear all messages from the queue.

        Thread-safe: Drains the queue by getting all items.
        """
        while True:
            try:
                self._message_queue.get_nowait()
            except queue.Empty:
                break

    @property
    def is_running(self) -> bool:
        """Check if collector is currently running.

        Thread-safe: Uses lock to read state.
        """
        with self._state_lock:
            return (
                self._running and self._thread is not None and self._thread.is_alive()
            )
