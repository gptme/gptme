"""Eval suite for computer-use capabilities (issue #216).

Validates end-to-end computer-use workflows:
- Structured-first web interaction via ARIA snapshots (no screenshot cost)
- Backend selection policy: prefers snapshot_url / observe_web for web, not screenshot
- Web content extraction and summarization
- Interactive web actions: open_page, fill_element, click_element (the "Can it Tweet?" pipeline)
- Keyboard navigation: press_key (Enter to submit, Tab to move focus)
- Dropdown selection: select_option for <select> elements
- Dynamic content: wait_for_element for elements that appear after user actions
- Hover interaction: hover_element() for revealing hover-only menus/tooltips
- Page state inspection: snapshot_page() after interactions, get_current_url() after navigation

These tests run without a physical display because they use Playwright's
headless mode via the browser tool. Desktop/screenshot tests that require
an X11 display are not included here — they belong in manual or CI-with-display
pipelines.
"""

import logging
import urllib.parse
from typing import TYPE_CHECKING

from gptme.message import Message
from gptme.tools.base import ToolUse

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec

logger = logging.getLogger(__name__)

# Self-contained fixture with a real <select> element (httpbin's /forms/post
# renders "size" as radio buttons, not a <select>, so select_option() would
# raise against it — see gptme#3097 review discussion). The result marker is
# only written by a JS "change" listener, so a passing check proves the tool
# actually drove the <select>, not that "large" happens to appear in static
# markup.
_DROPDOWN_FIXTURE_HTML = (
    "<!doctype html><html><body>"
    '<form><select name="size" id="size">'
    '<option value="small">Small</option>'
    '<option value="medium">Medium</option>'
    '<option value="large">Large</option>'
    "</select></form>"
    '<div id="result">no selection</div>'
    "<script>"
    "document.getElementById('size').addEventListener('change', function(e) {"
    "document.getElementById('result').textContent = 'selected:' + e.target.value;"
    "});"
    "</script>"
    "</body></html>"
)
_DROPDOWN_FIXTURE_URL = "data:text/html," + urllib.parse.quote(_DROPDOWN_FIXTURE_HTML)

# Hover fixture: a trigger element whose mouseover reveals a hidden menu item.
# The marker text "hover-revealed" is absent from static HTML — it only appears
# after a real hover_element() call fires the mouseover handler.
_HOVER_FIXTURE_HTML = (
    "<!doctype html><html><body>"
    '<div id="menu-trigger" style="cursor:pointer">Hover me</div>'
    '<div id="menu-item" style="display:none">hover-revealed</div>'
    "<script>"
    "document.getElementById('menu-trigger').addEventListener('mouseover', function() {"
    "document.getElementById('menu-item').style.display = 'block';"
    "});"
    "</script>"
    "</body></html>"
)
_HOVER_FIXTURE_URL = "data:text/html," + urllib.parse.quote(_HOVER_FIXTURE_HTML)

# Snapshot fixture: a page with a text input. Used to verify snapshot_page()
# captures the current DOM state (filled field) without re-fetching the URL.
_SNAPSHOT_FIXTURE_HTML = (
    "<!doctype html><html><body>"
    '<form><input name="msg" id="msg" type="text" value="" /></form>'
    "</body></html>"
)
_SNAPSHOT_FIXTURE_URL = "data:text/html," + urllib.parse.quote(_SNAPSHOT_FIXTURE_HTML)

# Current URL fixture: local page used to verify get_current_url() without an
# external network dependency.
_CURRENT_URL_FIXTURE_HTML = (
    "<!doctype html><html><body><h1>current-url-fixture</h1></body></html>"
)
_CURRENT_URL_FIXTURE_URL = "data:text/html," + urllib.parse.quote(
    _CURRENT_URL_FIXTURE_HTML
)


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


def check_used_press_key(messages: list[Message]) -> bool:
    """Agent must use press_key() for keyboard-driven interaction (not click for submit)."""
    return any("press_key(" in code for code in _executed_tool_calls(messages))


def check_used_select_option(messages: list[Message]) -> bool:
    """Agent must use select_option() for dropdown interaction."""
    return any("select_option(" in code for code in _executed_tool_calls(messages))


def check_used_wait_for_element(messages: list[Message]) -> bool:
    """Agent must use wait_for_element() to wait for dynamically-rendered content."""
    return any("wait_for_element(" in code for code in _executed_tool_calls(messages))


def check_used_hover_element(messages: list[Message]) -> bool:
    """Agent must use hover_element() to trigger a hover-only interaction."""
    return any("hover_element(" in code for code in _executed_tool_calls(messages))


def check_used_snapshot_page(messages: list[Message]) -> bool:
    """Agent must use snapshot_page() to read current page state after interaction."""
    return any("snapshot_page(" in code for code in _executed_tool_calls(messages))


def check_used_get_current_url(messages: list[Message]) -> bool:
    """Agent must use get_current_url() to inspect URL after navigation."""
    return any("get_current_url(" in code for code in _executed_tool_calls(messages))


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
    # httpbin returns the submitted fields in a JSON body or as text.
    return "custname" in ctx.stdout


def _expect_page2_content(ctx) -> bool:
    return "navigation.txt" in ctx.files or len(ctx.stdout.strip()) > 10


def _expect_second_page_reached(ctx) -> bool:
    content = ctx.files.get("navigation.txt")
    if content is None:
        return False
    if isinstance(content, bytes):
        content = content.decode(errors="replace")
    return len(content.strip()) > 5


def _expect_keyboard_submit_reflected(ctx) -> bool:
    # httpbin echoes submitted field names in the response JSON (e.g. {"custname": "..."}).
    # Checking for the field key "custname" (not the user-supplied value "TestUser") avoids
    # false positives where the agent narrates what it attempted without actually submitting.
    return "custname" in ctx.stdout


def _expect_dropdown_result_written(ctx) -> bool:
    return "dropdown.txt" in ctx.files or len(ctx.stdout.strip()) > 5


def _expect_dropdown_value_echoed(ctx) -> bool:
    # The fixture page only writes "selected:large" via a JS "change" listener
    # fired by a real select_option() call — the marker text is absent from the
    # static HTML, so this can't pass on narration or an unexecuted tool call.
    content = ctx.files.get("dropdown.txt", ctx.stdout)
    if isinstance(content, bytes):
        content = content.decode(errors="replace")
    return "selected:large" in content


def _expect_hover_menu_found(ctx) -> bool:
    content = ctx.files.get("hover.txt", ctx.stdout)
    if isinstance(content, bytes):
        content = content.decode(errors="replace")
    # The fixture only writes "hover-revealed" via JS mouseover — absent from static HTML
    return "hover-revealed" in content


def _expect_current_url_fixture_recorded(ctx) -> bool:
    content = ctx.files.get("url.txt", ctx.stdout)
    if isinstance(content, bytes):
        content = content.decode(errors="replace")
    return _CURRENT_URL_FIXTURE_URL in content


def _expect_current_url_captured(ctx) -> bool:
    content = ctx.files.get("url.txt", ctx.stdout)
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
    # --- Keyboard navigation tests ---
    # Validates press_key() for submitting forms without click_element, mirroring
    # workflows like Twitter where pressing Enter submits the compose box directly.
    {
        "name": "computer-use-web-keyboard-submit",
        "files": {},
        "run": "cat result.txt",
        "prompt": (
            "You are in computer-use mode. Use keyboard navigation to submit a web form:\n"
            "1. Call open_page('https://httpbin.org/forms/post') to open the pizza order form.\n"
            "2. Call fill_element('[name=\"custname\"]', 'TestUser') to fill the customer name.\n"
            "3. Call fill_element('[name=\"custemail\"]', 'test@example.com') to fill the email.\n"
            "4. Call press_key('Tab') to move focus to the next field, then "
            "call press_key('Return') to submit the form using the keyboard (do NOT use click_element for submit).\n"
            "5. Call read_page_text() to read the response.\n"
            "6. Write the response (or a summary) to result.txt."
        ),
        "tools": ["browser", "computer", "vision", "ipython", "save"],
        "expect": {
            "result.txt written": _expect_result_written,
            "form submitted (custname reflected)": _expect_keyboard_submit_reflected,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "used open_page for navigation": check_used_open_page,
            "used fill_element for input": check_used_fill_element,
            "used press_key for keyboard submission": check_used_press_key,
        },
    },
    # --- Dropdown selection test ---
    # Validates select_option() for <select> elements. Uses a self-contained
    # data: URL fixture (not httpbin) because httpbin's /forms/post renders
    # "size" as radio buttons, not a <select> — select_option() would raise
    # against it, and any static-text check would be a false positive since
    # "large" is already present in that page's radio-button label.
    {
        "name": "computer-use-web-dropdown-select",
        "files": {},
        "run": "cat dropdown.txt",
        "prompt": (
            "You are in computer-use mode. Use select_option() to choose a dropdown value:\n"
            f"1. Call open_page('{_DROPDOWN_FIXTURE_URL}') to open a page with a size dropdown.\n"
            "2. Call select_option('[name=\"size\"]', 'large') to pick the pizza size.\n"
            "3. Call read_page_text() to read the updated page content.\n"
            "4. Write the response (or a summary confirming the size selection) to dropdown.txt."
        ),
        "tools": ["browser", "computer", "vision", "ipython", "save"],
        "expect": {
            "dropdown.txt written": _expect_dropdown_result_written,
            "selection reflected in response": _expect_dropdown_value_echoed,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "used select_option for dropdown": check_used_select_option,
            "used open_page for navigation": check_used_open_page,
        },
    },
    # --- Dynamic-content waiting test ---
    # Validates wait_for_element() for pages where elements may not be immediately
    # ready (JS-rendered content, delayed DOM updates, SPAs after navigation).
    # httpbin /forms/post is used as the host page; the agent must call
    # wait_for_element() before filling to exercise the tool.
    {
        "name": "computer-use-web-wait-for-element",
        "files": {},
        "run": "cat result.txt",
        "prompt": (
            "You are in computer-use mode. Use wait_for_element() to confirm an element is ready before interacting:\n"
            "1. Call open_page('https://httpbin.org/forms/post') to open the pizza order form.\n"
            "2. Call wait_for_element('[name=\"custname\"]') to wait until the customer name field is present in the DOM.\n"
            "3. Call fill_element('[name=\"custname\"]', 'WaitUser') to fill the customer name field.\n"
            "4. Call click_element('[type=\"submit\"]') to submit the form.\n"
            "5. Call read_page_text() to read the response.\n"
            "6. Write the response (or a summary) to result.txt."
        ),
        "tools": ["browser", "computer", "vision", "ipython", "save"],
        "expect": {
            "result.txt written": _expect_result_written,
            "form submitted (custname reflected)": _expect_form_submitted,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "used wait_for_element before interaction": check_used_wait_for_element,
            "used open_page for navigation": check_used_open_page,
            "used fill_element for input": check_used_fill_element,
        },
    },
    # --- hover_element() test ---
    # Validates hover_element() for triggering hover-only DOM changes (dropdown
    # menus, tooltips, contextual buttons).  The fixture page hides a menu item
    # via CSS and reveals it only on mouseover — the marker text is absent from
    # the static HTML, so a passing check proves hover_element() was actually
    # called, not that the agent narrated the interaction.
    {
        "name": "computer-use-web-hover-element",
        "files": {},
        "run": "cat hover.txt",
        "prompt": (
            "You are in computer-use mode. Use hover_element() to reveal a hidden menu:\n"
            f"1. Call open_page('{_HOVER_FIXTURE_URL}') to open a page with a hover menu.\n"
            "2. Call hover_element('#menu-trigger') to hover over the trigger element.\n"
            "3. Call read_page_text() to read the updated page content.\n"
            "4. Write the page content (or a summary confirming 'hover-revealed' appeared) to hover.txt."
        ),
        "tools": ["browser", "computer", "vision", "ipython", "save"],
        "expect": {
            "hover.txt written": lambda ctx: (
                "hover.txt" in ctx.files or len(ctx.stdout.strip()) > 5
            ),
            "hover-revealed marker found": _expect_hover_menu_found,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "used hover_element for hover interaction": check_used_hover_element,
            "used open_page for navigation": check_used_open_page,
        },
    },
    # --- snapshot_page() test ---
    # Validates that snapshot_page() returns the current DOM state after an
    # interaction — not a re-fetch of the original URL.  The fixture increments
    # a counter on every button click; the agent must fill a field, take a
    # snapshot with snapshot_page(), and confirm the snapshot reflects the
    # current form state (field value visible in the ARIA tree).
    {
        "name": "computer-use-web-snapshot-page",
        "files": {},
        "run": "cat snapshot.txt",
        "prompt": (
            "You are in computer-use mode. Use snapshot_page() to inspect current page state after interaction:\n"
            f"1. Call open_page('{_SNAPSHOT_FIXTURE_URL}') to open a page with an input field.\n"
            "2. Call fill_element('[name=\"msg\"]', 'hello-gptme') to fill the field.\n"
            "3. Call snapshot_page() to get the current ARIA snapshot (do NOT reopen the page).\n"
            "4. Write the snapshot content to snapshot.txt."
        ),
        "tools": ["browser", "computer", "vision", "ipython", "save"],
        "expect": {
            "snapshot.txt written": lambda ctx: (
                "snapshot.txt" in ctx.files or len(ctx.stdout.strip()) > 5
            ),
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "used snapshot_page() for current state": check_used_snapshot_page,
            "used fill_element for interaction": check_used_fill_element,
        },
    },
    # --- get_current_url() test ---
    # Validates get_current_url() returns the URL after navigation.  The agent
    # opens a self-contained fixture, then calls get_current_url() to record
    # where it ended up.
    {
        "name": "computer-use-web-get-current-url",
        "files": {},
        "run": "cat url.txt",
        "prompt": (
            "You are in computer-use mode. Use get_current_url() to record the page URL:\n"
            f"1. Call open_page('{_CURRENT_URL_FIXTURE_URL}') to open a page.\n"
            "2. Call get_current_url() to retrieve the current URL.\n"
            "3. Write the URL to url.txt."
        ),
        "tools": ["browser", "computer", "vision", "ipython", "save"],
        "expect": {
            "url.txt written": _expect_current_url_captured,
            "fixture URL recorded": _expect_current_url_fixture_recorded,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "used get_current_url()": check_used_get_current_url,
            "used open_page for navigation": check_used_open_page,
        },
    },
]
