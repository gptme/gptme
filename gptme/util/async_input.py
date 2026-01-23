"""
Async input collection using prompt_toolkit's patch_stdout.

This module provides input collection that keeps the prompt visible at the
bottom of the terminal while output (like LLM responses) streams above it.

The approach:
1. Run prompt_toolkit's prompt_async() in a background thread with its own event loop
2. Use patch_stdout() context so that any stdout writes appear above the prompt
3. Main thread continues with LLM streaming, output appears above the active prompt
4. When user presses Enter, input is queued for processing
"""

import asyncio
import logging
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

    Usage:
        collector = VisiblePromptCollector(prompt_queue)
        collector.start()
        collector.stop()
    """

    def __init__(
        self,
        prompt_queue: list["Message"],
        on_input: Callable[[str], None] | None = None,
        max_queue_size: int = 100,
    ):
        """
        Initialize the visible prompt collector.

        Args:
            prompt_queue: List to append collected prompts to
            on_input: Optional callback when input is collected
            max_queue_size: Maximum number of prompts to queue
        """
        self._prompt_queue = prompt_queue
        self._on_input = on_input
        self._max_queue_size = max_queue_size
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

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
                        with self._lock:
                            if len(self._prompt_queue) >= self._max_queue_size:
                                logger.warning(
                                    f"Prompt queue full ({self._max_queue_size}), "
                                    "dropping input"
                                )
                                continue

                            msg = Message("user", result.strip(), quiet=True)
                            self._prompt_queue.append(msg)
                            queue_size = len(self._prompt_queue)

                        print(
                            f"\033[92mQueued\033[0m "
                            f"({queue_size} prompt{'s' if queue_size > 1 else ''} ready)",
                            file=sys.stderr,
                        )

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
            self._loop.run_until_complete(self._async_collect_loop())
        except Exception as e:
            logger.debug(f"Collector thread error: {e}")
        finally:
            if self._loop:
                self._loop.close()
                self._loop = None

    def start(self) -> None:
        """Start visible prompt collection in a background thread."""
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
        """Stop visible prompt collection."""
        if not self._running:
            return

        self._running = False
        self._stop_event.set()

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            if self._thread.is_alive():
                logger.warning("Collector thread did not stop cleanly")

        self._thread = None
        logger.debug("Stopped visible prompt collector")

    def get_queue_size(self) -> int:
        """Get number of prompts in queue."""
        with self._lock:
            return len(self._prompt_queue)

    @property
    def is_running(self) -> bool:
        """Check if collector is currently running."""
        return self._running and self._thread is not None and self._thread.is_alive()
