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

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


# ---------------------------------------------------------------------------
# Trajectory-check helpers
# ---------------------------------------------------------------------------


def _role_tool_use_contents(messages: list[Message], role: str) -> str:
    """Concatenate the content of actually-executed tool-use blocks for a role.

    Unlike scanning raw message text, this only looks inside blocks that
    gptme's own parser recognizes as runnable tool invocations (see
    ``gptme.eval.run.count_tool_calls`` for the same pattern). An agent that
    merely describes calling ``observe_web(...)`` in prose, without emitting
    a real tool-use block, will not satisfy these checks.
    """
    from gptme.tools import ToolUse

    parts: list[str] = []
    for msg in messages:
        if msg.role != role:
            continue
        parts.extend(
            tu.content
            for tu in ToolUse.iter_from_content(msg.content)
            if tu.is_runnable and tu.content
        )
    return "\n".join(parts)


def check_used_snapshot_or_observe_web(messages: list[Message]) -> bool:
    """Agent must use snapshot_url or observe_web, not screenshot, for a pure web task."""
    assistant_log = _role_tool_use_contents(messages, "assistant")
    return "snapshot_url(" in assistant_log or "observe_web(" in assistant_log


def check_did_not_screenshot_for_web(messages: list[Message]) -> bool:
    """Structured-first policy: screenshots should NOT be the first observation for web."""
    assistant_log = _role_tool_use_contents(messages, "assistant")
    first_snapshot = min(
        (
            assistant_log.find(needle)
            for needle in ("snapshot_url(", "observe_web(")
            if needle in assistant_log
        ),
        default=-1,
    )
    first_screenshot = min(
        (
            assistant_log.find(needle)
            for needle in (
                "computer('screenshot')",
                'computer("screenshot")',
                "computer(action='screenshot')",
                'computer(action="screenshot")',
            )
            if needle in assistant_log
        ),
        default=-1,
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
            "Find the 'In the news' section and extract the first 3 linked article titles. "
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
