import atexit
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Browser, ElementHandle

from ._browser_thread import BrowserThread

_browser: BrowserThread | None = None
_last_logs: dict = {"logs": [], "errors": [], "url": None}
logger = logging.getLogger(__name__)


def get_browser() -> BrowserThread:
    global _browser
    if _browser is None:
        _browser = BrowserThread()
        atexit.register(_browser.stop)
    return _browser


def _load_page(browser: Browser, url: str) -> str:
    """Load a page and return its body HTML, always capturing logs"""
    global _last_logs

    context = browser.new_context(
        locale="en-US",
        geolocation={"latitude": 37.773972, "longitude": 13.39},
        permissions=["geolocation"],
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

    return page.inner_html("body")


def read_url(url: str) -> str:
    """Read the text of a webpage and return the text in Markdown format."""
    browser = get_browser()
    body_html = browser.execute(_load_page, url)
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
    page.goto(url)

    els = _list_clickable_elements(page)
    for el in els:
        if "Accept all" in el.text:
            el.element.click()
            logger.debug("Accepted Google terms")
            break

    return _list_results_google(page)


def search_google(query: str) -> str:
    browser = get_browser()
    return browser.execute(_search_google, query)


def _search_duckduckgo(browser: Browser, query: str) -> str:
    url = f"https://duckduckgo.com/?q={query}"

    context = browser.new_context(
        locale="en-US",
        geolocation={"latitude": 37.773972, "longitude": 13.39},
        permissions=["geolocation"],
    )
    page = context.new_page()
    page.goto(url)

    return _list_results_duckduckgo(page)


def search_duckduckgo(query: str) -> str:
    browser = get_browser()
    return browser.execute(_search_duckduckgo, query)


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


def _list_results_google(page) -> str:
    # fetch the results (elements with .g class)
    results = page.query_selector_all(".g")
    if not results:
        logger.error("No search results found")
        logger.debug(f"{page.inner_text('body')=}")
        return "Error: something went wrong with the search."

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
    # fetch the results
    results = page.query_selector(".react-results--main")
    if not results:
        logger.error("Unable to find selector `.react-results--main` in results")
        logger.debug(f"{page.inner_text('body')=}")
        return "Error: something went wrong with the search."
    results = results.query_selector_all("article")
    if not results:
        logger.error("Unable to find selector `article` in results")
        logger.debug(f"{page.inner_text('body')=}")
        return "Error: something went wrong with the search."

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
    page.goto(url)
    page.screenshot(path=path)

    return Path(path)


def screenshot_url(url: str, path: Path | str | None = None) -> Path:
    """Take a screenshot of a webpage and save it to a file."""
    logger.info(f"Taking screenshot of '{url}' and saving to '{path}'")
    browser = get_browser()
    path = browser.execute(_take_screenshot, url, path)
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
