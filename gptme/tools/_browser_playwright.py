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
from ._browser_thread import BrowserThread, _is_connection_error

_browser: BrowserThread | None = None
_last_logs: dict = {"logs": [], "errors": [], "url": None}
# Persistent page state for interactive browsing (open_page/click/fill/scroll)
_current_page: Page | None = None
_current_context: BrowserContext | None = None
logger = logging.getLogger(__name__)


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

    context = browser.new_context(
        locale="en-US",
        geolocation={"latitude": 37.773972, "longitude": 13.39},
        permissions=["geolocation"],
        extra_http_headers={
            # Prefer markdown and plaintext over HTML for better LLM consumption
            # Quality values (q) indicate preference order
            "Accept": "text/markdown, text/plain, text/html;q=0.9, */*;q=0.8"
        },
    )

    logger.info(f"Loading page: {url}")
    page = context.new_page()

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
        page.close()
        context.close()


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

    context = browser.new_context(
        locale="en-US",
        geolocation={"latitude": 37.773972, "longitude": 13.39},
        permissions=["geolocation"],
    )
    page = context.new_page()
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
        page.close()
        context.close()


def search_google(query: str) -> str:
    return _execute_with_retry(_search_google, query)


def _search_duckduckgo(browser: Browser, query: str) -> str:
    url = f"https://html.duckduckgo.com/html?q={query}"

    context = browser.new_context(
        locale="en-US",
        geolocation={"latitude": 37.773972, "longitude": 13.39},
        permissions=["geolocation"],
    )
    page = context.new_page()
    try:
        page.goto(url)
        return _list_results_duckduckgo(page)
    finally:
        page.close()
        context.close()


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
    context = browser.new_context(
        locale="en-US",
    )
    page = context.new_page()
    try:
        page.goto(
            url
        )  # waits for "load" state by default; networkidle can hang on SPAs/analytics
        snapshot = page.locator("body").aria_snapshot()
        if not snapshot:
            return "Error: Could not get accessibility snapshot for this page."
        return _format_snapshot(snapshot, page.url, page.title())
    finally:
        page.close()
        context.close()


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

    _current_context = browser.new_context(
        locale="en-US",
        geolocation={"latitude": 37.773972, "longitude": 13.39},
        permissions=["geolocation"],
        extra_http_headers={
            "Accept": "text/markdown, text/plain, text/html;q=0.9, */*;q=0.8"
        },
    )
    _current_page = _current_context.new_page()

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

    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(url)
        page.screenshot(path=path)
        return Path(path)
    finally:
        page.close()
        context.close()


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
