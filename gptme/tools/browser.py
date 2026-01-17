"""
Tools to let the assistant control a browser, including:
 - loading pages
 - reading their contents
 - searching the web
 - taking screenshots (Playwright only)
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

import importlib.metadata
import importlib.util
import logging
import shutil
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Literal

import requests

from ..util import console
from .base import ToolSpec, ToolUse

try:
    import pypdf

    has_pypdf = True
except ImportError:
    has_pypdf = False

has_playwright = lambda: importlib.util.find_spec("playwright") is not None  # noqa
has_lynx = lambda: shutil.which("lynx")  # noqa
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
    else:
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
    elif _has_imagemagick():
        return _convert_with_imagemagick(pdf_path, output_prefix, pages, dpi)
    elif _has_vips():
        return _convert_with_vips(pdf_path, output_prefix, pages, dpi)
    else:
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
        page_spec = f"[{pages[0]-1}-{pages[1]-1}]"
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
        output_file = output_prefix.parent / f"{output_prefix.name}-{i+1}.png"
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
    # Define example output with newlines outside f-string (backslashes not allowed in f-string expressions)
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

### Read PDF document
User: read this research paper from arxiv
Assistant: I'll read the PDF and extract its text content.
{ToolUse("ipython", [], "read_url('https://arxiv.org/pdf/2410.12361v2')").to_output(tool_format)}
System:
{ToolUse("result", [], pdf_example_result).to_output()}
Assistant: I've extracted the text from the PDF. The paper discusses [summary of key points]...
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
    except Exception:
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
                text_parts.append(f"--- Page {i+1} ---\n{page_text}")

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
        return f"Error reading PDF: {str(e)}"


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

    # Otherwise use normal browser reading (max_pages ignored)
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
    functions=[read_url, search, screenshot_url, read_logs, pdf_to_images],
    available=has_browser_tool,
    init=init,
)
__doc__ = tool.get_doc(__doc__)
