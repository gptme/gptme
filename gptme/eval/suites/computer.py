"""Eval suite for computer-use capabilities (issue #216).

Validates end-to-end computer-use workflows:
- Structured-first web interaction via ARIA snapshots (no screenshot cost)
- Backend selection policy: prefers snapshot_url / observe_web for web, not screenshot
- Web content extraction and summarization
- Interactive web actions: open_page, fill_element, click_element (the "Can it Tweet?" pipeline)

These tests run without a physical display because they use Playwright's
headless mode via the browser tool. Desktop/screenshot tests that require
an X11 display are not included here — they belong in manual or CI-with-display
pipelines.
"""

import logging
from typing import TYPE_CHECKING

from gptme.message import Message
from gptme.tools.base import ToolUse

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Trajectory-check helpers
# ---------------------------------------------------------------------------


def _executed_tool_calls(messages: list[Message]) -> list[str]:
    """Code of every runnable tool call, across assistant messages, in call order.

    Scans parsed ``ToolUse`` blocks rather than raw message text, so a tool
    name mentioned in prose (e.g. "I will call observe_web(...)") without an
    actual executable code block does not count as having been used.

    Note: ``tu.is_runnable`` and ``ToolUse.iter_from_content`` both resolve
    against the global tool registry (``get_tool`` / ``get_tool_for_langtag``).
    If ``init_tools()`` was never called — e.g. in a unit test constructing
    synthetic ``Message`` objects — the registry is empty and this returns
    ``[]`` for every message, which makes both trajectory checks below fail
    silently rather than raising. This matches the existing pattern in
    ``count_tool_calls`` (``eval/run.py``).
    """
    calls = [
        tu.content
        for msg in messages
        if msg.role == "assistant"
        for tu in ToolUse.iter_from_content(msg.content)
        if tu.is_runnable and tu.content is not None
    ]
    if not calls and any(msg.role == "assistant" for msg in messages):
        logger.debug(
            "_executed_tool_calls found no runnable tool calls; "
            "if this is unexpected, verify init_tools() has been called"
        )
    return calls


def check_used_snapshot_or_observe_web(messages: list[Message]) -> bool:
    """Agent must actually call snapshot_url or observe_web, not screenshot, for a pure web task."""
    return any(
        "snapshot_url(" in code or "observe_web(" in code
        for code in _executed_tool_calls(messages)
    )


def check_used_open_page(messages: list[Message]) -> bool:
    """Agent must use open_page() for interactive navigation (not a one-shot read_url)."""
    return any("open_page(" in code for code in _executed_tool_calls(messages))


def check_used_fill_element(messages: list[Message]) -> bool:
    """Agent must use fill_element() to fill a form field (not type() or screenshot-click)."""
    return any("fill_element(" in code for code in _executed_tool_calls(messages))


def check_used_click_element(messages: list[Message]) -> bool:
    """Agent must use click_element() to click a button (not coordinate-based clicking)."""
    return any("click_element(" in code for code in _executed_tool_calls(messages))


def check_used_open_page_or_click_element(messages: list[Message]) -> bool:
    """Agent must navigate interactively with open_page() or click_element()."""
    return any(
        "open_page(" in code or "click_element(" in code
        for code in _executed_tool_calls(messages)
    )


def check_did_not_screenshot_for_web(messages: list[Message]) -> bool:
    """Structured-first policy: screenshots should NOT be the first observation for web."""
    calls = _executed_tool_calls(messages)
    first_snapshot = next(
        (
            i
            for i, code in enumerate(calls)
            if "snapshot_url(" in code or "observe_web(" in code
        ),
        -1,
    )
    first_screenshot = next(
        (
            i
            for i, code in enumerate(calls)
            if any(
                needle in code
                for needle in (
                    "computer('screenshot')",
                    'computer("screenshot")',
                    "computer(action='screenshot')",
                    'computer(action="screenshot")',
                )
            )
        ),
        -1,
    )
    if first_snapshot == -1:
        # never used structured approach at all — fail
        return False
    if first_screenshot == -1:
        # used structured approach, never took a screenshot — ideal
        return True
    # structured approach came first — policy respected
    return first_snapshot < first_screenshot


# ---------------------------------------------------------------------------
# Expect-check helpers (named module-level functions required for
# ProcessPoolExecutor pickling — inline lambdas crash with PicklingError)
# ---------------------------------------------------------------------------


def _expect_summary_written(ctx) -> bool:
    return "summary.txt" in ctx.files or len(ctx.stdout.strip()) > 5


def _expect_title_extracted(ctx) -> bool:
    return "TITLE=" in ctx.stdout or "Example Domain" in ctx.stdout


def _expect_clean_exit(ctx) -> bool:
    return ctx.exit_code == 0


def _expect_links_written(ctx) -> bool:
    return "links.txt" in ctx.files or len(ctx.stdout.strip()) > 10


def _expect_at_least_one_title(ctx) -> bool:
    return len(ctx.stdout.strip()) > 5


def _expect_result_written(ctx) -> bool:
    return "result.txt" in ctx.files or len(ctx.stdout.strip()) > 5


def _expect_form_submitted(ctx) -> bool:
    # httpbin returns the submitted fields in a JSON body or as text
    return "custname" in ctx.stdout or "result.txt" in ctx.files


def _expect_page2_content(ctx) -> bool:
    return "navigation.txt" in ctx.files or len(ctx.stdout.strip()) > 10


def _expect_second_page_reached(ctx) -> bool:
    content = ctx.files.get("navigation.txt")
    if content is None:
        return False
    if isinstance(content, bytes):
        content = content.decode(errors="replace")
    return len(content.strip()) > 5


# ---------------------------------------------------------------------------
# Eval specs
# ---------------------------------------------------------------------------

tests: list["EvalSpec"] = [
    {
        "name": "computer-use-web-observe",
        "files": {},
        "run": "cat summary.txt",
        "prompt": (
            "You are in computer-use mode. Use the structured-first approach to read "
            "https://example.com — call snapshot_url('https://example.com') or "
            "observe_web('https://example.com') to get an ARIA accessibility snapshot "
            "(do NOT take a screenshot for this step). "
            "From the snapshot extract: (1) the page title/heading and "
            "(2) the first sentence of the main paragraph. "
            "Write these to summary.txt with labels TITLE= and CONTENT=."
        ),
        "tools": ["browser", "computer", "vision", "ipython", "save"],
        "expect": {
            "summary.txt written": _expect_summary_written,
            "title extracted": _expect_title_extracted,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "used structured snapshot (not screenshot) for web": check_used_snapshot_or_observe_web,
            "structured approach before any screenshot": check_did_not_screenshot_for_web,
        },
    },
    {
        "name": "computer-use-web-extract-links",
        "files": {},
        "run": "cat links.txt",
        "prompt": (
            "You are in computer-use mode. Use observe_web('https://en.wikipedia.org/wiki/Main_Page') "
            "or snapshot_url('https://en.wikipedia.org/wiki/Main_Page') to get the page structure — "
            "prefer the structured approach over taking screenshots. "
            "Find the top 3 linked article titles you see on the page. "
            "Write each title on its own line to links.txt."
        ),
        "tools": ["browser", "computer", "vision", "ipython", "save"],
        "expect": {
            "links.txt written": _expect_links_written,
            "at least one title extracted": _expect_at_least_one_title,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "used structured snapshot for web content": check_used_snapshot_or_observe_web,
        },
    },
    # --- Interactive web action tests (the "Can it Tweet?" pipeline) ---
    # These validate that the agent can use open_page + fill_element + click_element
    # (structured DOM interaction) rather than screenshot-guessing coordinates.
    # httpbin.org/forms/post is a stable public form that returns submitted values.
    {
        "name": "computer-use-web-form-fill",
        "files": {},
        "run": "cat result.txt",
        "prompt": (
            "You are in computer-use mode. Use the browser tool to fill and submit a web form:\n"
            "1. Call open_page('https://httpbin.org/forms/post') to open the pizza order form.\n"
            "2. Call fill_element('[name=\"custname\"]', 'TestUser') to fill the customer name field.\n"
            "3. Call fill_element('[name=\"custemail\"]', 'test@example.com') to fill the email field.\n"
            "4. Call click_element('[type=\"submit\"]') to submit the form.\n"
            "5. Call read_page_text() to read the response.\n"
            "6. Write the response (or a summary) to result.txt."
        ),
        "tools": ["browser", "computer", "vision", "ipython", "save"],
        "expect": {
            "result.txt written": _expect_result_written,
            "form submission reflected": _expect_form_submitted,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "used open_page for interactive navigation": check_used_open_page,
            "used fill_element for form input": check_used_fill_element,
            "used click_element for form submission": check_used_click_element,
        },
    },
    {
        "name": "computer-use-web-navigate-multi-step",
        "files": {},
        "run": "cat navigation.txt",
        "prompt": (
            "You are in computer-use mode. Perform a two-step web navigation:\n"
            "1. Call open_page('https://en.wikipedia.org/wiki/Python_(programming_language)') "
            "to open the Python Wikipedia article.\n"
            "2. Call snapshot_url or read_page_text to read the page. Find the first "
            "external link or the 'History' section heading.\n"
            "3. Click or navigate to the 'History of Python' link (or another prominent "
            "internal link). Use click_element or open_page.\n"
            "4. Call read_page_text() on the second page.\n"
            "5. Write the title of the second page to navigation.txt."
        ),
        "tools": ["browser", "computer", "vision", "ipython", "save"],
        "expect": {
            "navigation.txt written": _expect_page2_content,
            "second page content reached": _expect_second_page_reached,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "used open_page or click_element for navigation": check_used_open_page_or_click_element,
        },
    },
]
