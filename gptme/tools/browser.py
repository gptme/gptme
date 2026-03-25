"""
Tools to let the assistant control a browser, including:
 - loading pages
 - reading their contents
 - searching the web
 - taking screenshots (Playwright only)
 - getting ARIA accessibility snapshots (Playwright only)
 - interactive browsing: click, fill forms, scroll (Playwright only)
 - reading PDFs (with page limits and vision fallback hints)
 - converting PDFs to images (using pdftoppm, ImageMagick, or vips)

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

Provider Native Search:
 - When using Anthropic Claude models, native web search can be enabled
 - This uses Anthropic's built-in web search instead of web scraping
 - More reliable than Google/DuckDuckGo scraping (which is blocked by bot detection)
 - Configuration:

   .. code-block:: bash

       export GPTME_ANTHROPIC_WEB_SEARCH=true
       export GPTME_ANTHROPIC_WEB_SEARCH_MAX_USES=5  # Optional, default is 5

.. note::

    This is an experimental feature. It needs some work to be more robust and useful.
"""

import base64
import binascii
import importlib
import importlib.metadata
import importlib.util
import json
import logging
import re
import shutil
import subprocess
from dataclasses import replace
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Literal

import requests

from ..util import console
from ..util.context import md_codeblock
from ..util.gh import (
    get_github_issue_content,
    get_github_pr_content,
    parse_github_url,
    transform_github_url,
)
from .base import ToolSpec, ToolUse

try:
    import pypdf

    has_pypdf = True
except ImportError:
    has_pypdf = False


def has_playwright() -> bool:
    """Check if playwright is available."""
    return importlib.util.find_spec("playwright") is not None


def has_lynx() -> bool:
    """Check if lynx is available."""
    return shutil.which("lynx") is not None


browser: Literal["playwright", "lynx"] | None = (
    "playwright" if has_playwright() else ("lynx" if has_lynx() else None)
)


# PDF-to-image CLI tool detection
def _has_imagemagick() -> bool:
    """Check if ImageMagick's convert command is available."""
    return shutil.which("convert") is not None


def _has_pdftoppm() -> bool:
    """Check if pdftoppm (from poppler-utils) is available."""
    return shutil.which("pdftoppm") is not None


def _has_vips() -> bool:
    """Check if vips CLI is available."""
    return shutil.which("vips") is not None


def _get_pdf_to_image_hints() -> str:
    """Get hints for converting PDF to images using available CLI tools.

    Auto-detects which tools are installed and provides appropriate guidance.
    """
    available_tools: list[str] = []
    if _has_pdftoppm():
        available_tools.append("pdftoppm")
    if _has_imagemagick():
        available_tools.append("convert")
    if _has_vips():
        available_tools.append("vips")

    if available_tools:
        tool_list = ", ".join(available_tools)
        return (
            f"**PDF-to-image tools available**: {tool_list}\n\n"
            "Use the `pdf_to_images()` function to convert PDF pages to images:\n"
            "```python\n"
            "images = pdf_to_images('https://example.com/doc.pdf')\n"
            "for img in images:\n"
            "    view_image(img)  # Analyze with vision\n"
            "```\n\n"
            "Options: `pages=(1, 3)` for specific pages, `dpi=200` for higher resolution."
        )
    return (
        "**No PDF-to-image tools detected.** Install one of:\n"
        "- `pdftoppm` (recommended): `sudo apt install poppler-utils` or `brew install poppler`\n"
        "- `convert` (ImageMagick): `sudo apt install imagemagick` or `brew install imagemagick`\n"
        "- `vips`: `sudo apt install libvips-tools` or `brew install vips`\n\n"
        "After installing, use `pdf_to_images()` to convert, then vision to analyze."
    )


def pdf_to_images(
    url_or_path: str,
    output_dir: str | Path | None = None,
    pages: tuple[int, int] | None = None,
    dpi: int = 150,
) -> list[Path]:
    """Convert PDF pages to images using auto-detected CLI tools.

    Auto-detects and uses the first available tool: pdftoppm, ImageMagick convert, or vips.

    Args:
        url_or_path: URL or local path to PDF file
        output_dir: Directory to save images (default: creates temp directory)
        pages: Optional tuple of (first_page, last_page) to convert (1-indexed).
               If None, converts all pages.
        dpi: Resolution for output images (default: 150)

    Returns:
        List of paths to generated PNG images

    Raises:
        RuntimeError: If no PDF-to-image tools are available
        subprocess.CalledProcessError: If conversion fails

    Example:
        >>> images = pdf_to_images("https://example.com/doc.pdf")
        >>> for img in images:
        ...     view_image(img)  # Analyze with vision tool
    """
    import tempfile

    # Determine output directory
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="pdf_images_"))
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    # Download PDF if URL
    if url_or_path.startswith(("http://", "https://")):
        logger.info(f"Downloading PDF from: {url_or_path}")
        response = requests.get(url_or_path, timeout=60)
        response.raise_for_status()
        pdf_path = output_dir / "input.pdf"
        pdf_path.write_bytes(response.content)
    else:
        pdf_path = Path(url_or_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    output_prefix = output_dir / "page"

    # Try tools in order of preference
    if _has_pdftoppm():
        return _convert_with_pdftoppm(pdf_path, output_prefix, pages, dpi)
    if _has_imagemagick():
        return _convert_with_imagemagick(pdf_path, output_prefix, pages, dpi)
    if _has_vips():
        return _convert_with_vips(pdf_path, output_prefix, pages, dpi)
    raise RuntimeError(
        "No PDF-to-image tools available. Install one of:\n"
        "- pdftoppm: sudo apt install poppler-utils (or brew install poppler)\n"
        "- convert: sudo apt install imagemagick (or brew install imagemagick)\n"
        "- vips: sudo apt install libvips-tools (or brew install vips)"
    )


def _convert_with_pdftoppm(
    pdf_path: Path, output_prefix: Path, pages: tuple[int, int] | None, dpi: int
) -> list[Path]:
    """Convert PDF to images using pdftoppm."""
    import subprocess

    cmd = ["pdftoppm", "-png", "-r", str(dpi)]
    if pages:
        cmd.extend(["-f", str(pages[0]), "-l", str(pages[1])])
    cmd.extend([str(pdf_path), str(output_prefix)])

    logger.info(f"Converting PDF with pdftoppm: {' '.join(cmd)}")
    subprocess.run(cmd, check=True, capture_output=True, text=True)

    # pdftoppm creates files like: page-1.png, page-2.png, etc.
    return sorted(output_prefix.parent.glob(f"{output_prefix.name}-*.png"))


def _convert_with_imagemagick(
    pdf_path: Path, output_prefix: Path, pages: tuple[int, int] | None, dpi: int
) -> list[Path]:
    """Convert PDF to images using ImageMagick convert."""
    import subprocess

    # ImageMagick uses 0-indexed pages
    if pages:
        page_spec = f"[{pages[0] - 1}-{pages[1] - 1}]"
        input_spec = f"{pdf_path}{page_spec}"
    else:
        input_spec = str(pdf_path)

    output_pattern = f"{output_prefix}-%d.png"
    cmd = ["convert", "-density", str(dpi), input_spec, output_pattern]

    logger.info(f"Converting PDF with ImageMagick: {' '.join(cmd)}")
    subprocess.run(cmd, check=True, capture_output=True, text=True)

    # ImageMagick creates files like: page-0.png, page-1.png, etc.
    return sorted(output_prefix.parent.glob(f"{output_prefix.name}-*.png"))


def _convert_with_vips(
    pdf_path: Path, output_prefix: Path, pages: tuple[int, int] | None, dpi: int
) -> list[Path]:
    """Convert PDF to images using vips."""
    import subprocess

    output_files = []
    if pages:
        page_range = range(pages[0] - 1, pages[1])  # vips is 0-indexed
    else:
        # Try to get page count, default to 100 max
        page_range = range(100)

    for i, page_num in enumerate(page_range):
        output_file = output_prefix.parent / f"{output_prefix.name}-{i + 1}.png"
        cmd = [
            "vips",
            "pdfload",
            str(pdf_path),
            str(output_file),
            f"--page={page_num}",
            f"--dpi={dpi}",
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            output_files.append(output_file)
        except subprocess.CalledProcessError:
            # Reached end of pages
            if not pages:
                break
            raise

    return output_files


# Check for Perplexity availability
try:
    _perplexity_mod = importlib.import_module("._browser_perplexity", __package__)
    has_perplexity_key = _perplexity_mod.has_perplexity_key
    search_perplexity = _perplexity_mod.search_perplexity
    has_perplexity = has_perplexity_key()
except (ImportError, AttributeError):
    has_perplexity = False
    search_perplexity = None

# noreorder
if browser == "playwright":
    from ._browser_playwright import aria_snapshot as aria_snapshot_pw  # fmt: skip
    from ._browser_playwright import click_element as click_element_pw  # fmt: skip
    from ._browser_playwright import close_page as close_page_pw  # fmt: skip
    from ._browser_playwright import fill_element as fill_element_pw  # fmt: skip
    from ._browser_playwright import open_page as open_page_pw  # fmt: skip
    from ._browser_playwright import read_logs as read_logs_playwright  # fmt: skip
    from ._browser_playwright import read_page_text as read_page_text_pw  # fmt: skip
    from ._browser_playwright import read_url as read_url_playwright  # fmt: skip
    from ._browser_playwright import screenshot_url as screenshot_url_pw  # fmt: skip
    from ._browser_playwright import scroll_page as scroll_page_pw  # fmt: skip
    from ._browser_playwright import search_duckduckgo, search_google  # fmt: skip
elif browser == "lynx":
    from ._browser_lynx import read_url as read_url_lynx  # fmt: skip
    from ._browser_lynx import search as search_lynx  # fmt: skip

logger = logging.getLogger(__name__)

# Always include all engine types in the type definition
EngineType = Literal["google", "duckduckgo", "perplexity"]

SEARCH_ENGINE_ERROR_PREFIX = "Error:"


def _available_search_engines() -> list[EngineType]:
    """Return usable search backends in priority order."""
    engines: list[EngineType] = []

    if has_perplexity:
        engines.append("perplexity")

    if browser in ("playwright", "lynx"):
        engines.append("google")

    # DuckDuckGo scraping is currently known-broken outside the lynx backend.
    if browser == "lynx":
        engines.append("duckduckgo")

    return engines


def _search_with_engine(query: str, engine: EngineType) -> str:
    """Execute a search with a specific engine without fallback.

    Note: the Error branches below are unreachable when called via search(), because
    search() only includes engines that pass _available_search_engines(). They remain
    here so _search_with_engine can be called directly (e.g. in tests) without the
    availability gate.
    """
    if engine == "perplexity":
        if has_perplexity:
            assert search_perplexity is not None
            return search_perplexity(query)
        return (
            "Error: Perplexity search not available. Set PERPLEXITY_API_KEY or "
            "OPENROUTER_API_KEY environment variable or add it to "
            "~/.config/gptme/config.toml"
        )

    if engine == "google":
        if browser == "playwright":
            return search_google(query)
        if browser == "lynx":
            return search_lynx(query, "google")
        return "Error: Google search not available because no browser backend is configured"

    if engine == "duckduckgo":
        if browser == "lynx":
            return search_lynx(query, "duckduckgo")
        return (
            "Error: DuckDuckGo search is unavailable because bot detection blocks "
            "the current browser backend"
        )

    raise ValueError(f"Unknown search engine: {engine}")


def _search_failed(result: str) -> bool:
    return result.startswith(SEARCH_ENGINE_ERROR_PREFIX)


def _available_search_engines_text() -> str:
    engines = _available_search_engines()
    if not engines:
        return "none"
    return ", ".join(engines)


def examples(tool_format):
    # Define example output with newlines outside f-string (backslashes not allowed in f-string expressions)
    snapshot_example_result = (
        "Page: Example Domain\n"
        "URL: https://example.com/\n\n"
        '- WebArea "Example Domain":\n'
        '  - heading "Example Domain" [level=1]\n'
        '  - text "This domain is for use in illustrative examples..."\n'
        '  - link "More information..."'
    )
    interact_open_result = (
        "Page: Example\n"
        "URL: https://example.com/\n\n"
        '- WebArea "Example":\n  - textbox "Search" [name="q"]\n  - button "Go"'
    )
    interact_fill_result = (
        "Page: Example\n"
        "URL: https://example.com/\n\n"
        '- WebArea "Example":\n  - textbox "Search" [name="q"]: gptme\n  - button "Go"'
    )
    interact_fill_code = "fill_element('input[name=\"q\"]', 'gptme')"
    interact_click_result = (
        "Page: Search Results\n"
        "URL: https://example.com/search?q=gptme\n\n"
        '- WebArea "Search Results":\n  - heading "Results for: gptme"\n  - link "gptme on GitHub"'
    )
    read_page_text_result = "# Article Title\n\nThe article discusses..."
    pdf_example_result = (
        "--- Page 1 ---\n[PDF text content...]\n\n--- Page 2 ---\n[More content...]\n\n---\n"
        "**Note**: This PDF has 42 pages. Showing first 10 pages.\n"
        "To read more pages, use: `read_url('...', max_pages=N)` where N is the desired count, or 0 for all pages.\n\n"
        "**Tip**: If this text extraction seems incomplete or garbled (common with scanned documents, "
        "complex layouts, or image-heavy PDFs), try vision-based reading: convert pages to images "
        "using a PDF-to-image tool, then use the vision tool to analyze them."
    )
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
{md_codeblock("https://superuserlabs.org/", "... [ActivityWatch](https://activitywatch.net/) ...")}
Assistant: Couldn't find the answer on the page. Following link to the ActivityWatch website.
{ToolUse("ipython", [], "read_url('https://activitywatch.net/')").to_output(tool_format)}
System:
{md_codeblock("https://activitywatch.net/", "... Download latest version v0.12.2 ...")}
Assistant: The latest version of ActivityWatch is v0.12.2

### Searching
User: who is the founder of ActivityWatch?
Assistant: Let's search for that.
{ToolUse("ipython", [], "search('ActivityWatch founder')").to_output(tool_format)}
System:
{md_codeblock("result", "ActivityWatch was founded by Erik Bjäreholt in 2016...")}
Assistant: The founder of ActivityWatch is Erik Bjäreholt.

### Searching for latest information
User: what are the latest developments in AI?
Assistant: Let me search for that.
{ToolUse("ipython", [], "search('latest developments in AI')").to_output(tool_format)}
System:
{md_codeblock("result", "Based on recent developments, AI has seen significant advances...")}
Assistant: Based on the search results, here are the latest AI developments...

### Take screenshot of page
User: take a screenshot of the ActivityWatch website
Assistant: Certainly! I'll use the browser tool to screenshot the ActivityWatch website.
{ToolUse("ipython", [], "screenshot_url('https://activitywatch.net')").to_output(tool_format)}
System:
{md_codeblock("result", "Screenshot saved to screenshot.png")}

### Get ARIA snapshot to see interactive elements
User: what interactive elements are on example.com?
Assistant: Let me get the accessibility snapshot of the page.
{ToolUse("ipython", [], "snapshot_url('https://example.com')").to_output(tool_format)}
System:
{md_codeblock("result", snapshot_example_result)}
Assistant: The page has a heading "Example Domain", a paragraph with description text, and a link "More information...".

### Interactive browsing: open page, click, fill
User: search for gptme on example.com's search form
Assistant: I'll open the page, fill the search form, and click submit.
{ToolUse("ipython", [], "open_page('https://example.com')").to_output(tool_format)}
System:
{md_codeblock("result", interact_open_result)}
Assistant: I can see a search box and button. Let me fill in the search and click Go.
{ToolUse("ipython", [], interact_fill_code).to_output(tool_format)}
System:
{md_codeblock("result", interact_fill_result)}
{ToolUse("ipython", [], "click_element('text=Go')").to_output(tool_format)}
System:
{md_codeblock("result", interact_click_result)}
Assistant: The search was submitted and the page now shows results for "gptme".

### Read full text content of interactive page
User: what does the article say?
Assistant: Let me read the full text content of the current page.
{ToolUse("ipython", [], "read_page_text()").to_output(tool_format)}
System:
{md_codeblock("result", read_page_text_result)}
Assistant: The article covers [summary of content].

### Read URL and check browser logs
User: read this page and check if there are any console errors
Assistant: I'll read the page first and then check the browser logs.
{ToolUse("ipython", [], "read_url('https://example.com')").to_output(tool_format)}
System:
{md_codeblock("https://example.com", "This domain is for use in illustrative examples...")}
Assistant: Now let me check the browser console logs:
{ToolUse("ipython", [], "read_logs()").to_output(tool_format)}
System:
{md_codeblock("result", "No logs or errors captured.")}

### Read PDF document
User: read this research paper from arxiv
Assistant: I'll read the PDF and extract its text content.
{ToolUse("ipython", [], "read_url('https://arxiv.org/pdf/2410.12361v2')").to_output(tool_format)}
System:
{md_codeblock("result", pdf_example_result)}
Assistant: I've extracted the text from the PDF. The paper discusses [summary of key points]...
""".strip()


def _tool_instructions() -> str:
    available_search = _available_search_engines_text()
    return (
        "Browse the web: read any URL or PDF with read_url(), "
        f"search the web with search() using auto-detected backends and fallback "
        f"(available now: {available_search}), "
        "capture screenshots with screenshot_url(), "
        "use snapshot_url() to instantly understand page structure and locate interactive elements without a screenshot, "
        "use open_page() + click_element()/fill_element()/scroll_page() to fully automate "
        "multi-step web interactions such as form submissions and paginated browsing — "
        "each call returns an ARIA snapshot so you can verify the updated page state and plan your next step, "
        "use read_page_text() to get the full text content of the current interactive page as Markdown, "
        "check browser console errors with read_logs(), "
        "or convert a local PDF to images with pdf_to_images()."
    )


def init() -> ToolSpec:
    if browser == "playwright":
        console.log("Using browser tool with playwright")
    elif browser == "lynx":
        console.log("Using browser tool with lynx")
    # Note: _tool_instructions() is evaluated once at init time, so the
    # "available now:" list reflects backend availability at startup.
    # search() itself always re-evaluates _available_search_engines() at call time.
    return replace(
        tool,
        instructions_format={**tool.instructions_format, "tool": _tool_instructions()},
    )


@lru_cache
def has_browser_tool():
    return browser is not None


def _is_pdf_url(url: str) -> bool:
    """Check if URL points to a PDF file."""
    # Check URL extension
    if url.lower().endswith(".pdf"):
        return True

    # Check Content-Type header
    try:
        response = requests.head(url, allow_redirects=True, timeout=10)
        content_type = response.headers.get("Content-Type", "").lower()
        return "application/pdf" in content_type
    except requests.RequestException:
        # If we can't check headers, rely on URL extension
        return False


# Default max pages for PDF reading
DEFAULT_PDF_MAX_PAGES = 10


def _read_pdf_url(url: str, max_pages: int | None = None) -> str:
    """Read PDF content from URL using pypdf.

    Args:
        url: URL of the PDF to read
        max_pages: Maximum number of pages to read (default: 10).
                   Set to 0 to read all pages.
    """
    if not has_pypdf:
        return "Error: PDF support requires pypdf. Install with: pip install pypdf"

    # Use default if not specified
    if max_pages is None:
        max_pages = DEFAULT_PDF_MAX_PAGES

    try:
        # Download PDF content
        logger.info(f"Downloading PDF from: {url}")
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        # Read PDF
        pdf_file = BytesIO(response.content)
        reader = pypdf.PdfReader(pdf_file)
        total_pages = len(reader.pages)

        # Determine how many pages to read
        pages_to_read = total_pages if max_pages == 0 else min(max_pages, total_pages)
        truncated = pages_to_read < total_pages

        # Extract text from pages
        text_parts = []
        for i, page in enumerate(reader.pages[:pages_to_read]):
            page_text = page.extract_text()
            if page_text.strip():  # Only add non-empty pages
                text_parts.append(f"--- Page {i + 1} ---\n{page_text}")

        if not text_parts:
            return (
                "Error: PDF appears to be empty or contains only images.\n\n"
                "**Tip**: For image-based or complex PDFs, convert to images first:\n\n"
                + _get_pdf_to_image_hints()
            )

        result = "\n\n".join(text_parts)

        # Add footer with hints
        footer_parts = []

        # Truncation notice
        if truncated:
            footer_parts.append(
                f"**Note**: This PDF has {total_pages} pages. Showing first {pages_to_read} pages.\n"
                f"To read more pages, use: `read_url('{url}', max_pages=N)` where N is the desired count, or 0 for all pages."
            )

        # Vision alternative hint with CLI tool detection
        footer_parts.append(
            "**Tip**: If this text extraction seems incomplete or garbled (common with scanned documents, "
            "complex layouts, or image-heavy PDFs), try vision-based reading:\n\n"
            + _get_pdf_to_image_hints()
        )

        if footer_parts:
            result += "\n\n---\n" + "\n\n".join(footer_parts)

        logger.info(
            f"Successfully extracted text from {pages_to_read}/{total_pages} pages"
        )
        return result

    except Exception as e:
        logger.error(f"Error reading PDF: {e}")
        return f"Error reading PDF: {e}"


def read_url(url: str, max_pages: int | None = None) -> str:
    """Read a webpage or PDF in a text format.

    Args:
        url: URL to read
        max_pages: For PDFs only - maximum pages to read (default: 10).
                   Set to 0 to read all pages. Ignored for web pages.
    """
    # Check if it's a PDF first
    if _is_pdf_url(url):
        return _read_pdf_url(url, max_pages)

    # GitHub: issues and PRs
    github_info = parse_github_url(url)
    if github_info:
        if github_info["type"] == "pull":
            content = get_github_pr_content(url)
            if content:
                return f"<!-- Source: gh pr view (GitHub CLI) -->\n\n{content}"
        else:
            content = get_github_issue_content(
                github_info["owner"], github_info["repo"], github_info["number"]
            )
            if content:
                return f"<!-- Source: gh issue view (GitHub CLI) -->\n\n{content}"

    # GitHub: repo root
    if _is_github_repo_url(url):
        return _read_github_repo(url)

    # GitHub: blob URLs → convert to raw content URL
    raw_url = transform_github_url(url)
    if raw_url != url:
        url = raw_url
        # Note: will be served as raw content without browser rendering

    # Otherwise use normal browser reading (max_pages ignored)
    return _read_url_with_browser(url)


def _is_github_repo_url(url: str) -> bool:
    """Check if URL is a GitHub repository URL that gh can handle."""
    # Match github.com/owner/repo patterns (not raw files, issues, PRs, etc.)
    pattern = r"^https?://github\.com/[^/]+/[^/]+/?$"
    return bool(re.match(pattern, url.rstrip("/")))


def _read_github_repo(url: str) -> str:
    """Read a GitHub repo using gh CLI for clean, structured output."""
    match = re.match(r"https?://github\.com/([^/]+)/([^/]+)/?", url)
    if not match:
        return _read_url_with_browser(url)

    owner, repo = match.groups()
    repo = repo.rstrip("/")
    full_repo = f"{owner}/{repo}"

    try:
        result = subprocess.run(
            [
                "gh",
                "repo",
                "view",
                full_repo,
                "--json",
                "name,description,url,stargazerCount,forkCount,licenseInfo,repositoryTopics,homepageUrl,defaultBranchRef",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        if result.returncode != 0:
            logger.warning(f"gh repo view failed: {result.stderr}")
            return _read_url_with_browser(url)

        data = json.loads(result.stdout)

        lines = [f"# {data['name']}", ""]

        if data.get("description"):
            lines += [data["description"], ""]

        lines.append(f"**URL**: {data['url']}")
        lines.append(f"**Stars**: {data.get('stargazerCount', 'N/A')}")
        lines.append(f"**Forks**: {data.get('forkCount', 'N/A')}")

        if data.get("licenseInfo"):
            lines.append(f"**License**: {data['licenseInfo'].get('name', 'N/A')}")

        topics = [t["name"] for t in data.get("repositoryTopics", [])]
        if topics:
            lines.append(f"**Topics**: {', '.join(topics)}")

        if data.get("homepageUrl"):
            lines.append(f"**Homepage**: {data['homepageUrl']}")

        branch = (data.get("defaultBranchRef") or {}).get("name", "main")
        lines += [f"**Default branch**: {branch}", ""]

        # Fetch README
        readme_result = subprocess.run(
            ["gh", "api", f"repos/{full_repo}/readme"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if readme_result.returncode == 0:
            try:
                readme_data = json.loads(readme_result.stdout)
                readme_content = base64.b64decode(readme_data["content"]).decode(
                    "utf-8"
                )
            except (
                binascii.Error,
                json.JSONDecodeError,
                KeyError,
                UnicodeDecodeError,
            ) as e:
                logger.warning(f"Failed to parse README for {full_repo}: {e}")
            else:
                lines += ["## README", "", readme_content]
        else:
            logger.debug(f"No README found: {readme_result.stderr}")

        return "<!-- Source: gh repo view (GitHub CLI) -->\n\n" + "\n".join(lines)

    except FileNotFoundError:
        logger.warning("gh CLI not found, falling back to browser")
    except Exception as e:
        logger.warning(f"Error reading GitHub repo with gh: {e}")

    return _read_url_with_browser(url)


def _read_url_with_browser(url: str) -> str:
    """Read a URL using the available browser backend (no PDF/GitHub special-casing)."""
    if browser == "playwright":
        return read_url_playwright(url)
    if browser == "lynx":
        return read_url_lynx(url)
    raise RuntimeError("No browser backend available")


def search(query: str, engine: EngineType | None = None) -> str:
    """Search for a query on a search engine.

    If no engine is specified, automatically chooses the best available backend
    and falls back to the next usable backend on failure.
    """
    available_engines = _available_search_engines()

    if engine is not None:
        if engine not in available_engines:
            available_text = (
                ", ".join(available_engines) if available_engines else "none"
            )
            return (
                f"Error: Search engine '{engine}' is not currently available. "
                f"Available engines: {available_text}"
            )
        engines_to_try = [engine]
    else:
        engines_to_try = available_engines

    if not engines_to_try:
        return (
            "Error: No search backends are currently available. "
            "Set PERPLEXITY_API_KEY or OPENROUTER_API_KEY, or install a supported browser backend."
        )

    errors: list[str] = []
    for candidate in engines_to_try:
        logger.info(f"Searching for '{query}' on {candidate}")
        try:
            result = _search_with_engine(query, candidate)
        except Exception as exc:
            logger.warning(
                "Search backend %s failed with exception", candidate, exc_info=exc
            )
            errors.append(f"{candidate}: {exc}")
            continue
        if not _search_failed(result):
            return result
        errors.append(
            f"{candidate}: {result.removeprefix(SEARCH_ENGINE_ERROR_PREFIX).strip()}"
        )

    target = (
        "All available search backends"
        if engine is None
        else f"The requested search backend '{engine}'"
    )
    return f"Error: {target} failed for query '{query}'.\n" + "\n".join(
        f"- {error}" for error in errors
    )


def search_playwright(query: str, engine: EngineType = "google") -> str:
    """Search for a query on a search engine using Playwright."""
    if engine == "google":
        return search_google(query)
    if engine == "duckduckgo":
        return search_duckduckgo(query)
    raise ValueError(f"Unknown search engine: {engine}")


def screenshot_url(url: str, path: Path | str | None = None) -> Path:
    """Take a screenshot of a webpage."""
    assert browser
    if browser == "playwright":
        return screenshot_url_pw(url, path)
    raise ValueError("Screenshot not supported with lynx backend")


def snapshot_url(url: str) -> str:
    """Get the ARIA accessibility snapshot of a webpage.

    Returns a structured text representation of the page's accessibility tree,
    showing interactive elements (buttons, links, inputs) with their roles and names.
    Useful for understanding page structure and finding elements to interact with.

    The output includes a metadata header with the page title and current URL
    (which may differ from the requested URL after redirects).
    """
    assert browser
    if browser == "playwright":
        return aria_snapshot_pw(url)
    raise ValueError("ARIA snapshots not supported with lynx backend")


def read_logs() -> str:
    """Read browser console logs from the last read URL."""
    assert browser
    if browser == "playwright":
        return read_logs_playwright()
    raise ValueError("Browser logs not supported with lynx backend")


def open_page(url: str) -> str:
    """Open a page for interactive browsing. Returns ARIA accessibility snapshot.

    Use this instead of read_url() when you need to interact with the page
    (click buttons, fill forms, scroll). The page stays open for subsequent
    click_element(), fill_element(), and scroll_page() calls.

    The output includes a metadata header with the page title and current URL.
    """
    assert browser
    if browser == "playwright":
        return open_page_pw(url)
    raise ValueError("Interactive browsing not supported with lynx backend")


def close_page() -> str:
    """Close the current interactive browsing page.

    Frees browser resources. A new page can be opened with open_page().
    """
    assert browser
    if browser == "playwright":
        return close_page_pw()
    raise ValueError("Interactive browsing not supported with lynx backend")


def read_page_text() -> str:
    """Read the full text content of the current interactive page as Markdown.

    Requires open_page() to be called first. Returns the page body converted
    to Markdown, preserving text formatting. Useful for reading article text,
    documentation, or other content after navigating to a page.

    Unlike read_url(), this reads from the current interactive session — so
    it reflects the page state after any clicks, form fills, or navigation.
    """
    assert browser
    if browser == "playwright":
        return read_page_text_pw()
    raise ValueError("Interactive browsing not supported with lynx backend")


def click_element(selector: str) -> str:
    """Click an element on the current page and return updated ARIA snapshot.

    Requires open_page() to be called first.

    Args:
        selector: Playwright selector to find the element. Supports:
            - CSS: "#submit-btn", ".nav-link", "button"
            - Text: "text=Submit", "text=Log in"
            - Role: "role=button[name='Submit']"
            - Chained: "form >> text=Submit"
    """
    assert browser
    if browser == "playwright":
        return click_element_pw(selector)
    raise ValueError("Interactive browsing not supported with lynx backend")


def fill_element(selector: str, value: str) -> str:
    """Fill a form field on the current page and return updated ARIA snapshot.

    Requires open_page() to be called first. Clears any existing value before filling.

    Args:
        selector: Playwright selector for the input/textarea element.
        value: Text to fill into the field.
    """
    assert browser
    if browser == "playwright":
        return fill_element_pw(selector, value)
    raise ValueError("Interactive browsing not supported with lynx backend")


def scroll_page(direction: str = "down", amount: int = 500) -> str:
    """Scroll the current page and return updated ARIA snapshot.

    Requires open_page() to be called first.

    Args:
        direction: "up" or "down" (default: "down")
        amount: Pixels to scroll (default: 500)
    """
    assert browser
    if browser == "playwright":
        return scroll_page_pw(direction, amount)
    raise ValueError("Interactive browsing not supported with lynx backend")


tool = ToolSpec(
    name="browser",
    desc="Browse, interact with, search, or screenshot the web",
    instructions_format={
        # Compact description for OpenAI tool format (full docstrings exceed 1024 chars)
        "tool": "Browse the web: read any URL or PDF with read_url(), "
        "search the web with search() using auto-detected backends and fallback, "
        "capture screenshots with screenshot_url(), "
        "get ARIA accessibility snapshots with snapshot_url(), "
        "interact with pages using open_page() + click_element()/fill_element()/scroll_page(), "
        "read interactive page content with read_page_text(), "
        "check browser console errors with read_logs(), "
        "or convert a local PDF to images with pdf_to_images().",
    },
    examples=examples,
    functions=[
        read_url,
        search,
        screenshot_url,
        snapshot_url,
        open_page,
        close_page,
        read_page_text,
        click_element,
        fill_element,
        scroll_page,
        read_logs,
        pdf_to_images,
    ],
    available=has_browser_tool,
    init=init,
)
__doc__ = tool.get_doc(__doc__)
