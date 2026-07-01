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
    from gptme.eval.types import EvalSpec, ResultContext


# ---------------------------------------------------------------------------
# Trajectory-check helpers
# ---------------------------------------------------------------------------


def _role_contents(messages: list[Message], role: str) -> str:
    return "\n".join(msg.content for msg in messages if msg.role == role)


def check_used_snapshot_or_observe_web(messages: list[Message]) -> bool:
    """Agent must use snapshot_url or observe_web, not screenshot, for a pure web task."""
    assistant_log = _role_contents(messages, "assistant")
    return "snapshot_url(" in assistant_log or "observe_web(" in assistant_log


def check_did_not_screenshot_for_web(messages: list[Message]) -> bool:
    """Structured-first policy: screenshots should NOT be the first observation for web."""
    assistant_log = _role_contents(messages, "assistant")
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
# Expect-check helpers (named module-level functions required for
# ProcessPoolExecutor pickling — inline lambdas crash with PicklingError)
# ---------------------------------------------------------------------------


def _expect_summary_written(ctx: "ResultContext") -> bool:
    return "summary.txt" in ctx.files or len(ctx.stdout.strip()) > 5


def _expect_title_extracted(ctx: "ResultContext") -> bool:
    return "TITLE=" in ctx.stdout or "Example Domain" in ctx.stdout


def _expect_clean_exit(ctx: "ResultContext") -> bool:
    return ctx.exit_code == 0


def _expect_links_written(ctx: "ResultContext") -> bool:
    return "links.txt" in ctx.files or len(ctx.stdout.strip()) > 10


def _expect_at_least_one_title(ctx: "ResultContext") -> bool:
    return len(ctx.stdout.strip()) > 5


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
            "You are in computer-use mode. Use observe_web('https://news.ycombinator.com') "
            "or snapshot_url('https://news.ycombinator.com') to get the page structure — "
            "prefer the structured approach over taking screenshots. "
            "Find the top 3 story titles (the text of the first 3 ranked links). "
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
]
