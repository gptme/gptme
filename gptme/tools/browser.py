"""
Tools to let the assistant control a browser, including:
 - loading pages
 - reading their contents
 - searching the web
 - taking screenshots (Playwright only)

Two backends are available:

Playwright backend:
 - Full browser automation with screenshots
 - Installation:

   .. code-block:: bash

       pipx install 'gptme[browser]'
       # We need to use the same version of Playwright as the one installed by gptme
       # when downloading the browser binaries. gptme will attempt this automatically
       PW_VERSION=$(pipx runpip gptme show playwright | grep Version | cut -d' ' -f2)
       pipx run playwright==$PW_VERSION install chromium-headless-shell

Lynx backend:
 - Text-only browser for basic page reading and searching
 - No screenshot support
 - Installation:

   .. code-block:: bash

       # On Ubuntu
       sudo apt install lynx
       # On macOS
       brew install lynx
       # or any other way that gets you the `lynx` command

.. note::

    This is an experimental feature. It needs some work to be more robust and useful.
"""

import importlib.metadata
import importlib.util
import logging
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Literal

from ..util import console
from .base import ToolSpec, ToolUse

has_playwright = lambda: importlib.util.find_spec("playwright") is not None  # noqa
has_lynx = lambda: shutil.which("lynx")  # noqa
browser: Literal["playwright", "lynx"] | None = (
    "playwright" if has_playwright() else ("lynx" if has_lynx() else None)
)

# Check for Perplexity availability
try:
    from ._browser_perplexity import has_perplexity_key, search_perplexity  # fmt: skip

    has_perplexity = has_perplexity_key()
except ImportError:
    has_perplexity = False
    search_perplexity = None  # type: ignore

# noreorder
if browser == "playwright":
    from ._browser_playwright import read_logs as read_logs_playwright  # fmt: skip
    from ._browser_playwright import read_url as read_url_playwright  # fmt: skip
    from ._browser_playwright import screenshot_url as screenshot_url_pw  # fmt: skip
    from ._browser_playwright import search_duckduckgo, search_google  # fmt: skip
elif browser == "lynx":
    from ._browser_lynx import read_url as read_url_lynx  # fmt: skip
    from ._browser_lynx import search as search_lynx  # fmt: skip

logger = logging.getLogger(__name__)

# Always include all engine types in the type definition
EngineType = Literal["google", "duckduckgo", "perplexity"]


def examples(tool_format):
    return f"""
### Reading docs
User: how does gptme work?
Assistant: Let's read the docs.
{ToolUse("ipython", [], "read_url('https://gptme.org/docs/')").to_output(tool_format)}

### Answer question from URL with browsing
User: find out which is the latest ActivityWatch version from superuserlabs.org
Assistant: Let's browse the site.
{ToolUse("ipython", [], "read_url('https://superuserlabs.org/')").to_output(tool_format)}
System:
{ToolUse("https://superuserlabs.org/", [], "... [ActivityWatch](https://activitywatch.net/) ...".strip()).to_output()}
Assistant: Couldn't find the answer on the page. Following link to the ActivityWatch website.
{ToolUse("ipython", [], "read_url('https://activitywatch.net/')").to_output(tool_format)}
System:
{ToolUse("https://activitywatch.net/", [], "... Download latest version v0.12.2 ...".strip()).to_output()}
Assistant: The latest version of ActivityWatch is v0.12.2

### Searching
User: who is the founder of ActivityWatch?
Assistant: Let's search for that.
{ToolUse("ipython", [], "search('ActivityWatch founder')").to_output(tool_format)}
System:
{ToolUse("results", [], "1. [ActivityWatch](https://activitywatch.net/) ...").to_output()}
Assistant: Following link to the ActivityWatch website.
{ToolUse("ipython", [], "read_url('https://activitywatch.net/')").to_output(tool_format)}
System:
{ToolUse("https://activitywatch.net/", [], "... The ActivityWatch project was founded by Erik Bjäreholt in 2016. ...".strip()).to_output()}
Assistant: The founder of ActivityWatch is Erik Bjäreholt.

### Searching with Perplexity
User: what are the latest developments in AI?
Assistant: Let me search for that using Perplexity AI.
{ToolUse("ipython", [], "search('latest developments in AI', 'perplexity')").to_output(tool_format)}
System:
{ToolUse("result", [], "Based on recent developments, AI has seen significant advances...").to_output()}
Assistant: Based on the search results, here are the latest AI developments...

### Take screenshot of page
User: take a screenshot of the ActivityWatch website
Assistant: Certainly! I'll use the browser tool to screenshot the ActivityWatch website.
{ToolUse("ipython", [], "screenshot_url('https://activitywatch.net')").to_output(tool_format)}
System:
{ToolUse("result", [], "Screenshot saved to screenshot.png").to_output()}

### Read URL and check browser logs
User: read this page and check if there are any console errors
Assistant: I'll read the page first and then check the browser logs.
{ToolUse("ipython", [], "read_url('https://example.com')").to_output(tool_format)}
System:
{ToolUse("https://example.com", [], "This domain is for use in illustrative examples...").to_output()}
Assistant: Now let me check the browser console logs:
{ToolUse("ipython", [], "read_logs()").to_output(tool_format)}
System:
{ToolUse("result", [], "No logs or errors captured.").to_output()}
""".strip()


def init() -> ToolSpec:
    if browser == "playwright":
        console.log("Using browser tool with playwright")
    elif browser == "lynx":
        console.log("Using browser tool with lynx")
    return tool


@lru_cache
def has_browser_tool():
    return browser is not None


def read_url(url: str) -> str:
    """Read a webpage in a text format."""
    assert browser
    if browser == "playwright":
        return read_url_playwright(url)  # type: ignore
    elif browser == "lynx":
        return read_url_lynx(url)  # type: ignore


def search(query: str, engine: EngineType = "google") -> str:
    """Search for a query on a search engine."""
    logger.info(f"Searching for '{query}' on {engine}")
    if engine == "perplexity":
        if has_perplexity:
            return search_perplexity(query)  # type: ignore
        else:
            return "Error: Perplexity search not available. Set PERPLEXITY_API_KEY or OPENROUTER_API_KEY environment variable or add it to ~/.config/gptme/config.toml"
    elif browser == "playwright":
        return search_playwright(query, engine)
    elif browser == "lynx":
        return search_lynx(query, engine)  # type: ignore
    raise ValueError(f"Unknown search engine: {engine}")


def search_playwright(query: str, engine: EngineType = "google") -> str:
    """Search for a query on a search engine using Playwright."""
    if engine == "google":
        return search_google(query)  # type: ignore
    elif engine == "duckduckgo":
        return search_duckduckgo(query)  # type: ignore
    raise ValueError(f"Unknown search engine: {engine}")


def screenshot_url(url: str, path: Path | str | None = None) -> Path:
    """Take a screenshot of a webpage."""
    assert browser
    if browser == "playwright":
        return screenshot_url_pw(url, path)  # type: ignore
    raise ValueError("Screenshot not supported with lynx backend")


def read_logs() -> str:
    """Read browser console logs from the last read URL."""
    assert browser
    if browser == "playwright":
        return read_logs_playwright()  # type: ignore
    raise ValueError("Browser logs not supported with lynx backend")


tool = ToolSpec(
    name="browser",
    desc="Browse, search or screenshot the web",
    examples=examples,
    functions=[read_url, search, screenshot_url, read_logs],
    available=has_browser_tool,
    init=init,
)
__doc__ = tool.get_doc(__doc__)
