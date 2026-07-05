import atexit
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from playwright.sync_api import (
    Browser,
    BrowserContext,
    ElementHandle,
    Page,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from ._browser_format import format_snapshot as _format_snapshot
from ._browser_thread import (
    BrowserThread,
    _is_connection_error,
    get_context_options,
)
from ._computer_gate import sensitive_action_gate

_browser: BrowserThread | None = None
_last_logs: dict = {"logs": [], "errors": [], "url": None}
# Persistent page state for interactive browsing (open_page/click/fill/scroll)
_current_page: Page | None = None
_current_context: BrowserContext | None = None
logger = logging.getLogger(__name__)


def _is_cdp_connection() -> bool:
    """Check if the active browser was connected via CDP (reuse existing window)."""
    return _browser is not None and _browser.cdp_url is not None


@dataclass
class _ManagedPage:
    """A page with optional context ownership for proper cleanup.

    For CDP connections the existing context is reused (new tab, not window),
    so ``_owned_context`` is ``None`` and ``close()`` only closes the tab.
    For launched browsers we create and own an isolated context.
    """

    page: Page
    _owned_context: BrowserContext | None = None

    def close(self) -> None:
        try:
            self.page.close()
        except Exception:
            pass
        if self._owned_context is not None:
            try:
                self._owned_context.close()
            except Exception:
                pass


def _create_page(browser: Browser, **context_kwargs) -> _ManagedPage:
    """Create a page, reusing the session context for CDP connections.

    CDP mode  → opens a new **tab** in an isolated session context so that
    parallel gptme instances sharing the same Chrome don't collide. The shared
    context already carries the default locale/geolocation; per-call request
    headers are applied to the individual tab.
    Launched  → creates an isolated context with the given options.
    """
    if _is_cdp_connection() and _browser is not None:
        ctx = _browser._session_context
        if ctx is not None:
            page = ctx.new_page()
            headers = context_kwargs.get("extra_http_headers")
            if headers:
                page.set_extra_http_headers(headers)
            return _ManagedPage(page=page, _owned_context=None)
        # CDP without a session context shouldn't happen (it's recreated on
        # every reconnect) — warn rather than silently opening a new window.
        logger.warning(
            "CDP connection has no session context; falling back to a new "
            "browser context (may open a new window)"
        )

    context = browser.new_context(**context_kwargs)
    page = context.new_page()
    return _ManagedPage(page=page, _owned_context=context)


def _restart_browser() -> None:
    """Restart the browser by resetting the global instance"""

    global _browser, _current_page, _current_context
    start_time = time.time()

    # Clear persistent page globals — after a restart, old Page/BrowserContext objects
    # are dead. Resetting here ensures callers get a clear "no page open" error rather
    # than silently failing with a low-level Playwright "Target closed" error.
    _current_page = None
    _current_context = None

    if _browser is not None:
        try:
            logger.debug("Stopping old browser instance...")
            _browser.stop()
            logger.debug(f"Browser stopped in {time.time() - start_time:.2f}s")
        except Exception:
            logger.debug("Error stopping old browser instance")
        _browser = None

    logger.debug(f"Browser restart completed in {time.time() - start_time:.2f}s")


def get_browser() -> BrowserThread:
    global _browser
    if _browser is None:
        _browser = BrowserThread()
        atexit.register(_browser.stop)
    return _browser


T = TypeVar("T")


def _execute_with_retry(
    func: Callable[..., T], *args, max_retries: int = 1, **kwargs
) -> T:
    """Execute a browser function with automatic retry on connection failures"""
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            browser = get_browser()
            return browser.execute(func, *args, **kwargs)

        except Exception as e:
            last_error = e

            if _is_connection_error(e) and attempt < max_retries:
                logger.info("Browser connection failed, restarting browser...")
                _restart_browser()
                continue
            break

    # last_error will never be None here since we only break after setting it
    assert last_error is not None
    raise last_error


def _load_page(browser: Browser, url: str) -> str:
    """Load a page and return its body HTML, always capturing logs"""
    global _last_logs

    managed = _create_page(
        browser,
        **get_context_options(),
        extra_http_headers={
            # Prefer markdown and plaintext over HTML for better LLM consumption
            # Quality values (q) indicate preference order
            "Accept": "text/markdown, text/plain, text/html;q=0.9, */*;q=0.8"
        },
    )
    page = managed.page

    logger.info(f"Loading page: {url}")

    # Always capture logs
    logs = []
    page_errors = []

    def on_console(msg):
        logs.append(
            {
                "type": msg.type,
                "text": msg.text,
                "location": f"{msg.location.get('url', 'unknown')}:{msg.location.get('lineNumber', 'unknown')}:{msg.location.get('columnNumber', 'unknown')}"
                if msg.location
                else "unknown",
            }
        )

    def on_page_error(error):
        page_errors.append(f"Page error: {error}")

    page.on("console", on_console)
    page.on("pageerror", on_page_error)

    # Navigate to the page
    try:
        page.goto(url)
        # Wait for page to be fully loaded (includes network idle)
        page.wait_for_load_state("networkidle")
    except Exception as e:
        page_errors.append(f"Navigation error: {e}")
        # Don't re-raise, just capture the error

    # Store logs globally
    _last_logs = {"logs": logs, "errors": page_errors, "url": url}

    try:
        # Try to extract main content area first, falling back to body
        html = _extract_main_content(page)
        return html
    finally:
        managed.close()


def _extract_main_content(page: Page) -> str:
    """Extract main content from a page, stripping noise like nav, sidebar, footer.

    This reduces token waste and improves LLM consumption by focusing on actual content.
    """
    # Selectors for main content areas (in priority order)
    content_selectors = [
        "main",
        "[role='main']",
        "article",
        # GitHub-specific
        ".markdown-body",
        ".blob-wrapper",
        # Generic
        ".content-body",
        "#content",
        ".main-content",
        ".article-content",
        # GitHub SPA containers
        "#repo-content-pjax-container",
        ".repository-content",
        "[data-pjax-container]",
    ]

    # Selectors for noise elements to remove when falling back to body
    noise_selectors = [
        "nav",
        "header:not(article header)",
        "footer",
        "aside",
        ".sidebar",
        ".navigation",
        ".nav",
        ".menu",
        ".header",
        ".footer",
        ".toc",
        ".table-of-contents",
        "script",
        "style",
        "noscript",
        ".clipboard-copy",
        ".share-button",
        ".social-share",
        "[aria-hidden='true']",
        # GitHub noise
        ".gh-header",
        ".repohead",
        ".file-navigation",
        ".BtnGroup",
        ".d-none",
        "[data-hide-on-error]",
    ]

    # Find the first matching content selector
    main_content = None
    found_content_selector = False
    for selector in content_selectors:
        try:
            elem = page.query_selector(selector)
            if elem and elem.inner_text().strip():
                main_content = elem
                found_content_selector = True
                logger.debug(f"Found main content with selector: {selector}")
                break
        except Exception:
            continue

    # For SPAs: if nothing found yet, wait briefly and retry
    if not found_content_selector:
        try:
            page.wait_for_timeout(1000)
        except Exception:
            pass
        for selector in content_selectors:
            try:
                elem = page.query_selector(selector)
                if elem and elem.inner_text().strip():
                    main_content = elem
                    found_content_selector = True
                    logger.debug(f"Found content after wait with selector: {selector}")
                    break
            except Exception:
                continue

    if main_content is None:
        # Fall back to body
        main_content = page.query_selector("body")
        logger.debug("No main content selector found, using body")

    if main_content is None:
        return ""

    # If we found a dedicated content selector, return that directly (already clean)
    if found_content_selector:
        return main_content.inner_html()

    # Fell back to body: strip noise elements first
    for selector in noise_selectors:
        try:
            page.evaluate(
                "(selector) => document.querySelectorAll(selector).forEach((el) => el.remove())",
                selector,
            )
        except Exception:
            pass
    try:
        return page.inner_html("body")
    except Exception as e:
        logger.warning(f"Error getting body after noise removal: {e}")
        return main_content.inner_html()


def read_url(url: str) -> str:
    """Read the text of a webpage and return the text in Markdown format."""
    body_html = _execute_with_retry(_load_page, url)
    return html_to_markdown(body_html)


def read_logs() -> str:
    """Read browser console logs from the last read URL."""
    global _last_logs

    if not _last_logs["url"]:
        return "No URL has been read yet."

    result = [f"=== Logs for {_last_logs['url']} ==="]

    if _last_logs["logs"]:
        result.append("\n=== Console Logs ===")
        result.extend(
            f"[{log['type'].upper()}] {log['text']} ({log['location']})"
            for log in _last_logs["logs"]
        )

    if _last_logs["errors"]:
        result.append("\n=== Page Errors ===")
        result.extend(_last_logs["errors"])

    if not _last_logs["logs"] and not _last_logs["errors"]:
        result.append("\nNo logs or errors captured.")

    return "\n".join(result)


def _search_google(browser: Browser, query: str) -> str:
    query = urllib.parse.quote(query)
    url = f"https://www.google.com/search?q={query}&hl=en"

    managed = _create_page(browser, **get_context_options())
    page = managed.page
    try:
        page.goto(url)

        els = _list_clickable_elements(page)
        for el in els:
            if "Accept all" in el.text:
                el.element.click()
                logger.debug("Accepted Google terms")
                break

        # Check for CAPTCHA/bot detection before parsing results
        body_text = page.inner_text("body")
        if "unusual traffic" in body_text.lower() or "not a robot" in body_text.lower():
            logger.error("Google CAPTCHA detected")
            return "Error: Google detected automated access and is showing a CAPTCHA. Try using 'perplexity' as the search engine instead: search(query, 'perplexity')"
        return _list_results_google(page, body_text)
    finally:
        managed.close()


def search_google(query: str) -> str:
    return _execute_with_retry(_search_google, query)


def _search_duckduckgo(browser: Browser, query: str) -> str:
    url = f"https://html.duckduckgo.com/html?q={query}"

    managed = _create_page(browser, **get_context_options())
    page = managed.page
    try:
        page.goto(url)
        return _list_results_duckduckgo(page)
    finally:
        managed.close()


def search_duckduckgo(query: str) -> str:
    return _execute_with_retry(_search_duckduckgo, query)


@dataclass
class Element:
    type: str
    text: str
    name: str
    href: str | None
    element: ElementHandle
    selector: str

    @classmethod
    def from_element(cls, element: ElementHandle):
        return cls(
            type=element.evaluate("el => el.type"),
            text=element.evaluate("el => el.innerText"),
            name=element.evaluate("el => el.name"),
            href=element.evaluate("el => el.href"),
            element=element,
            selector=element.evaluate(
                """el => {
                    let s = el.tagName.toLowerCase();
                    if (el.id) return s + '#' + el.id;
                    if (el.className && typeof el.className === 'string')
                        s += '.' + el.className.trim().split(/\\s+/).join('.');
                    return s;
                }"""
            ),
        )


def _list_clickable_elements(page, selector=None) -> list[Element]:
    # filter by selector
    if selector:
        selector = f"{selector} button, {selector} a"
    else:
        selector = "button, a"

    # List all clickable buttons
    clickable = page.query_selector_all(selector)
    return [Element.from_element(el) for el in clickable]


@dataclass
class SearchResult:
    title: str
    url: str
    description: str | None = None


def titleurl_to_list(results: list[SearchResult]) -> str:
    s = ""
    for i, r in enumerate(results):
        s += f"\n{i + 1}. {r.title} ({r.url})"
        if r.description:
            s += f"\n   {r.description}"
    return s.strip()


def _list_results_google(page, body_text: str | None = None) -> str:
    # fetch the results (elements with .g class)
    results = page.query_selector_all(".g")
    if not results:
        logger.error("No search results found")
        if body_text is None:
            body_text = page.inner_text("body")
        logger.debug(f"{body_text=}")
        return "Error: No search results found. Google may be blocking automated access. Try using 'perplexity' as the search engine instead: search(query, 'perplexity')"

    # list results
    hits = []
    for result in results:
        url = result.query_selector("a").evaluate("el => el.href")
        h3 = result.query_selector("h3")
        if h3:
            title = h3.inner_text()
            # desc has data-sncf attribute
            desc_el = result.query_selector("[data-sncf]")
            desc = (desc_el.inner_text().strip().split("\n")[0]) if desc_el else ""
            hits.append(SearchResult(title, url, desc))
    return titleurl_to_list(hits)


def _list_results_duckduckgo(page) -> str:
    body_text = page.inner_text("body")
    if "Unfortunately, bots use DuckDuckGo too" in body_text:
        logger.error("Blocked by DuckDuckGo bot detection")
        logger.debug(f"{body_text=}")
        return "Error: DuckDuckGo detected automated access. Try using 'perplexity' as the search engine instead: search(query, 'perplexity')"
    if "complete the following challenge" in body_text.lower():
        logger.error("DuckDuckGo showing CAPTCHA")
        return "Error: DuckDuckGo is showing a CAPTCHA challenge. Try using 'perplexity' as the search engine instead: search(query, 'perplexity')"

    # fetch the results
    sel_results = "div#links"
    results = page.query_selector(sel_results)
    if not results:
        logger.error(f"Unable to find selector `{sel_results}` with results")
        logger.debug(f"{body_text=}")
        return "Error: DuckDuckGo page structure changed or blocked. Try using 'perplexity' as the search engine instead: search(query, 'perplexity')"
    results = results.query_selector_all(".result")
    if not results:
        logger.error("Unable to find selector `.result` in results")
        logger.debug(f"{body_text=}")
        return "Error: DuckDuckGo page structure changed. Try using 'perplexity' as the search engine instead: search(query, 'perplexity')"

    # list results
    hits = []
    for result in results:
        url = result.query_selector("a").evaluate("el => el.href")
        h2 = result.query_selector("h2")
        if h2:
            title = h2.inner_text()
            desc = result.query_selector("span").inner_text().strip().split("\n")[0]
            hits.append(SearchResult(title, url, desc))
    return titleurl_to_list(hits)


def _get_aria_snapshot(browser: Browser, url: str) -> str:
    """Load a page and return its ARIA accessibility snapshot."""
    managed = _create_page(
        browser,
        locale="en-US",
    )
    page = managed.page
    try:
        page.goto(
            url
        )  # waits for "load" state by default; networkidle can hang on SPAs/analytics
        snapshot = page.locator("body").aria_snapshot()
        if not snapshot:
            return "Error: Could not get accessibility snapshot for this page."
        return _format_snapshot(snapshot, page.url, page.title())
    finally:
        managed.close()


def aria_snapshot(url: str) -> str:
    """Get the ARIA accessibility snapshot of a webpage."""
    logger.info(f"Getting ARIA snapshot of '{url}'")
    return _execute_with_retry(_get_aria_snapshot, url)


# --- Interactive browser functions (persistent page state) ---


def _close_current_page() -> None:
    """Close the current persistent page and context if open."""
    global _current_page, _current_context
    if _current_page is not None:
        try:
            _current_page.close()
        except Exception:
            pass
        _current_page = None
    if _current_context is not None:
        try:
            _current_context.close()
        except Exception:
            pass
        _current_context = None


def _page_snapshot() -> str:
    """Get ARIA snapshot of the current persistent page."""
    if _current_page is None:
        raise RuntimeError("No page is currently open")
    snapshot = _current_page.locator("body").aria_snapshot()
    if not snapshot:
        raise RuntimeError("Could not get accessibility snapshot.")
    return _format_snapshot(snapshot, _current_page.url, _current_page.title())


def _read_page_text(browser: Browser) -> str:
    """Read the text content of the current persistent page as Markdown."""
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    body_html = _current_page.inner_html("body")
    return html_to_markdown(body_html)


def read_page_text() -> str:
    """Read the full text content of the current interactive page as Markdown.

    Returns the page content converted to Markdown, preserving text formatting.
    Useful for reading article text, documentation, or other content after
    navigating with open_page()/click_element().
    """
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    logger.info("Reading text content of current page")
    return _execute_with_retry(_read_page_text)


def _open_page(browser: Browser, url: str) -> str:
    """Open a page for interactive browsing and return its ARIA snapshot."""
    global _current_page, _current_context

    _close_current_page()

    managed = _create_page(
        browser,
        **get_context_options(),
        extra_http_headers={
            "Accept": "text/markdown, text/plain, text/html;q=0.9, */*;q=0.8"
        },
    )
    # Store page/context for interactive use; don't auto-close via ManagedPage
    _current_page = managed.page
    _current_context = managed._owned_context

    try:
        _current_page.goto(url)
    except Exception as e:
        _close_current_page()
        raise RuntimeError(f"Failed to navigate to {url}: {e}") from e

    return _page_snapshot()


def _do_close_page(browser: Browser) -> str:
    """Close the current page on the browser thread."""
    _close_current_page()
    return "Page closed."


def close_page() -> str:
    """Close the current interactive browsing page."""
    if _current_page is None:
        return "No page is currently open."
    return _execute_with_retry(_do_close_page)


def _do_save_browser_state(browser: Browser, path: str) -> str:
    """Save current browser session state (cookies + localStorage) to a JSON file."""
    ctx: BrowserContext | None = _current_context
    if ctx is None and _browser is not None and _browser._session_context is not None:
        # CDP mode — the active context is the shared session context.
        ctx = _browser._session_context
    if ctx is None:
        raise RuntimeError(
            "No browser context is active. Call open_page(url) first to open a page "
            "and authenticate, then save the session state."
        )
    resolved = Path(path).expanduser()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    ctx.storage_state(path=str(resolved))
    os.chmod(resolved, 0o600)
    return f"Browser session state saved to {resolved}"


def save_browser_state(path: str) -> str:
    """Save the current browser session state (cookies, localStorage) to a file.

    Captures the full authentication state of the active browser context so it can
    be restored in a future session via ``GPTME_BROWSER_STORAGE_STATE``.

    Typical workflow::

        # 1. Open the page and log in manually or via fill_element/click_element:
        open_page("https://x.com/login")
        fill_element("#username", "you@example.com")
        fill_element("#password", "hunter2")
        click_element("text=Log in")

        # 2. Verify you're logged in, then save the session:
        save_browser_state("~/.config/gptme/twitter-session.json")

        # 3. Future sessions load it automatically:
        #    export GPTME_BROWSER_STORAGE_STATE=~/.config/gptme/twitter-session.json
        # OR call load_browser_state() programmatically without a restart.

    Args:
        path: File path to write the session JSON. Parent directories are
              created automatically. ``~`` is expanded.

    Returns:
        Confirmation string with the absolute path where the state was saved.
    """
    logger.info("Saving browser session state to %s", path)
    return _execute_with_retry(_do_save_browser_state, path)


def _do_load_browser_state(browser: Browser, path: str) -> str:
    """Load a previously saved browser session state for use in the next open_page()."""
    from ._browser_thread import set_storage_state_override

    resolved = Path(path).expanduser()
    if not resolved.exists():
        raise FileNotFoundError(
            f"Browser state file not found: {resolved}\n"
            "Save a session first with save_browser_state(path), then reload."
        )

    # Close the current page + context so the next open_page() creates a fresh
    # context that will pick up the loaded storage state.
    _close_current_page()

    # Register the override so get_context_options() uses it on the next context.
    set_storage_state_override(resolved)

    return (
        f"Browser state loaded from {resolved}. "
        "Call open_page(url) to start a session with the restored cookies and localStorage."
    )


def load_browser_state(path: str) -> str:
    """Load a previously saved browser session (cookies, localStorage) from a file.

    This is the in-session complement to ``save_browser_state()``.  Instead of
    restarting gptme with ``GPTME_BROWSER_STORAGE_STATE``, call this function
    directly to restore authentication state without a process restart.

    After calling ``load_browser_state()``, call ``open_page(url)`` to start a
    new browser session with the restored cookies and localStorage.

    Typical workflow::

        # Session A — log in and save state:
        open_page("https://x.com/login")
        fill_element("#username", "you@example.com")
        fill_element("#password", "hunter2")
        click_element("text=Log in")
        save_browser_state("~/.config/gptme/twitter-session.json")

        # Session B (same process, or a new one) — restore state and tweet:
        load_browser_state("~/.config/gptme/twitter-session.json")
        open_page("https://x.com")           # opens already logged in
        click_element("text=What is happening?!")
        fill_element('[data-testid="tweetTextarea_0"]', "hello from gptme!")
        click_element('[data-testid="tweetButtonInline"]')

    Args:
        path: Path to the session JSON previously written by ``save_browser_state()``.
              ``~`` is expanded to the home directory.

    Returns:
        Confirmation string. The next ``open_page()`` will use the restored state.

    Raises:
        FileNotFoundError: If *path* does not exist.
    """
    logger.info("Loading browser session state from %s", path)
    return _execute_with_retry(_do_load_browser_state, path)


def open_page(url: str) -> str:
    """Open a page for interactive browsing. Returns ARIA accessibility snapshot.

    Use this instead of read_url() when you need to interact with the page
    (click buttons, fill forms, scroll). The page stays open for subsequent
    click_element(), fill_element(), and scroll_page() calls.
    """
    logger.info(f"Opening page for interaction: '{url}'")
    return _execute_with_retry(_open_page, url)


def _click(browser: Browser, selector: str) -> str:
    """Click an element on the current page."""
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    _current_page.locator(selector).click(timeout=10000)
    # Wait for page to settle after click (navigation or dynamic update)
    try:
        _current_page.wait_for_load_state("domcontentloaded", timeout=5000)
    except PlaywrightTimeoutError:
        pass  # Timeout is fine — page may not navigate
    return _page_snapshot()


def click_element(selector: str) -> str:
    """Click an element on the current page and return updated ARIA snapshot.

    Args:
        selector: Playwright selector to find the element. Supports:
            - CSS: "#submit-btn", ".nav-link", "button"
            - Text: "text=Submit", "text=Log in"
            - Role: "role=button[name='Submit']"
            - Chained: "form >> text=Submit"

    Note:
        Links with ``target="_blank"`` open a new tab, but ``_current_page`` is not
        updated to point to it. The returned snapshot reflects the *original* tab.
    """
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    logger.info(f"Clicking element: '{selector}'")
    return _execute_with_retry(_click, selector)


def _fill(browser: Browser, selector: str, value: str) -> str:
    """Fill a form field on the current page."""
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    _current_page.locator(selector).fill(value, timeout=10000)
    return _page_snapshot()


def fill_element(selector: str, value: str) -> str:
    """Fill a form field on the current page and return updated ARIA snapshot.

    Clears any existing value before filling.

    Args:
        selector: Playwright selector for the input/textarea element.
        value: Text to fill into the field.
    """
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    sensitive_action_gate("fill_element", value, is_browser=True)
    logger.info(f"Filling element '{selector}' with value")
    return _execute_with_retry(_fill, selector, value)


def _scroll(browser: Browser, direction: str, amount: int) -> str:
    """Scroll the current page."""
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    if direction not in ("up", "down"):
        raise ValueError(f"direction must be 'up' or 'down', got: {direction!r}")
    if amount <= 0:
        raise ValueError(f"amount must be positive, got: {amount!r}")
    pixels = amount if direction == "down" else -amount
    _current_page.mouse.wheel(0, pixels)
    # Brief wait for lazy-loaded content
    _current_page.wait_for_timeout(300)
    return _page_snapshot()


def scroll_page(direction: str = "down", amount: int = 500) -> str:
    """Scroll the current page and return updated ARIA snapshot.

    Args:
        direction: "up" or "down" (default: "down")
        amount: Pixels to scroll — must be positive (default: 500)
    """
    if direction not in ("up", "down"):
        raise ValueError(f"direction must be 'up' or 'down', got: {direction!r}")
    if amount <= 0:
        raise ValueError(f"amount must be positive, got: {amount!r}")
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    logger.info(f"Scrolling {direction} by {amount}px")
    return _execute_with_retry(_scroll, direction, amount)


def _press_key(browser: Browser, key: str) -> str:
    """Press a keyboard key or shortcut in the current page."""
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    _current_page.keyboard.press(key)
    try:
        _current_page.wait_for_load_state("domcontentloaded", timeout=5000)
    except PlaywrightTimeoutError:
        pass  # Timeout is fine — key press may not navigate
    return _page_snapshot()


def press_key(key: str) -> str:
    """Press a keyboard key or shortcut in the current browser page.

    Dispatches the key event to the active focused element, or the document if
    nothing is focused. Useful for submitting forms (``Enter``), navigating
    autocomplete menus (``ArrowDown``), dismissing modals (``Escape``), and
    triggering keyboard shortcuts (e.g. ``Control+a`` to select all).

    Args:
        key: Playwright key name. Examples: ``"Enter"``, ``"Tab"``,
             ``"Escape"``, ``"ArrowDown"``, ``"Control+a"``, ``"Meta+k"``.

    Returns:
        Updated ARIA snapshot of the page after the key press.

    Example::

        open_page("https://example.com/search")
        fill_element("[name='q']", "gptme")
        press_key("Enter")   # submit the search form
    """
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    logger.info("Pressing key: '%s'", key)
    return _execute_with_retry(_press_key, key)


def _select_option(browser: Browser, selector: str, value: str) -> str:
    """Select an option from a <select> element on the current page."""
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    locator = _current_page.locator(selector)
    # The removed label= fallback was unreachable: PlaywrightTimeoutError is
    # raised when the locator cannot resolve (element absent), in which case
    # label= on the same locator would also time out.  A no-option-match
    # raises a different error that the except clause never caught.
    # Drop the dead fallback to surface failures in 10 s instead of 20 s.
    locator.select_option(value=value, timeout=10000)
    return _page_snapshot()


def select_option(selector: str, value: str) -> str:
    """Select an option from a <select> dropdown on the current page.

    Finds the ``<select>`` element and selects the option matching ``value``
    by its ``value`` attribute.

    Args:
        selector: Playwright selector for the ``<select>`` element
                  (e.g. ``"select#country"``, ``"[name='size']"``).
        value: The option value (``value`` attribute) to select.

    Returns:
        Updated ARIA snapshot of the page after the selection.

    Example::

        open_page("https://example.com/order")
        select_option("[name='size']", "large")
        click_element("text=Add to cart")
    """
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    logger.info("Selecting option '%s' from '%s'", value, selector)
    return _execute_with_retry(_select_option, selector, value)


def _wait_for_element(browser: Browser, selector: str, timeout_ms: int) -> str:
    """Wait for an element to be visible on the current page."""
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    try:
        _current_page.locator(selector).wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightTimeoutError as e:
        raise RuntimeError(
            f"Element '{selector}' did not appear within {timeout_ms}ms. "
            "The page may still be loading, or the selector is wrong."
        ) from e
    return _page_snapshot()


def wait_for_element(selector: str, timeout_ms: int = 5000) -> str:
    """Wait for a DOM element to become visible on the current page.

    Blocks until the element matching ``selector`` is visible, then returns
    the updated page snapshot. Useful after clicking something that triggers
    a dynamic content load, modal, or redirect.

    Args:
        selector: Playwright selector for the element to wait for.
        timeout_ms: Maximum wait time in milliseconds (default: 5000).
                    Raises ``RuntimeError`` if element does not appear.

    Returns:
        Updated ARIA snapshot of the page once the element is visible.

    Example::

        open_page("https://x.com/compose/tweet")
        wait_for_element("[data-testid='tweetTextarea_0']", timeout_ms=8000)
        fill_element("[data-testid='tweetTextarea_0']", "Hello from gptme!")
        click_element("[data-testid='tweetButtonInline']")
    """
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    if timeout_ms <= 0:
        raise ValueError(f"timeout_ms must be positive, got: {timeout_ms!r}")
    logger.info("Waiting for element '%s' (timeout=%dms)", selector, timeout_ms)
    return _execute_with_retry(_wait_for_element, selector, timeout_ms)


def _hover(browser: Browser, selector: str) -> str:
    """Hover over an element on the current page."""
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    _current_page.locator(selector).hover(timeout=10000)
    _current_page.wait_for_timeout(300)
    return _page_snapshot()


def hover_element(selector: str) -> str:
    """Hover over an element on the current page and return updated ARIA snapshot.

    Triggers ``mouseover`` and ``mouseenter`` events, revealing hover-only
    content such as dropdown menus, tooltips, and contextual buttons.  Use it
    before clicking a menu item that only appears on hover.

    Args:
        selector: Playwright selector for the element to hover over.

    Returns:
        Updated ARIA snapshot of the page after the hover.

    Example::

        open_page("https://example.com")
        hover_element("text=Products")   # reveal dropdown
        click_element("text=Pricing")    # click item that appeared
    """
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    logger.info("Hovering over element: '%s'", selector)
    return _execute_with_retry(_hover, selector)


def _snapshot_current_page(_browser: Browser) -> str:
    """Return the current page snapshot from the browser thread."""
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    return _page_snapshot()


def snapshot_page() -> str:
    """Get the ARIA accessibility snapshot of the current interactive page.

    Returns the structured accessibility tree of the page that is currently
    open via ``open_page()``, reflecting all DOM changes made by subsequent
    interactions (clicks, fills, scrolls, key presses, hover events).

    Use this when you need to re-read the current page state without
    triggering any action — e.g. after a dynamic update or to verify a
    form's state before submitting.

    Returns:
        Structured ARIA snapshot including page title and current URL.

    Raises:
        RuntimeError: If no page is currently open.

    Example::

        open_page("https://example.com/form")
        fill_element("[name='email']", "user@example.com")
        state = snapshot_page()   # verify the field was filled before submitting
        click_element("text=Submit")
    """
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    logger.info("Snapshotting current page state")
    return _execute_with_retry(_snapshot_current_page)


def _get_current_url(_browser: Browser) -> str:
    """Return the current page URL from the browser thread."""
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    return _current_page.url


def get_current_url() -> str:
    """Return the URL of the currently open browser page.

    Useful after a redirect, navigation, or login flow to confirm where
    the browser ended up.

    Returns:
        The current URL as a string.

    Raises:
        RuntimeError: If no page is currently open.

    Example::

        open_page("https://example.com/login")
        fill_element("#username", "alice")
        fill_element("#password", "secret")
        click_element("text=Log in")
        url = get_current_url()   # confirm redirect to /dashboard
    """
    if _current_page is None:
        raise RuntimeError("No page is open. Call open_page(url) first.")
    return _execute_with_retry(_get_current_url)


def _take_screenshot(
    browser: Browser, url: str, path: Path | str | None = None
) -> Path:
    """Take a screenshot of a webpage and save it to a file."""
    if path is None:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
    else:
        # create the directory if it doesn't exist
        os.makedirs(os.path.dirname(path), exist_ok=True)

    managed = _create_page(browser)
    page = managed.page
    try:
        page.goto(url)
        page.screenshot(path=path)
        return Path(path)
    finally:
        managed.close()


def screenshot_url(url: str, path: Path | str | None = None) -> Path:
    """Take a screenshot of a webpage and save it to a file."""
    logger.info(f"Taking screenshot of '{url}' and saving to '{path}'")
    path = _execute_with_retry(_take_screenshot, url, path)
    print(f"Screenshot saved to {path}")
    return path


def html_to_markdown(html):
    # check that pandoc is installed
    if not shutil.which("pandoc"):
        raise Exception("Pandoc is not installed. Needed for browsing.")

    p = subprocess.Popen(
        ["pandoc", "-f", "html", "-t", "markdown"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = p.communicate(input=html.encode(), timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        raise Exception("Pandoc timed out while converting HTML to markdown") from None

    if p.returncode != 0:
        raise Exception(
            f"Pandoc returned error code {p.returncode}: "
            f"{stderr.decode('utf-8', errors='replace')}"
        )

    # Post-process the output to remove :::
    markdown = stdout.decode("utf-8", errors="replace")
    markdown = "\n".join(
        line for line in markdown.split("\n") if not line.strip().startswith(":::")
    )

    # Post-process the output to remove div tags
    markdown = markdown.replace("<div>", "").replace("</div>", "")

    # replace [\n]{3,} with \n\n
    markdown = re.sub(r"[\n]{3,}", "\n\n", markdown)

    # replace {...} with ''
    markdown = re.sub(r"\{(#|style|target|\.)[^}]*\}", "", markdown)

    # strip inline images, like: data:image/png;base64,...
    re_strip_data = re.compile(r"!\[[^\]]*\]\(data:image[^)]*\)")

    # test cases
    assert re_strip_data.sub("", "![test](data:image/png;base64,123)") == ""
    assert re_strip_data.sub("", "![test](data:image/png;base64,123) test") == " test"

    markdown = re_strip_data.sub("", markdown)

    return markdown
