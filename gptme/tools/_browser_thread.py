import importlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Event, Lock, Thread
from typing import Any, Literal, TypeVar

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

T = TypeVar("T")

TIMEOUT = 20  # seconds - accounts for retry attempts with browser restarts


def _is_connection_error(error: Exception) -> bool:
    """Check if error indicates browser connection failure"""
    error_msg = str(error).lower()
    return any(
        phrase in error_msg
        for phrase in [
            "connection closed",
            "browser has been closed",
            "target closed",
            "connection terminated",
            "pipe closed",
        ]
    )


@dataclass
class Command:
    func: Callable
    args: tuple
    kwargs: dict


Action = Literal["stop"]


class BrowserThread:
    def __init__(self):
        self.queue: Queue[tuple[Command | Action, object]] = Queue()
        self.results: dict[object, tuple[Any, Exception | None]] = {}
        self.lock = Lock()
        self.ready = Event()
        self._init_error: Exception | None = None
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()
        # Wait for browser to be ready
        if not self.ready.wait(timeout=TIMEOUT):
            raise TimeoutError("Browser failed to start")
        if self._init_error:
            raise self._init_error

        logger.debug("Browser thread started")

    def _run(self):
        playwright = None
        browser = None

        def launch_browser():
            """Launch or relaunch the browser"""
            nonlocal playwright, browser
            if playwright is None:
                playwright = sync_playwright().start()
            try:
                if browser is not None:
                    try:
                        browser.close()
                        browser = None  # Clear reference after close
                    except Exception:
                        browser = None  # Clear reference even if close fails
                browser = playwright.chromium.launch()
                logger.info("Browser launched")
                return True
            except Exception as e:
                browser = None  # Ensure browser is None after failed launch
                if "Executable doesn't exist" in str(e):
                    pw_version = importlib.metadata.version("playwright")
                    self._init_error = RuntimeError(
                        f"Browser executable not found. Run: pipx run playwright=={pw_version} install chromium-headless-shell"
                    )
                else:
                    self._init_error = e
                logger.error(f"Failed to launch browser: {e}", exc_info=True)
                return False

        try:
            # Initial browser launch
            if not launch_browser():
                self.ready.set()
                return

            self.ready.set()  # Signal successful init

            while True:
                try:
                    cmd, cmd_id = self.queue.get(timeout=1.0)
                    if cmd == "stop":
                        break

                    # Try to execute command, with retry on connection error
                    command_name = getattr(cmd.func, "__name__", str(cmd.func))
                    max_retries = 2
                    for attempt in range(max_retries):
                        try:
                            result = cmd.func(browser, *cmd.args, **cmd.kwargs)
                            with self.lock:
                                self.results[cmd_id] = (result, None)
                            break  # Success, exit retry loop
                        except Exception as e:
                            if _is_connection_error(e):
                                logger.warning(
                                    f"Connection error in {command_name} (attempt {attempt + 1}/{max_retries}): {e}"
                                )
                                if attempt < max_retries - 1:
                                    # Try to recover by restarting browser
                                    logger.info("Attempting to restart browser...")
                                    if launch_browser():
                                        logger.info("Browser restarted successfully")
                                        continue  # Retry command
                                    else:
                                        # Create informative error about restart failure
                                        restart_error = RuntimeError(
                                            f"Browser restart failed after connection error in {command_name}: {e}"
                                        )
                                        with self.lock:
                                            self.results[cmd_id] = (None, restart_error)
                                        break  # Exit retry loop immediately
                            else:
                                logger.exception("Unexpected error in browser thread")

                            # Store error and exit retry loop
                            with self.lock:
                                self.results[cmd_id] = (None, e)
                            break
                except Empty:
                    # Timeout on queue.get, continue waiting
                    continue
        except Exception:
            logger.exception("Fatal error in browser thread")
            self.ready.set()  # Prevent hanging in __init__
            raise
        finally:
            # Close browser with isolated error handling
            if browser is not None:
                try:
                    browser.close()
                except Exception as e:
                    if _is_connection_error(e):
                        logger.debug(
                            f"Browser connection already closed during cleanup: {e}"
                        )
                    else:
                        logger.exception("Error closing browser")

            # Stop playwright with isolated error handling
            if playwright is not None:
                try:
                    playwright.stop()
                except Exception:
                    logger.exception("Error stopping playwright")

            logger.info("Browser stopped")

    def execute(self, func: Callable[..., T], *args, **kwargs) -> T:
        if not self.thread.is_alive():
            raise RuntimeError("Browser thread died")

        cmd_id = object()  # unique id
        self.queue.put((Command(func, args, kwargs), cmd_id))

        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            with self.lock:
                if cmd_id in self.results:
                    result, error = self.results.pop(cmd_id)
                    if error:
                        raise error
                    logger.info("Browser operation completed")
                    return result
            time.sleep(0.1)  # Prevent busy-waiting

        raise TimeoutError(f"Browser operation timed out after {TIMEOUT}s")

    def stop(self):
        """Stop the browser thread"""
        try:
            self.queue.put(("stop", object()))
            self.thread.join(timeout=TIMEOUT)
        except Exception:
            logger.exception("Error stopping browser thread")
