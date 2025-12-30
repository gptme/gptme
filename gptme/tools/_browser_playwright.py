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

from playwright.sync_api import Browser, ElementHandle

from ._browser_thread import BrowserThread, _is_connection_error

_browser: BrowserThread | None = None
_last_logs: dict = {"logs": [], "errors": [], "url": None}
logger = logging.getLogger(__name__)


def _restart_browser() -> None:
    """Restart the browser by resetting the global instance"""

    global _browser
    start_time = time.time()

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
            else:
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
        page_errors.append(f"Navigation error: {str(e)}")
        # Don't re-raise, just capture the error

    # Store logs globally
    _last_logs = {"logs": logs, "errors": page_errors, "url": url}

    try:
        return page.inner_html("body")
    finally:
        page.close()
        context.close()


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
        for log in _last_logs["logs"]:
            result.append(f"[{log['type'].upper()}] {log['text']} ({log['location']})")

    if _last_logs["errors"]:
        result.append("\n=== Page Errors ===")
        for error in _last_logs["errors"]:
            result.append(error)

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
            # FIXME: is this correct?
            selector=element.evaluate("el => el.selector"),
        )


def _list_clickable_elements(page, selector=None) -> list[Element]:
    elements = []

    # filter by selector
    if selector:
        selector = f"{selector} button, {selector} a"
    else:
        selector = "button, a"

    # List all clickable buttons
    clickable = page.query_selector_all(selector)
    for el in clickable:
        elements.append(Element.from_element(el))

    return elements


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


def _take_screenshot(
    browser: Browser, url: str, path: Path | str | None = None
) -> Path:
    """Take a screenshot of a webpage and save it to a file."""
    if path is None:
        path = tempfile.mktemp(suffix=".png")
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
    stdout, stderr = p.communicate(input=html.encode())

    if p.returncode != 0:
        raise Exception(f"Pandoc returned error code {p.returncode}: {stderr.decode()}")

    # Post-process the output to remove :::
    markdown = stdout.decode()
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
