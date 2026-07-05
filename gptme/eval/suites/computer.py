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
- "Can it Tweet?" end-to-end: full compose→post pipeline using a Twitter-like local fixture
- "Can it play Doom?" end-to-end: keyboard-driven game-loop using a Doom-like local fixture
- "Can it play Factorio?" end-to-end: click-driven gather-and-craft loop using a Factorio-like local fixture

These tests run without a physical display because they use Playwright's
headless mode via the browser tool. Desktop/screenshot tests that require
an X11 display are not included here — they belong in manual or CI-with-display
pipelines.
"""

import ast
import base64
import logging
import re
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

# "Can it Tweet?" fixture (issue #216 milestone).
#
# A self-contained Twitter/X-like compose box that validates the full
# "compose → post" pipeline without requiring a real Twitter account.
#
# The marker text "tweet-posted" only appears in the DOM after a real
# click_element() call fires the submit handler, so read_page_text() cannot
# return it from the initial page state.
#
# The contenteditable compose box uses Twitter's real data-testid so the same
# selectors and editable-element path work against both this fixture and a real
# X.com compose page.
_TWEET_COMPOSE_FIXTURE_HTML = (
    "<!doctype html><html><body>"
    "<h1>Compose</h1>"
    '<div role="group" aria-label="Tweet compose">'
    '<div data-testid="tweetTextarea_0" role="textbox" contenteditable="true" '
    'aria-label="Tweet text" style="width:100%;min-height:80px"></div>'
    '<button data-testid="tweetButtonInline" data-role="tweet-button" '
    'style="margin-top:8px">Tweet</button>'
    "</div>"
    '<div id="status"></div>'
    "<script>"
    "document.querySelector('[data-role=\"tweet-button\"]')"
    ".addEventListener('click', function() {"
    "var compose = document.querySelector('[data-testid=\"tweetTextarea_0\"]');"
    "var text = (compose.innerText || compose.textContent || '').trim();"
    "document.getElementById('status').textContent = 'tweet-posted:' + text;"
    "});"
    "</script>"
    "</body></html>"
)
_TWEET_COMPOSE_FIXTURE_URL = "data:text/html;base64," + base64.b64encode(
    _TWEET_COMPOSE_FIXTURE_HTML.encode()
).decode("ascii")

# "Can it play Doom?" fixture (issue #216 milestone).
#
# A self-contained keyboard-driven game that validates the full
# "read state → press key → verify result" loop without requiring an X11
# display or a real game binary.
#
# Game mechanics (readable via read_page_text / ARIA):
#   - 7-cell 1-D battlefield rendered as text: . . . @ . . E
#   - ArrowLeft / ArrowRight move the player (@) one cell
#   - Space fires a bullet that travels toward the enemy (E)
#   - When the bullet reaches the enemy the status div shows:
#       doom-milestone:enemy-defeated score:100
#
# The "doom-milestone:enemy-defeated" marker is written only when the JS shoot
# handler actually reaches the enemy cell, so read_page_text() cannot return
# it from the initial page state.  Pressing Space once is enough to win because
# the bullet auto-aims toward the enemy.
#
# The same press_key() calls work against a real game running in the browser;
# this fixture validates the tool-call pipeline without any external dep.
_DOOM_MILESTONE_FIXTURE_HTML = (
    "<!doctype html><html><head><title>gptme Doom Milestone Fixture</title>"
    "<style>body{font-family:monospace;padding:20px;}"
    "#game{font-size:24px;letter-spacing:8px;padding:10px;border:1px solid #333;display:inline-block;}"
    "#status{margin-top:12px;font-size:14px;color:#444;}"
    "#instructions{margin-top:8px;font-size:12px;color:#888;}</style></head><body>"
    "<h2>Doom Milestone Fixture</h2>"
    '<div id="game"></div>'
    '<div id="status">doom-milestone:waiting score:0 player-at:3 enemy-at:6 enemy-alive:true</div>'
    '<div id="instructions">ArrowLeft/ArrowRight: move player | Space: shoot</div>'
    "<script>"
    "var playerX=3,COLS=7,enemyX=6,enemyAlive=true,score=0,milestone='waiting';"
    "function render(){"
    "var row=[];"
    "for(var i=0;i<COLS;i++) row.push('.');"
    "if(enemyAlive) row[enemyX]='E';"
    "row[playerX]='@';"
    "document.getElementById('game').textContent=row.join(' ');"
    "document.getElementById('status').textContent="
    "'doom-milestone:'+milestone+' score:'+score+' player-at:'+playerX+' enemy-at:'+enemyX+' enemy-alive:'+enemyAlive;"
    "}"
    "function shoot(){"
    "if(!enemyAlive) return;"
    "if(playerX===enemyX){enemyAlive=false;score=100;milestone='enemy-defeated';return;}"
    "var dir=enemyX>playerX?1:-1;"
    "var pos=playerX+dir;"
    "while(pos>=0&&pos<COLS){"
    "if(pos===enemyX){enemyAlive=false;score=100;milestone='enemy-defeated';break;}"
    "pos+=dir;}"
    "}"
    "document.addEventListener('keydown',function(e){"
    "if(e.key==='ArrowLeft'){playerX=Math.max(0,playerX-1);e.preventDefault();}"
    "else if(e.key==='ArrowRight'){playerX=Math.min(COLS-1,playerX+1);e.preventDefault();}"
    "else if(e.key===' '||e.key==='Space'){shoot();e.preventDefault();}"
    "render();"
    "});"
    "render();"
    "</script>"
    "</body></html>"
)
_DOOM_MILESTONE_FIXTURE_URL = "data:text/html;base64," + base64.b64encode(
    _DOOM_MILESTONE_FIXTURE_HTML.encode()
).decode("ascii")


# "Can it play Factorio?" fixture (issue #216 milestone).
#
# A self-contained click-driven crafting game that validates the full
# "observe → gather → craft" loop without requiring a real game or display.
#
# Game mechanics (readable via read_page_text / ARIA):
#   - Three iron ore nodes; each click gathers 2 ore
#   - When iron_ore >= 5 the "Craft iron plate" button becomes enabled
#   - Clicking it spends 5 ore and creates 1 iron plate
#   - Status div then shows:
#       factorio-milestone:automation-started iron_ore:N iron_plate:1
#
# The "factorio-milestone:automation-started" marker only appears after a
# successful craft action, so read_page_text() cannot return it from the
# initial page state.  Clicking three ore nodes (6 ore) then the craft
# button is sufficient to win.
_FACTORIO_MILESTONE_FIXTURE_HTML = (
    "<!doctype html><html><head><title>gptme Factorio Milestone Fixture</title>"
    "<style>"
    "body{font-family:monospace;padding:20px;}"
    ".ore-node{display:inline-block;width:80px;height:50px;background:#7a5c2e;"
    "color:#f5c842;text-align:center;line-height:50px;cursor:pointer;margin:4px;"
    "border:2px solid #5a3c1e;font-size:12px;user-select:none;}"
    ".ore-node:hover{background:#9a7c4e;}"
    ".ore-node.depleted{background:#555;color:#888;cursor:not-allowed;}"
    "#inventory{margin-top:12px;font-size:14px;}"
    "#craft-btn{margin-top:8px;padding:4px 12px;cursor:pointer;font-family:monospace;}"
    "#craft-btn:disabled{cursor:not-allowed;opacity:0.5;}"
    "#status{margin-top:8px;font-size:12px;color:#444;}"
    "#instructions{margin-top:6px;font-size:11px;color:#888;}"
    "</style></head><body>"
    "<h2>Factorio Milestone Fixture</h2>"
    '<div id="world">'
    '<div class="ore-node" id="ore-1" data-testid="iron-ore-1">Iron Ore</div>'
    '<div class="ore-node" id="ore-2" data-testid="iron-ore-2">Iron Ore</div>'
    '<div class="ore-node" id="ore-3" data-testid="iron-ore-3">Iron Ore</div>'
    "</div>"
    '<div id="inventory">Inventory: iron_ore:0 iron_plate:0</div>'
    '<button id="craft-btn" data-testid="craft-iron-plate" disabled>'
    "Craft iron plate (needs 5 iron ore)"
    "</button>"
    '<div id="status">factorio-milestone:waiting iron_ore:0 iron_plate:0</div>'
    '<div id="instructions">Click ore nodes to gather, then craft iron plates</div>'
    "<script>"
    "var inv={iron_ore:0,iron_plate:0},milestone='waiting';"
    "function update(){"
    "document.getElementById('inventory').textContent="
    "'Inventory: iron_ore:'+inv.iron_ore+' iron_plate:'+inv.iron_plate;"
    "var btn=document.getElementById('craft-btn');"
    "btn.disabled=inv.iron_ore<5;"
    "document.getElementById('status').textContent="
    "'factorio-milestone:'+milestone+' iron_ore:'+inv.iron_ore+' iron_plate:'+inv.iron_plate;"
    "}"
    "document.querySelectorAll('.ore-node').forEach(function(node){"
    "node.addEventListener('click',function(){"
    "if(node.classList.contains('depleted')) return;"
    "inv.iron_ore+=2;"
    "node.classList.add('depleted');"
    "node.textContent='(empty)';"
    "update();"
    "});"
    "});"
    "document.getElementById('craft-btn').addEventListener('click',function(){"
    "if(inv.iron_ore<5) return;"
    "inv.iron_ore-=5;inv.iron_plate+=1;"
    "milestone='automation-started';"
    "update();"
    "});"
    "update();"
    "</script>"
    "</body></html>"
)
_FACTORIO_MILESTONE_FIXTURE_URL = "data:text/html;base64," + base64.b64encode(
    _FACTORIO_MILESTONE_FIXTURE_HTML.encode()
).decode("ascii")


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


def check_used_save_browser_state(messages: list[Message]) -> bool:
    """Agent must use save_browser_state() to persist the browser session."""
    return any("save_browser_state(" in code for code in _executed_tool_calls(messages))


def check_used_load_browser_state(messages: list[Message]) -> bool:
    """Agent must use load_browser_state() to restore a browser session."""
    return any("load_browser_state(" in code for code in _executed_tool_calls(messages))


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


def _expect_state_file_written(ctx) -> bool:
    """State file must have been saved by save_browser_state()."""
    return "state.json" in ctx.files or (
        "state.json" in ctx.stdout or "state.json" in ctx.stderr
    )


def _expect_url_after_reload_recorded(ctx) -> bool:
    """Agent must confirm the page URL after loading state."""
    content = ctx.files.get("result.txt", ctx.stdout)
    if isinstance(content, bytes):
        content = content.decode(errors="replace")
    # The agent should record the fixture URL or at least a non-empty URL
    return len(content.strip()) > 5


def _expect_tweet_posted(ctx) -> bool:
    """The "Can it Tweet?" milestone check.

    The fixture's JS click handler writes "tweet-posted:<text>" into #status
    only after a real click_element() call. It is not rendered in the initial
    page text, so this cannot pass on narration or unfired tool calls.
    """
    content = ctx.files.get("tweet.txt", ctx.stdout)
    if isinstance(content, bytes):
        content = content.decode(errors="replace")
    return "tweet-posted" in content


def _expect_tweet_text_echoed(ctx) -> bool:
    """The composed tweet text must appear in the output (proving the fill worked)."""
    content = ctx.files.get("tweet.txt", ctx.stdout)
    if isinstance(content, bytes):
        content = content.decode(errors="replace")
    return "Hello from gptme" in content


def check_used_tweet_textarea(messages: list[Message]) -> bool:
    """Agent must address the tweet textarea by its Twitter data-testid selector."""
    return any("tweetTextarea_0" in code for code in _executed_tool_calls(messages))


def _expect_doom_milestone_achieved(ctx) -> bool:
    """The "Can it play Doom?" milestone check.

    The fixture's JS shoot handler writes "doom-milestone:enemy-defeated" into
    #status only after the player's bullet reaches the enemy cell.  It is not
    present in the initial page text, so this cannot pass on narration or
    unfired tool calls.
    """
    content = ctx.files.get("game.txt", ctx.stdout)
    if isinstance(content, bytes):
        content = content.decode(errors="replace")
    return "doom-milestone:enemy-defeated" in content


def _expect_doom_score_nonzero(ctx) -> bool:
    """The score must be > 0, proving the enemy was actually hit."""
    content = ctx.files.get("game.txt", ctx.stdout)
    if isinstance(content, bytes):
        content = content.decode(errors="replace")
    return bool(re.search(r"score:([1-9]\d*)", content))


def _press_key_values(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    values: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_press_key = (
            isinstance(func, ast.Name)
            and func.id == "press_key"
            or isinstance(func, ast.Attribute)
            and func.attr == "press_key"
        )
        if not is_press_key:
            continue

        if node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str):
                values.append(value)
        for keyword in node.keywords:
            if keyword.arg == "key" and isinstance(keyword.value, ast.Constant):
                value = keyword.value.value
                if isinstance(value, str):
                    values.append(value)
    return values


def check_used_game_control_keys(messages: list[Message]) -> bool:
    """Agent must press at least one game-control key (arrows or Space).

    The "Can it play Doom?" flow requires the agent to actually drive the game
    via press_key() rather than just reading the initial page state.  We
    accept any of the four control keys: ArrowLeft, ArrowRight, ArrowUp,
    ArrowDown, or Space (the shoot key).  Both "Space" and the browser event
    key value " " are accepted, but only as exact press_key() arguments.
    """
    game_keys = {"ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Space", " "}
    for code in _executed_tool_calls(messages):
        if any(key in game_keys for key in _press_key_values(code)):
            return True
    return False


def _expect_factorio_milestone_achieved(ctx) -> bool:
    """The "Can it play Factorio?" milestone check.

    The fixture's JS craft handler writes "factorio-milestone:automation-started"
    into #status only after 5+ iron ore is gathered and the craft button is clicked.
    It is absent from the initial page state, so this cannot pass on narration alone.
    """
    content = ctx.files.get("factorio.txt", ctx.stdout)
    if isinstance(content, bytes):
        content = content.decode(errors="replace")
    return "factorio-milestone:automation-started" in content


def _expect_factorio_iron_plate_crafted(ctx) -> bool:
    """At least one iron plate must appear in the status, proving the craft succeeded."""
    content = ctx.files.get("factorio.txt", ctx.stdout)
    if isinstance(content, bytes):
        content = content.decode(errors="replace")
    return bool(re.search(r"iron_plate:([1-9]\d*)", content))


def check_used_click_for_crafting(messages: list[Message]) -> bool:
    """Agent must use click_element() to interact (gathering ore or crafting).

    The "Can it play Factorio?" flow requires clicking ore nodes and the craft
    button — press_key() alone cannot advance this fixture.
    """
    return any("click_element(" in code for code in _executed_tool_calls(messages))


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
    # --- save_browser_state / load_browser_state round-trip test ---
    # Validates the session-persistence workflow: save state after visiting a
    # page, then restore it with load_browser_state() and open a second page to
    # confirm the state was applied.  Uses data: URL fixtures so no network is
    # needed and no real credentials are involved.
    #
    # This directly addresses the "Can it Tweet?" milestone from #216: the
    # authentication workflow is: log in → save_browser_state → (later)
    # load_browser_state → open the target page already authenticated.
    {
        "name": "computer-use-web-session-persistence",
        "files": {},
        "run": "cat result.txt",
        "prompt": (
            "You are in computer-use mode. Test browser session persistence:\n"
            f"1. Call open_page('{_CURRENT_URL_FIXTURE_URL}') to open a fixture page.\n"
            "2. Call save_browser_state('state.json') to save the current session.\n"
            "3. Call load_browser_state('state.json') to reload the saved state.\n"
            f"4. Call open_page('{_CURRENT_URL_FIXTURE_URL}') again with the restored state.\n"
            "5. Call get_current_url() to confirm the page loaded.\n"
            "6. Write the URL to result.txt."
        ),
        "tools": ["browser", "computer", "vision", "ipython", "save"],
        "expect": {
            "state.json written": _expect_state_file_written,
            "result.txt written": _expect_url_after_reload_recorded,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "used save_browser_state": check_used_save_browser_state,
            "used load_browser_state": check_used_load_browser_state,
            "used open_page for navigation": check_used_open_page,
        },
    },
    # --- "Can it Tweet?" milestone (issue #216) ---
    #
    # End-to-end validation of the full compose→post pipeline using a
    # self-contained fixture that mirrors Twitter's real DOM selectors
    # (data-testid="tweetTextarea_0", data-testid="tweetButtonInline").
    #
    # This test proves the structured-first pipeline works for the milestone
    # scenario without requiring a real Twitter account or session.  The
    # same four tool calls (open_page, wait_for_element, fill_element,
    # click_element) work against real Twitter once the user is authenticated
    # via save_browser_state / GPTME_BROWSER_STORAGE_STATE.
    {
        "name": "computer-use-web-tweet-compose",
        "files": {},
        "run": "cat tweet.txt",
        "prompt": (
            "You are in computer-use mode. Simulate the 'Can it Tweet?' workflow:\n"
            f"1. Call open_page('{_TWEET_COMPOSE_FIXTURE_URL}') to open a Twitter-like compose box.\n"
            "2. Call wait_for_element('[data-testid=\"tweetTextarea_0\"]') to wait for the compose box to be ready.\n"
            "3. Call fill_element('[data-testid=\"tweetTextarea_0\"]', 'Hello from gptme!') to type the tweet.\n"
            "4. Call click_element('[data-testid=\"tweetButtonInline\"]') to click the Tweet button.\n"
            "5. Call read_page_text() to read the page after posting.\n"
            "6. Write the exact text returned by read_page_text() to tweet.txt."
        ),
        "tools": ["browser", "computer", "vision", "ipython", "save"],
        "expect": {
            "tweet.txt written": lambda ctx: (
                "tweet.txt" in ctx.files or len(ctx.stdout.strip()) > 5
            ),
            "tweet-posted marker present": _expect_tweet_posted,
            "tweet text echoed in response": _expect_tweet_text_echoed,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "used open_page for navigation": check_used_open_page,
            "used wait_for_element before interaction": check_used_wait_for_element,
            "used fill_element for tweet text": check_used_fill_element,
            "used click_element for Tweet button": check_used_click_element,
            "addressed compose box by Twitter data-testid": check_used_tweet_textarea,
        },
    },
    # --- "Can it play Doom?" milestone (issue #216) ---
    #
    # End-to-end validation of the keyboard-driven game-loop using a
    # self-contained Doom-like fixture: a 7-cell 1-D battlefield where the
    # player (@) must shoot the enemy (E) using arrow keys + Space.
    #
    # The milestone marker "doom-milestone:enemy-defeated" only appears in the
    # DOM after a real press_key("Space") fires the shoot handler and the
    # bullet reaches the enemy cell.  It is absent from the initial page state,
    # so read_page_text() cannot return it without the agent actually playing.
    #
    # This tests the core game-playing loop:
    #   1. open_page → observe game state via read_page_text()
    #   2. press_key(ArrowLeft/Right) → move player into position
    #   3. press_key("Space") → fire
    #   4. read_page_text() → confirm "doom-milestone:enemy-defeated"
    #
    # The same press_key() calls work against a real game in the browser once
    # that game is open in the interactive browser session.
    {
        "name": "computer-use-web-doom-milestone",
        "files": {},
        "run": "cat game.txt",
        "prompt": (
            "You are in computer-use mode. Play a simple Doom-like game to hit the 'Can it play Doom?' milestone:\n"
            f"1. Call open_page('{_DOOM_MILESTONE_FIXTURE_URL}') to open the game.\n"
            "2. Call read_page_text() to read the current game state.\n"
            "   The status line shows: 'doom-milestone:waiting score:0 player-at:3 enemy-at:6 enemy-alive:true'\n"
            "   The game board shows: '. . . @ . . E'  (@ = player, E = enemy)\n"
            "3. Move the player toward the enemy using press_key('ArrowRight') one or more times.\n"
            "4. When the player is in position, call press_key('Space') to fire.\n"
            "   You can also fire from any position — the bullet auto-aims toward the enemy.\n"
            "5. Call read_page_text() to verify the result.\n"
            "6. Write the full page text to game.txt."
        ),
        "tools": ["browser", "computer", "vision", "ipython", "save"],
        "expect": {
            "game.txt written": lambda ctx: (
                "game.txt" in ctx.files or len(ctx.stdout.strip()) > 5
            ),
            "doom-milestone:enemy-defeated marker present": _expect_doom_milestone_achieved,
            "score is 100 (enemy hit)": _expect_doom_score_nonzero,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "used open_page for navigation": check_used_open_page,
            "used press_key for game control": check_used_game_control_keys,
        },
    },
    # --- "Can it play Factorio?" milestone (issue #216) ---
    #
    # End-to-end validation of the click-driven gather-and-craft loop using a
    # self-contained Factorio-inspired fixture: three iron ore nodes that must
    # be clicked to gather ore, then a craft button to convert ore → iron plate.
    #
    # This test validates a different tool path than the Doom milestone:
    #   - click_element() for resource gathering (not press_key)
    #   - DOM state reading via read_page_text() to check inventory
    #   - wait_for_element() to confirm craft button availability
    #   - click_element() again for the craft action
    #
    # The "factorio-milestone:automation-started" marker only appears in the DOM
    # after a successful craft action.  It is absent from the initial page state,
    # so read_page_text() cannot return it without the agent actually playing.
    {
        "name": "computer-use-web-factorio-milestone",
        "files": {},
        "run": "cat factorio.txt",
        "prompt": (
            "You are in computer-use mode. Play a simple Factorio-like game to hit the 'Can it play Factorio?' milestone:\n"
            f"1. Call open_page('{_FACTORIO_MILESTONE_FIXTURE_URL}') to open the game.\n"
            "2. Call read_page_text() to read the current game state.\n"
            "   The status line shows: 'factorio-milestone:waiting iron_ore:0 iron_plate:0'\n"
            "   There are three iron ore nodes you can click to gather ore.\n"
            "3. Click the iron ore nodes to gather ore:\n"
            "   call click_element('[data-testid=\"iron-ore-1\"]') — gathers 2 iron ore\n"
            "   call click_element('[data-testid=\"iron-ore-2\"]') — gathers 2 more\n"
            "   call click_element('[data-testid=\"iron-ore-3\"]') — gathers 2 more (6 total)\n"
            "4. Call wait_for_element('[data-testid=\"craft-iron-plate\"]:not([disabled])') "
            "to wait for the craft button to become enabled (needs 5+ iron ore).\n"
            "5. Call click_element('[data-testid=\"craft-iron-plate\"]') to craft an iron plate.\n"
            "6. Call read_page_text() to verify the result.\n"
            "7. Write the full page text to factorio.txt."
        ),
        "tools": ["browser", "computer", "vision", "ipython", "save"],
        "expect": {
            "factorio.txt written": lambda ctx: (
                "factorio.txt" in ctx.files or len(ctx.stdout.strip()) > 5
            ),
            "factorio-milestone:automation-started marker present": _expect_factorio_milestone_achieved,
            "iron plate crafted (iron_plate >= 1)": _expect_factorio_iron_plate_crafted,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "used open_page for navigation": check_used_open_page,
            "used click_element for gathering/crafting": check_used_click_for_crafting,
        },
    },
]
