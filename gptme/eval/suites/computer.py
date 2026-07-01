"""Eval suite for computer-use capabilities (issue #216).

Validates end-to-end computer-use workflows:
- Structured-first web interaction via ARIA snapshots (no screenshot cost)
- Backend selection policy: prefers snapshot_url / observe_web for web, not screenshot
- Web content extraction and summarization

These tests run without a physical display because they use Playwright's
headless mode via the browser tool. Desktop/screenshot tests that require
an X11 display are not included here — they belong in manual or CI-with-display
pipelines.
"""

from typing import TYPE_CHECKING

from gptme.message import Message
from gptme.tools.base import ToolUse

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


# ---------------------------------------------------------------------------
# Trajectory-check helpers
# ---------------------------------------------------------------------------


def _executed_tool_calls(messages: list[Message]) -> list[str]:
    """Code of every runnable tool call, across assistant messages, in call order.

    Scans parsed ``ToolUse`` blocks rather than raw message text, so a tool
    name mentioned in prose (e.g. "I will call observe_web(...)") without an
    actual executable code block does not count as having been used.
    """
    return [
        tu.content
        for msg in messages
        if msg.role == "assistant"
        for tu in ToolUse.iter_from_content(msg.content)
        if tu.is_runnable and tu.content is not None
    ]


def check_used_snapshot_or_observe_web(messages: list[Message]) -> bool:
    """Agent must actually call snapshot_url or observe_web, not screenshot, for a pure web task."""
    return any(
        "snapshot_url(" in code or "observe_web(" in code
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
            "summary.txt written": lambda ctx: (
                "summary.txt" in ctx.files or len(ctx.stdout.strip()) > 5
            ),
            "title extracted": lambda ctx: (
                "TITLE=" in ctx.stdout or "Example Domain" in ctx.stdout
            ),
            "clean exit": lambda ctx: ctx.exit_code == 0,
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
            "links.txt written": lambda ctx: (
                "links.txt" in ctx.files or len(ctx.stdout.strip()) > 10
            ),
            "at least one title extracted": lambda ctx: len(ctx.stdout.strip()) > 5,
            "clean exit": lambda ctx: ctx.exit_code == 0,
        },
        "check_log": {
            "used structured snapshot for web content": check_used_snapshot_or_observe_web,
        },
    },
]
