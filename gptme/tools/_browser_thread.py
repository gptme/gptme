import importlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock, Thread
from typing import Any, Literal, TypeVar, cast

from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright

from gptme.config import get_config

logger = logging.getLogger(__name__)

T = TypeVar("T")

TIMEOUT = 20  # seconds - accounts for retry attempts with browser restarts

# Supported browser engines for the Playwright backend.
# Set GPTME_BROWSER_ENGINE=firefox to use Firefox instead of Chromium.
BrowserEngine = Literal["chromium", "firefox"]
_VALID_ENGINES: tuple[BrowserEngine, ...] = ("chromium", "firefox")

# Default context options applied to every browser context (and, in CDP mode,
# to the shared session context). Per-call request headers are layered on top
# at page creation time (see _create_page).
DEFAULT_CONTEXT_OPTIONS: dict[str, Any] = {
    "locale": "en-US",
    "geolocation": {"latitude": 37.773972, "longitude": 13.39},
    "permissions": ["geolocation"],
}


def get_context_options() -> dict[str, Any]:
    """Return browser context options, optionally loading a saved session state.

    If ``GPTME_BROWSER_STORAGE_STATE`` points to an existing JSON file (previously
    created by ``save_browser_state()``), the saved cookies and localStorage are
    loaded into every new context — enabling authenticated sessions without
    re-entering credentials on each invocation.

    Typical workflow::

        # 1. Open a browser manually and log in to the target site, then save state:
        open_page("https://x.com")
        save_browser_state("~/.config/gptme/twitter-session.json")

        # 2. Next time, load the state automatically:
        export GPTME_BROWSER_STORAGE_STATE=~/.config/gptme/twitter-session.json
        gptme --agent-profile computer-use "tweet 'hello from gptme'"
    """
    options = dict(DEFAULT_CONTEXT_OPTIONS)
    storage_path_raw = get_config().get_env("BROWSER_STORAGE_STATE")
    if storage_path_raw:
        storage_path = Path(storage_path_raw).expanduser()
        if storage_path.exists():
            options["storage_state"] = str(storage_path)
            logger.info("Loading browser storage state from %s", storage_path)
        else:
            logger.warning(
                "GPTME_BROWSER_STORAGE_STATE=%s does not exist — "
                "starting with a fresh (unauthenticated) session",
                storage_path_raw,
            )
    return options


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
            "websocket error",
            "econnreset",
        ]
    )


@dataclass
class Command:
    func: Callable
    args: tuple
    kwargs: dict


Action = Literal["stop"]


def _connect_or_launch_browser(
    playwright: Playwright,
    cdp_url: str | None,
    engine: BrowserEngine = "chromium",
) -> Browser:
    if cdp_url:
        # CDP is only supported for Chromium-based browsers.
        if engine != "chromium":
            logger.warning(
                "CDP connections only support Chromium; ignoring GPTME_BROWSER_ENGINE=%s",
                engine,
            )
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        logger.info("Connected to browser over CDP")
        return browser

    browser_launcher = getattr(playwright, engine)
    browser = browser_launcher.launch()
    logger.info("Browser launched (engine=%s)", engine)
    return browser


class BrowserThread:
    def __init__(
        self, cdp_url: str | None = None, engine: BrowserEngine | None = None
    ) -> None:
        self.cdp_url = cdp_url or get_config().get_env("BROWSER_CDP_URL")

        # Resolve engine: explicit arg > env var > default "chromium"
        if engine is None:
            raw = (get_config().get_env("BROWSER_ENGINE") or "").strip().lower()
            if raw in _VALID_ENGINES:
                engine = cast(BrowserEngine, raw)
            else:
                if raw:
                    logger.warning(
                        "Invalid GPTME_BROWSER_ENGINE='%s'; falling back to 'chromium'. "
                        "Valid values: %s",
                        raw,
                        ", ".join(_VALID_ENGINES),
                    )
                engine = "chromium"
        self.engine: BrowserEngine = engine
        self.queue: Queue[tuple[Command | Action, object]] = Queue()
        self.results: dict[object, tuple[Any, Exception | None]] = {}
        self.lock = Lock()
        self.ready = Event()
        self._init_error: Exception | None = None
        # Session-scoped context for CDP connections — isolates this gptme
        # session from other agents/tabs sharing the same browser.
        self._session_context: BrowserContext | None = None
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()
        # Wait for browser to be ready
        if not self.ready.wait(timeout=TIMEOUT):
            raise TimeoutError("Browser failed to start")
        if self._init_error:
            raise self._init_error

        logger.debug("Browser thread started")

    def _run(self):
        playwright: Playwright | None = None
        browser: Browser | None = None

        def launch_browser() -> Exception | None:
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
                # Drop any session context bound to the old (now dead) connection
                # so we don't open tabs on a stale context after a reconnect.
                if self._session_context is not None:
                    try:
                        self._session_context.close()
                    except Exception:
                        pass
                    self._session_context = None
                browser = _connect_or_launch_browser(
                    playwright, self.cdp_url, self.engine
                )
                # For CDP, (re)create an isolated session context so parallel
                # gptme instances don't share cookies/tabs. Recreated on every
                # (re)connect so it never points at a dead browser.
                if self.cdp_url:
                    self._session_context = browser.new_context(
                        **get_context_options()
                    )
                    logger.info("Created isolated session context for CDP connection")
                return None
            except Exception as e:
                browser = None  # Ensure browser is None after failed launch
                error: Exception

                if "Executable doesn't exist" in str(e):
                    pw_version = importlib.metadata.version("playwright")
                    install_target = (
                        "chromium-headless-shell"
                        if self.engine == "chromium"
                        else self.engine
                    )
                    error = RuntimeError(
                        f"Browser executable not found. Run: pipx run playwright=={pw_version} install {install_target}"
                    )
                else:
                    error = e
                logger.error(f"Failed to launch browser: {e}", exc_info=True)
                return error

        try:
            # Initial browser launch
            init_error = launch_browser()
            if init_error:
                self._init_error = init_error
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
                                    restart_error = launch_browser()
                                    if restart_error is None:
                                        logger.info("Browser restarted successfully")
                                        continue  # Retry command
                                    restart_error = RuntimeError(
                                        f"Browser restart failed after connection error in {command_name}: {restart_error}"
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
            # Close session context before browser
            if self._session_context is not None:
                try:
                    self._session_context.close()
                except Exception:
                    logger.debug("Error closing session context during cleanup")
                self._session_context = None

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
