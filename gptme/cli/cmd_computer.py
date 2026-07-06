"""CLI commands for computer-use tooling (audit-log, screenshot, etc.)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

import click

# Optional Playwright import — present only when the browser extra is installed.
# Defined at module level so tests can patch `gptme.cli.cmd_computer.sync_playwright`.
try:
    from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    sync_playwright = None  # type: ignore[assignment]

from ..dirs import get_logs_dir
from ..logmanager import _gen_read_jsonl
from ..tools._computer_gate import (
    ACTION_RISK_READ,
    ACTION_RISK_SENSITIVE,
    ACTION_RISK_WRITE,
    action_risk_level,
)
from ..tools.base import ToolUse

# Patterns that indicate text/key content (redact for privacy)
_SENSITIVE_ACTIONS = frozenset({"type", "key"})

# Browser interaction functions whose first arg is a URL
_URL_BROWSER_FNS = frozenset({"observe_web", "snapshot_url", "open_page"})

# Browser interaction functions whose first arg is a CSS/DOM selector
_SELECTOR_BROWSER_FNS = frozenset(
    {
        "click_element",
        "hover_element",  # added PR #3104
        "wait_for_element",  # added PR #3095
    }
)

# Browser functions with no arguments (observation only)
_NO_ARG_BROWSER_FNS = frozenset(
    {
        "read_page_text",
        "snapshot_page",  # added PR #3104
        "get_current_url",  # added PR #3104
    }
)

# ACTION_RISK_* and action_risk_level are imported from _computer_gate
# (re-exported here for backward compatibility with any existing callers)
__all__ = [
    "ACTION_RISK_READ",
    "ACTION_RISK_WRITE",
    "ACTION_RISK_SENSITIVE",
    "action_risk_level",
]


def _slice_call(code: str, start: int) -> str:
    """Return the source span for a function call starting at ``start``."""
    depth = 0
    quote: str | None = None
    escaped = False

    for i, ch in enumerate(code[start:], start=start):
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue

        if ch in {"'", '"'}:
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return code[start : i + 1]

    return code[start:]


def _extract_computer_calls(messages) -> list[dict]:
    """Extract computer-use actions from a message list.

    Scans executable tool-use blocks (ipython codeblocks) for calls to:
    - ``computer()`` — desktop/X11 actions (screenshot, click, type, key, …)
    - ``act_and_observe()`` — "act then look" wrapper (recommended by computer-use profile)
    - ``observe_desktop()`` — explicit desktop observation
    - ``observe_web(url)`` — structured-first web observation
    - ``snapshot_url(url)`` — one-shot ARIA snapshot
    - ``open_page(url)`` — open an interactive browser session
    - ``click_element(selector)`` — DOM element click
    - ``hover_element(selector)`` — hover over a DOM element
    - ``fill_element(selector, value)`` — form fill (value length logged, not raw text)
    - ``press_key(key)`` — navigation key press (Enter, Tab, Escape, …)
    - ``select_option(selector, value)`` — dropdown/select element change
    - ``wait_for_element(selector)`` — wait for a DOM element to appear
    - ``read_page_text()`` — read page text content
    - ``snapshot_page()`` — take current-page ARIA snapshot
    - ``get_current_url()`` — get the current page URL
    - ``load_browser_state(path)`` — restore a saved browser session
    - ``scroll_page(direction)`` — scroll the current page

    Typed/key text and fill_element values are never logged raw — only their
    length is recorded to avoid leaking passwords or personally identifiable data.
    """
    records: list[dict] = []
    for msg in messages:
        if msg.role != "assistant":
            continue
        for tu in ToolUse.iter_from_content(msg.content):
            if not tu.is_runnable or not tu.content:
                continue
            code = tu.content
            ts = msg.timestamp.isoformat() if msg.timestamp else None

            # All calls tracked with their byte-offset so desktop and browser
            # calls within the same block are emitted in source order.
            all_positioned: list[tuple[int, dict]] = []

            # --- computer("action", ...) ---
            for m in re.finditer(r"""computer\s*\(\s*['"]([^'"]+)['"]""", code):
                action = m.group(1)
                call_source = _slice_call(code, m.start())
                record: dict = {
                    "timestamp": ts,
                    "action": action,
                    "risk_level": action_risk_level(action),
                }
                coord_m = re.search(
                    r"coordinate\s*=\s*\((\d+)\s*,\s*(\d+)\)", call_source
                )
                if coord_m:
                    record["coordinate"] = [
                        int(coord_m.group(1)),
                        int(coord_m.group(2)),
                    ]
                if action in _SENSITIVE_ACTIONS:
                    text_m = re.search(r"""text\s*=\s*['"]([^'"]*)['"]""", call_source)
                    record["text_len"] = len(text_m.group(1)) if text_m else None
                all_positioned.append((m.start(), record))

            # --- act_and_observe("action", ...) ---
            # The computer-use profile's system prompt recommends act_and_observe() as
            # the primary "act then look" primitive. Without this branch those calls
            # would vanish from the audit trail even though they trigger real actions.
            for m in re.finditer(r"""act_and_observe\s*\(\s*['"]([^'"]+)['"]""", code):
                aao_action = m.group(1)
                aao_call_source = _slice_call(code, m.start())
                aao_record: dict = {
                    "timestamp": ts,
                    "action": aao_action,
                    "source": "act_and_observe",
                    "risk_level": action_risk_level(aao_action),
                }
                aao_coord_m = re.search(
                    r"coordinate\s*=\s*\((\d+)\s*,\s*(\d+)\)", aao_call_source
                )
                if aao_coord_m:
                    aao_record["coordinate"] = [
                        int(aao_coord_m.group(1)),
                        int(aao_coord_m.group(2)),
                    ]
                if aao_action in _SENSITIVE_ACTIONS:
                    aao_text_m = re.search(
                        r"""text\s*=\s*['"]([^'"]*)['"]""", aao_call_source
                    )
                    aao_record["text_len"] = (
                        len(aao_text_m.group(1)) if aao_text_m else None
                    )
                all_positioned.append((m.start(), aao_record))

            # --- observe_desktop() ---
            all_positioned.extend(
                (
                    m.start(),
                    {
                        "timestamp": ts,
                        "action": "screenshot",
                        "source": "observe_desktop",
                        "risk_level": action_risk_level("observe_desktop"),
                    },
                )
                for m in re.finditer(r"\bobserve_desktop\s*\(", code)
            )

            # --- browser interaction calls ---
            # Collected with their byte-offset in the code block so they can be
            # sorted into code order before appending (multiple passes would
            # otherwise interleave URL-fns, selector-fns, fill-fns, etc.).
            browser_positioned: list[tuple[int, dict]] = []

            # Functions whose first arg is a URL (no mixed-quote risk)
            for fn in _URL_BROWSER_FNS:
                browser_positioned.extend(
                    (
                        m.start(),
                        {
                            "timestamp": ts,
                            "action": fn,
                            "source": "browser",
                            "url": m.group(1) or m.group(2),
                            "risk_level": action_risk_level(fn),
                        },
                    )
                    for m in re.finditer(
                        rf"""\b{fn}\s*\(\s*(?:'([^']+)'|"([^"]+)")""", code
                    )
                )

            # click_element(selector) — selectors may contain the opposite quote
            # type (e.g. '[name="q"]'), so match each quote style separately.
            for fn in _SELECTOR_BROWSER_FNS:
                browser_positioned.extend(
                    (
                        m.start(),
                        {
                            "timestamp": ts,
                            "action": fn,
                            "source": "browser",
                            "selector": m.group(1)
                            if m.group(1) is not None
                            else m.group(2),
                            "risk_level": action_risk_level(fn),
                        },
                    )
                    for m in re.finditer(
                        rf"""\b{fn}\s*\(\s*(?:'([^']*)'|"([^"]*)")""", code
                    )
                )

            # fill_element(selector, value) — value is potentially sensitive;
            # log only its length. Selector may contain opposite-type quotes.
            browser_positioned.extend(
                (
                    m.start(),
                    {
                        "timestamp": ts,
                        "action": "fill_element",
                        "source": "browser",
                        "selector": m.group(1)
                        if m.group(1) is not None
                        else m.group(2),
                        "value_len": len(
                            m.group(3) if m.group(3) is not None else (m.group(4) or "")
                        ),
                        "risk_level": action_risk_level("fill_element"),
                    },
                )
                for m in re.finditer(
                    r"""\bfill_element\s*\(\s*(?:'([^']*)'|"([^"]*)")\s*,\s*(?:'([^']*)'|"([^"]*)")""",
                    code,
                )
            )

            # No-argument browser observation functions
            # (read_page_text, snapshot_page, get_current_url)
            for fn in _NO_ARG_BROWSER_FNS:
                browser_positioned.extend(
                    (
                        m.start(),
                        {
                            "timestamp": ts,
                            "action": fn,
                            "source": "browser",
                            "risk_level": action_risk_level(fn),
                        },
                    )
                    for m in re.finditer(rf"\b{fn}\s*\(", code)
                )

            # scroll_page(direction)
            browser_positioned.extend(
                (
                    m.start(),
                    {
                        "timestamp": ts,
                        "action": "scroll_page",
                        "source": "browser",
                        "direction": m.group(1),
                        "risk_level": action_risk_level("scroll_page"),
                    },
                )
                for m in re.finditer(r"""\bscroll_page\s*\(\s*['"]([^'"]+)['"]""", code)
            )

            # press_key(key) — navigation key presses (Enter, Tab, Escape, …)
            browser_positioned.extend(
                (
                    m.start(),
                    {
                        "timestamp": ts,
                        "action": "press_key",
                        "source": "browser",
                        "key": m.group(1) if m.group(1) is not None else m.group(2),
                        "risk_level": action_risk_level("press_key"),
                    },
                )
                for m in re.finditer(
                    r"""\bpress_key\s*\(\s*(?:'([^']*)'|"([^"]*)")""", code
                )
            )

            # select_option(selector, value) — dropdown selection; value not sensitive
            browser_positioned.extend(
                (
                    m.start(),
                    {
                        "timestamp": ts,
                        "action": "select_option",
                        "source": "browser",
                        "selector": m.group(1)
                        if m.group(1) is not None
                        else m.group(2),
                        "value": m.group(3)
                        if m.group(3) is not None
                        else (m.group(4) or ""),
                        "risk_level": action_risk_level("select_option"),
                    },
                )
                for m in re.finditer(
                    r"""\bselect_option\s*\(\s*(?:'([^']*)'|"([^"]*)")\s*,\s*(?:'([^']*)'|"([^"]*)")""",
                    code,
                )
            )

            # load_browser_state(path) — restores a saved browser session
            browser_positioned.extend(
                (
                    m.start(),
                    {
                        "timestamp": ts,
                        "action": "load_browser_state",
                        "source": "browser",
                        "risk_level": action_risk_level("load_browser_state"),
                    },
                )
                for m in re.finditer(r"\bload_browser_state\s*\(", code)
            )

            # Merge desktop and browser records, emit in source order
            records.extend(
                r
                for _, r in sorted(
                    all_positioned + browser_positioned, key=lambda x: x[0]
                )
            )

    return records


@click.group()
def computer():
    """Computer-use tooling: audit, diagnostics."""


@computer.command("audit-log")
@click.argument("conversation", required=False)
@click.option(
    "--last",
    default=1,
    show_default=True,
    help="Number of most-recent conversations to scan (ignored when CONVERSATION is given).",
)
@click.option(
    "--json", "as_json", is_flag=True, help="Output raw JSON array instead of table."
)
@click.option(
    "--jsonl",
    "as_jsonl",
    is_flag=True,
    help="Output newline-delimited JSON (one record per line). Useful for streaming to log aggregators.",
)
@click.option(
    "--agent-id",
    "agent_id",
    default=None,
    help=(
        "computer_task() agent ID to audit (e.g. 'computer-task-abc12345'). "
        "Automatically resolves the subagent conversation name. "
        "Use the 'agent_id' key from the computer_task() result dict."
    ),
)
def audit_log(
    conversation: str | None,
    last: int,
    as_json: bool,
    as_jsonl: bool,
    agent_id: str | None,
):
    """Extract computer-use actions from session trajectories.

    Reads conversation JSONL logs (the authoritative audit trail) and prints a
    structured summary of every computer(), act_and_observe(), observe_desktop(),
    and browser interaction call (observe_web, open_page, fill_element,
    click_element, …). Typed/key text and fill_element values are redacted to
    just their length.

    CONVERSATION is a conversation name or ID. Omit to scan the most-recent
    session(s) (controlled by --last).

    Use --agent-id to audit a specific computer_task() run by its agent ID.
    The agent ID comes from the 'agent_id' key in the dict returned by computer_task().

    Examples:

    \b
        gptme-util computer audit-log
        gptme-util computer audit-log --last 3
        gptme-util computer audit-log my-session-name --json
        gptme-util computer audit-log my-session-name --jsonl
        gptme-util computer audit-log --jsonl | jq 'select(.risk_level == "sensitive")'
        gptme-util computer audit-log --agent-id computer-task-abc12345
    """
    logs_dir = get_logs_dir()

    if agent_id is not None and conversation:
        click.echo(
            "Error: --agent-id and CONVERSATION are mutually exclusive.",
            err=True,
        )
        sys.exit(1)

    # --agent-id is a shortcut: computer_task() returns agent_id like
    # "computer-task-abc123", but thread-mode subagents store conversations as
    # "subagent-computer-task-abc123-r4nd". Resolve automatically.
    if agent_id is not None:
        subagent_conv = f"subagent-{agent_id}"
        candidates = [
            path
            for path in [
                logs_dir / subagent_conv / "conversation.jsonl",
                logs_dir / agent_id / "conversation.jsonl",
            ]
            if path.exists()
        ]
        candidates.extend(
            sorted(logs_dir.glob(f"{subagent_conv}-*/conversation.jsonl"))
        )
        if not candidates:
            click.echo(
                f"Error: no conversation found for agent-id '{agent_id}'.\n"
                f"Looked for: {logs_dir / subagent_conv}, "
                f"{logs_dir / (subagent_conv + '-*')}, and {logs_dir / agent_id}",
                err=True,
            )
            sys.exit(1)
        if len(candidates) > 1:
            click.echo(
                f"Error: multiple conversations found for agent-id '{agent_id}'.\n"
                "Use the exact CONVERSATION name instead:\n"
                + "\n".join(f"  {path.parent.name}" for path in candidates),
                err=True,
            )
            sys.exit(1)
        conv_path = candidates[0]
        paths = [conv_path]
    elif conversation:
        # Single named conversation
        conv_path = logs_dir / conversation / "conversation.jsonl"
        if not conv_path.exists():
            # Try treating it as a direct path
            conv_path = Path(conversation)
        if not conv_path.exists():
            click.echo(f"Error: conversation not found: {conversation}", err=True)
            sys.exit(1)
        paths = [conv_path]
    else:
        # Most-recent N conversations
        if not logs_dir.exists():
            click.echo("No conversations found.", err=True)
            sys.exit(0)
        conv_dirs = sorted(
            (d for d in logs_dir.iterdir() if (d / "conversation.jsonl").exists()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )[:last]
        paths = [d / "conversation.jsonl" for d in conv_dirs]
        if not paths:
            click.echo("No conversations found.", err=True)
            sys.exit(0)

    all_records: list[dict] = []
    for path in paths:
        try:
            msgs = list(_gen_read_jsonl(path))
        except Exception as e:
            click.echo(f"Warning: could not read {path}: {e}", err=True)
            continue
        records = _extract_computer_calls(msgs)
        for r in records:
            r["conversation"] = path.parent.name
        all_records.extend(records)

    if as_json and as_jsonl:
        click.echo("Error: --json and --jsonl are mutually exclusive.", err=True)
        sys.exit(1)

    if not all_records:
        click.echo("No computer-use actions found.")
        return

    if as_json:
        click.echo(json.dumps(all_records, indent=2))
        return

    if as_jsonl:
        for record in all_records:
            click.echo(json.dumps(record, separators=(",", ":")))
        return

    # Human-readable table
    click.echo(f"{'Timestamp':<30} {'Conv':<25} {'Risk':<10} {'Action':<25} Details")
    click.echo("-" * 115)
    for r in all_records:
        ts = (r.get("timestamp") or "")[:19]
        conv = (r.get("conversation") or "")[:24]
        action = r.get("action", "")[:24]
        risk = r.get("risk_level", "write")[:9]
        details = ""
        source = r.get("source", "")
        if source == "observe_desktop":
            details = "via observe_desktop()"
        elif source == "act_and_observe":
            details = "via act_and_observe()"
            if "coordinate" in r:
                details += f" @ {r['coordinate']}"
            if "text_len" in r and r["text_len"] is not None:
                details += f" ({r['text_len']} chars, redacted)"
        elif source == "browser":
            if "url" in r:
                url = r["url"]
                details = url[:70] + ("…" if len(url) > 70 else "")
            elif "key" in r:
                # press_key(key) — show which key was pressed
                details = repr(r["key"])
            elif "selector" in r and "value" in r:
                # select_option(selector, value) — show both selector and value
                details = f"{r['selector']!r} → {r['value']!r}"
            elif "selector" in r and "value_len" in r:
                details = f"{r['selector']!r} → {r['value_len']} chars"
            elif "selector" in r:
                details = repr(r["selector"])
            elif "direction" in r:
                details = r["direction"]
        else:
            if "coordinate" in r:
                details = f"@ {r['coordinate']}"
            if "text_len" in r and r["text_len"] is not None:
                details += f" ({r['text_len']} chars, redacted)"
        click.echo(f"{ts:<30} {conv:<25} {risk:<10} {action:<25} {details}")


@computer.command("screenshot")
@click.option(
    "--output",
    "-o",
    default=None,
    metavar="PATH",
    help="Save screenshot to PATH (PNG). Defaults to /tmp/gptme-screenshot.png.",
)
@click.option(
    "--display",
    default=None,
    metavar="DISPLAY",
    help="X11 display to capture (e.g. ':1'). Defaults to $DISPLAY or ':1'. Linux only.",
)
def screenshot_cmd(output: str | None, display: str | None):
    """Take a screenshot of the current display.

    Verifies that the computer tool's screenshot action works in the current
    environment (X11 display reachable, scrot installed, etc.).  Useful for
    checking the setup before starting a full computer-use session.

    The screenshot is saved as a PNG file.  When --output is omitted it goes to
    /tmp/gptme-screenshot.png.

    Examples:

    \b
        gptme-util computer screenshot
        gptme-util computer screenshot --output /tmp/my-screen.png
        gptme-util computer screenshot --display :1
    """
    import platform
    import shutil
    import subprocess

    out_path = Path(output) if output else Path("/tmp/gptme-screenshot.png")

    system = platform.system()

    if system == "Darwin":
        # macOS: use screencapture
        try:
            subprocess.run(
                ["screencapture", "-x", str(out_path)],
                check=True,
                capture_output=True,
                timeout=10,
            )
        except FileNotFoundError:
            click.echo("Error: screencapture not found (expected on macOS).", err=True)
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            click.echo(f"Error: screencapture failed: {e.stderr.decode()}", err=True)
            sys.exit(1)
        except subprocess.TimeoutExpired:
            click.echo("Error: screencapture timed out.", err=True)
            sys.exit(1)
    else:
        # Linux: use scrot via $DISPLAY
        scrot = shutil.which("scrot")
        if not scrot:
            click.echo(
                "Error: scrot not found. Install it with:\n"
                "  sudo apt install scrot  # Debian/Ubuntu\n"
                "  sudo pacman -S scrot    # Arch",
                err=True,
            )
            sys.exit(1)

        effective_display: str = display or os.environ.get("DISPLAY") or ":1"
        env = os.environ.copy()
        env["DISPLAY"] = effective_display

        # scrot will not overwrite an existing file — remove the target first so
        # the output path is vacant, then capture directly to it.
        try:
            out_path.unlink(missing_ok=True)
        except OSError as e:
            click.echo(
                f"Error: cannot remove existing screenshot file at {out_path}: {e}",
                err=True,
            )
            sys.exit(1)
        try:
            subprocess.run(
                ["scrot", str(out_path)],
                check=True,
                capture_output=True,
                env=env,
                timeout=10,
            )
        except subprocess.CalledProcessError as e:
            out_path.unlink(missing_ok=True)
            stderr = e.stderr.decode().strip()
            if "open" in stderr.lower() and "display" in stderr.lower():
                click.echo(
                    f"Error: cannot open display {effective_display!r}.\n"
                    "Start Xvfb first:\n"
                    f"  Xvfb {effective_display} -screen 0 1024x768x24 &\n"
                    f"  export DISPLAY={effective_display}",
                    err=True,
                )
            else:
                click.echo(f"Error: scrot failed: {stderr}", err=True)
            sys.exit(1)
        except subprocess.TimeoutExpired:
            out_path.unlink(missing_ok=True)
            click.echo("Error: scrot timed out.", err=True)
            sys.exit(1)

    try:
        size = out_path.stat().st_size
    except FileNotFoundError:
        hint = (
            "\nOn macOS, check that Screen Recording permission is granted in "
            "System Settings > Privacy & Security > Screen Recording."
            if system == "Darwin"
            else ""
        )
        click.echo(
            "Error: screenshot file was not created at "
            f"{out_path} despite successful subprocess run.{hint}",
            err=True,
        )
        sys.exit(1)

    if size == 0:
        hint = (
            "\nOn macOS, check that Screen Recording permission is granted in "
            "System Settings > Privacy & Security > Screen Recording."
            if system == "Darwin"
            else ""
        )
        click.echo(
            f"Error: screenshot file at {out_path} is empty (0 bytes).{hint}",
            err=True,
        )
        sys.exit(1)

    click.echo(f"Screenshot saved to {out_path} ({size:,} bytes)")


@computer.command("video-frames")
@click.argument("input", metavar="INPUT")
@click.option(
    "--fps",
    default=1.0,
    show_default=True,
    type=float,
    help="Frames per second to extract.",
)
@click.option(
    "--limit",
    default=None,
    type=int,
    metavar="N",
    help="Maximum number of frames to extract.",
)
@click.option(
    "--output-dir",
    "-o",
    default=None,
    metavar="DIR",
    help="Directory for output frames. Defaults to a system temporary directory.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output JSON with frame paths and metadata instead of one path per line.",
)
def video_frames_cmd(
    input: str,
    fps: float,
    limit: int | None,
    output_dir: str | None,
    as_json: bool,
):
    """Extract key frames from a video for use as gptme context.

    Uses ffmpeg to extract frames at the specified rate and prints the resulting
    PNG file paths.  Useful for reviewing screen recordings of CI failures, UI
    bugs, or multi-step workflows without manually scrubbing the video.

    INPUT is the path to the video file (MP4, MKV, WebM, etc.).

    Examples:

    \b
        gptme-util computer video-frames recording.mp4
        gptme-util computer video-frames recording.mp4 --fps 0.5 --limit 5
        gptme-util computer video-frames recording.mp4 --output-dir /tmp/frames --json
    """
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("ffmpeg"):
        click.echo(
            "Error: ffmpeg not found. Install it with:\n"
            "  sudo apt install ffmpeg  # Debian/Ubuntu\n"
            "  brew install ffmpeg      # macOS",
            err=True,
        )
        sys.exit(1)

    in_path = Path(input)
    if not in_path.exists():
        click.echo(f"Error: input file not found: {in_path}", err=True)
        sys.exit(1)

    if fps <= 0:
        click.echo("Error: --fps must be a positive number.", err=True)
        sys.exit(1)

    if fps > 60:
        click.echo(
            "Error: --fps must be at most 60. "
            "Higher rates risk extracting thousands of frames and filling disk.",
            err=True,
        )
        sys.exit(1)

    if limit is not None and limit <= 0:
        click.echo("Error: --limit must be a positive integer.", err=True)
        sys.exit(1)

    if output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        # Remove stale frames so a reused --output-dir never leaks old files into
        # the glob result (temp dirs from the else branch are always fresh).
        for _stale in out_dir.glob("frame_*.png"):
            _stale.unlink()
    else:
        out_dir = Path(tempfile.mkdtemp(prefix="gptme-video-frames-"))
        click.echo(
            f"Output directory: {out_dir} (temp directory, not auto-cleaned)",
            err=True,
        )

    out_pattern = str(out_dir / "frame_%04d.png")
    cmd = ["ffmpeg", "-i", str(in_path), "-vf", f"fps={fps}"]
    if limit is not None:
        cmd += ["-frames:v", str(limit)]
    cmd += ["-y", out_pattern]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except subprocess.CalledProcessError as e:
        click.echo(f"Error: ffmpeg failed:\n{e.stderr.decode()}", err=True)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        click.echo("Error: ffmpeg timed out.", err=True)
        sys.exit(1)

    frames = sorted(out_dir.glob("frame_*.png"))
    if not frames:
        click.echo(
            f"Error: no frames were extracted from {in_path}.",
            err=True,
        )
        sys.exit(1)

    if as_json:
        click.echo(
            json.dumps(
                {"frames": [str(f) for f in frames], "count": len(frames), "fps": fps}
            )
        )
    else:
        for f in frames:
            click.echo(str(f))


@computer.command("run-task")
@click.argument("task")
@click.option(
    "--timeout",
    "-t",
    default=300,
    show_default=True,
    help="Maximum seconds to wait for the task to complete.",
)
@click.option(
    "--model",
    "-m",
    default=None,
    metavar="MODEL",
    help="Model override for the computer-use subagent.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Print the raw result dict as JSON instead of human-readable output.",
)
def run_task(task: str, timeout: int, model: str | None, as_json: bool):
    """Run a computer-use task in an isolated subagent.

    TASK is a natural-language description of what to accomplish.

    The task runs inside a subagent with the 'computer-use' profile so that
    intermediate screenshots stay inside the subagent's context — only a brief
    result summary is returned here.  This is the 'context-efficient tool-use
    loop until goal is achieved' pattern from gptme/gptme#216.

    To review what the subagent actually did after the task completes, use::

        gptme-util computer audit-log SESSION

    where SESSION is the session name printed in the output.

    Examples::

        gptme-util computer run-task "take a screenshot and describe the desktop"

        gptme-util computer run-task \\
            "open Firefox, go to example.com, and report the page title" \\
            --timeout 120

        gptme-util computer run-task "screenshot" --json
    """
    from ..tools.computer import computer_task  # lazy import (heavy deps)

    result = computer_task(task, timeout=timeout, model=model)

    if as_json:
        click.echo(json.dumps(result, indent=2))
        sys.exit(0 if result.get("status") == "success" else 1)

    status = result.get("status", "unknown")
    summary = result.get("result", "")
    agent_id = result.get("agent_id", "")
    logdir = result.get("logdir")
    conversation = result.get("conversation")

    status_icon = {
        "success": "✓",
        "failure": "✗",
        "timeout": "⏱",
        "clarification_needed": "?",
    }.get(status, "?")

    click.echo(f"{status_icon} Status: {status}")
    if summary:
        click.echo(f"  Result: {summary}")
    if agent_id:
        click.echo(f"  Agent:  {agent_id}")
    if conversation:
        click.echo(f"  Session: {conversation}")
        click.echo(f"  Audit:  gptme-util computer audit-log {conversation}")
    if logdir:
        click.echo(f"  Log:    {logdir}")

    sys.exit(0 if status == "success" else 1)


@computer.command("record")
@click.argument("output", required=False, default=None, metavar="OUTPUT")
@click.option(
    "--duration",
    "-d",
    default=10.0,
    show_default=True,
    type=float,
    help="Recording duration in seconds.",
)
@click.option(
    "--fps",
    default=10,
    show_default=True,
    type=int,
    help="Frames per second.  Use 10 for UI demos, 24+ for smooth game recordings.",
)
@click.option(
    "--display",
    default=None,
    metavar="DISPLAY",
    help="X11 display string (Linux only).  Defaults to $DISPLAY env var.",
)
def record_cmd(output: str | None, duration: float, fps: int, display: str | None):
    """Record the screen to an MP4 file.

    Uses ffmpeg x11grab (Linux) or avfoundation (macOS).  Blocks for DURATION
    seconds, then exits 0 and prints the path to the saved file.

    OUTPUT is the destination file path.  Defaults to a timestamped file
    in the system temporary directory.

    Use ``gptme-util computer video-frames OUTPUT`` to extract key frames
    from the recording for review.

    Examples::

        # Record 30 seconds to /tmp/demo.mp4
        gptme-util computer record /tmp/demo.mp4 --duration 30

        # Record 10s at 24fps (smoother for game recordings)
        gptme-util computer record game.mp4 --fps 24 --duration 10

        # Pipe into gptme for visual summary
        gptme-util computer record --duration 15 | xargs -I{} gptme-util computer video-frames {}
    """
    if not shutil.which("ffmpeg"):
        click.echo(
            "Error: ffmpeg not found. Install it with:\n"
            "  sudo apt install ffmpeg  # Debian/Ubuntu\n"
            "  brew install ffmpeg      # macOS",
            err=True,
        )
        sys.exit(1)

    if duration <= 0:
        click.echo("Error: --duration must be positive.", err=True)
        sys.exit(1)

    if fps <= 0 or fps > 120:
        click.echo("Error: --fps must be between 1 and 120.", err=True)
        sys.exit(1)

    from ..tools.computer import record_screen  # lazy import (heavy deps)

    try:
        click.echo(f"Recording {duration:.0f}s at {fps} fps...", err=True)
        path = record_screen(output=output, duration=duration, fps=fps, display=display)
        click.echo(str(path))
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _measure_terminal_startup(display: str, timeout: float = 15.0) -> dict:
    """Measure time from xterm launch to window focus.

    Launches xterm (or the fastest available terminal emulator) and uses
    xdotool --sync to detect when the window is ready.  Returns a dict with
    ``startup_ms`` on success or ``error`` on failure.

    This isolates the "new terminal window delay" from gptme/gptme#216 — a
    delay caused by X11 font loading and shell init that is separate from the
    screenshot pipeline measured by the main latency command.

    Mitigation if startup_ms is high (> 500 ms):
    - Use ``xterm -fn fixed`` to bypass Xft font loading (uses built-in bitmap).
    - Set ``XTerm*font: fixed`` in ``~/.Xdefaults`` as a permanent fix.
    - ``fc-cache -f`` warms the fontconfig cache and reduces the first-launch hit.
    - Use a lighter terminal (st / urxvt) which start in under 100 ms.
    """
    import subprocess
    import time

    # Try terminals in order of typical startup speed (fastest first).
    # xterm -fn fixed uses a built-in bitmap font, bypassing the Xft/fontconfig
    # scan that causes the multi-second delay in fresh Xvfb environments.
    _candidates = [
        ("xterm", ["-fn", "fixed"]),  # bitmap font: avoids font scan
        ("urxvt", []),  # rxvt-unicode — lighter than xterm
    ]

    terminal_cmd: str | None = None
    terminal_args: list[str] = []
    for name, args in _candidates:
        if shutil.which(name):
            terminal_cmd = name
            terminal_args = args
            break

    if not terminal_cmd:
        return {
            "error": "no terminal emulator found — install xterm: sudo apt install xterm"
        }

    if not shutil.which("xdotool"):
        return {"error": "xdotool not found — install it: sudo apt install xdotool"}

    env = os.environ.copy()
    env["DISPLAY"] = display

    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [terminal_cmd] + terminal_args,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        subprocess.run(
            [
                "xdotool",
                "search",
                "--sync",
                "--limit",
                "1",
                "--pid",
                str(proc.pid),
                "windowfocus",
                "--sync",
            ],
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        startup_ms = round((time.perf_counter() - t0) * 1000)
        return {
            "terminal": terminal_cmd,
            "args": terminal_args,
            "startup_ms": startup_ms,
            "display": display,
        }
    except subprocess.TimeoutExpired:
        return {
            "error": f"{terminal_cmd} window did not appear within {timeout:.0f}s — check $DISPLAY and xdotool",
            "terminal": terminal_cmd,
        }
    except subprocess.CalledProcessError as e:
        return {
            "error": f"xdotool failed: {e.stderr.strip()}",
            "terminal": terminal_cmd,
        }
    finally:
        # Always clean up the launched terminal so it doesn't litter the display.
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            proc.kill()
            proc.wait()


@computer.command("latency")
@click.option(
    "--shots",
    default=5,
    show_default=True,
    type=int,
    help="Number of screenshots to take for the latency measurement.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Output results as JSON.",
)
@click.option(
    "--display",
    default=None,
    metavar="DISPLAY",
    help="X11 display string (Linux only).  Defaults to $DISPLAY env var.",
)
@click.option(
    "--terminal",
    is_flag=True,
    default=False,
    help=(
        "Also measure terminal window startup latency (Linux only). "
        "Launches xterm and measures time until window is focused. "
        "Diagnoses the 'new terminal window delay' from gptme/gptme#216."
    ),
)
def latency_cmd(shots: int, as_json: bool, display: str | None, terminal: bool):
    """Measure screenshot and action latency to diagnose computer-use delays.

    Takes SHOTS screenshots and reports min/median/max latency, plus a
    breakdown of where time is spent.  Use this to identify whether delays
    are caused by X11 capture, image I/O, or the display pipeline.

    Use ``--terminal`` to also measure terminal window startup latency — the
    time from launching xterm to the window being ready for input.  This is
    the separate "new terminal window delay" mentioned in gptme/gptme#216,
    which is caused by X11 font loading and shell initialization rather than
    the screenshot pipeline.

    This command directly addresses the "figure out what is causing the delays"
    item from gptme/gptme#216.

    Examples::

        # Quick 5-shot screenshot latency check
        gptme-util computer latency

        # Take 10 shots for a more stable estimate
        gptme-util computer latency --shots 10

        # Also measure terminal window startup time
        gptme-util computer latency --terminal

        # Machine-readable output for scripting
        gptme-util computer latency --json

        # All measurements in JSON
        gptme-util computer latency --terminal --json
    """
    import statistics
    import time

    if shots < 1:
        click.echo("Error: --shots must be at least 1.", err=True)
        sys.exit(1)

    original_display = os.environ.get("DISPLAY")
    if display is not None:
        os.environ["DISPLAY"] = display

    try:
        from ..tools.computer_transport import (  # lazy import
            NativeComputerTransport,
            get_transport,
        )

        transport = get_transport()
        if transport is None:
            # No transport explicitly configured — auto-detect native X11/macOS.
            # This lets `gptme-util computer latency` work without requiring the
            # user to set GPTME_COMPUTER_TRANSPORT=native first (#216).
            import platform

            _display = os.environ.get("DISPLAY")
            _system = platform.system()
            if (_system == "Linux" and _display) or _system == "Darwin":
                transport = NativeComputerTransport()
            else:
                click.echo(
                    "Error: no display available — start an X11 display or set $DISPLAY.\n"
                    "  Xvfb :1 -screen 0 1024x768x24 &\n"
                    "  export DISPLAY=:1",
                    err=True,
                )
                sys.exit(1)

        # Warm up: take one shot to initialise any lazy state so the first measured
        # shot isn't artificially slow due to module imports or file descriptor setup.
        try:
            transport.screenshot()
        except Exception as e:
            click.echo(f"Error: warm-up screenshot failed: {e}", err=True)
            sys.exit(1)

        durations_ms: list[float] = []
        errors: list[str] = []

        for i in range(shots):
            t0 = time.perf_counter()
            try:
                transport.screenshot()
            except Exception as e:
                errors.append(str(e))
                continue
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            durations_ms.append(elapsed_ms)
            if not as_json:
                click.echo(f"  shot {i + 1:2d}/{shots}: {elapsed_ms:6.1f} ms")

        if not durations_ms:
            click.echo(
                f"Error: all {shots} screenshot attempts failed:\n"
                + "\n".join(f"  {e}" for e in errors),
                err=True,
            )
            sys.exit(1)

        min_ms: float = min(durations_ms)
        max_ms: float = max(durations_ms)
        median_ms: float = statistics.median(durations_ms)
        mean_ms: float = statistics.mean(durations_ms)
        stdev_ms: float | None = (
            statistics.stdev(durations_ms) if len(durations_ms) > 1 else None
        )

        result: dict = {
            "shots": shots,
            "successful": len(durations_ms),
            "errors": len(errors),
            "min_ms": min_ms,
            "max_ms": max_ms,
            "median_ms": median_ms,
            "mean_ms": mean_ms,
            "stdev_ms": stdev_ms,
            "display": os.environ.get("DISPLAY", ""),
            "platform": sys.platform,
        }

        # Terminal startup latency (optional, Linux/X11 only).
        # Measures time from xterm launch to window focus — the "new terminal
        # window delay" from gptme/gptme#216, which is separate from the
        # screenshot pipeline and caused by X11 font loading and shell init.
        terminal_startup: dict | None = None
        if terminal:
            effective_display = os.environ.get("DISPLAY", "")
            if sys.platform != "linux":
                if not as_json:
                    click.echo(
                        "⚠ --terminal is only supported on Linux (X11).", err=True
                    )
            elif not effective_display:
                if not as_json:
                    click.echo(
                        "⚠ --terminal requires $DISPLAY — skipping terminal measurement.",
                        err=True,
                    )
            else:
                terminal_startup = _measure_terminal_startup(effective_display)
            result["terminal_startup"] = terminal_startup

        if as_json:
            click.echo(json.dumps(result, indent=2))
            return

        click.echo("")
        click.echo(f"Screenshot latency ({len(durations_ms)}/{shots} successful):")
        click.echo(f"  min:    {min_ms:6.1f} ms")
        click.echo(f"  median: {median_ms:6.1f} ms")
        click.echo(f"  max:    {max_ms:6.1f} ms")
        if stdev_ms is not None:
            click.echo(f"  stdev:  {stdev_ms:6.1f} ms")
        click.echo("")

        if median_ms < 100:
            click.echo("✓ Latency is healthy (< 100 ms)")
        elif median_ms < 300:
            click.echo(
                "⚠ Latency is moderate (100–300 ms).\n"
                "  Possible causes: slow X11 display, high CPU load, or image scaling.\n"
                "  Try: DISPLAY=:1 with a local Xvfb instead of a remote X server."
            )
        else:
            click.echo(
                "✗ Latency is high (> 300 ms).\n"
                "  Likely causes: remote X11 display, high system load, or missing scrot.\n"
                "  On Linux: sudo apt install scrot && export DISPLAY=:1\n"
                "  For headless use: Xvfb :1 -screen 0 1024x768x24 &"
            )

        if terminal_startup is not None:
            click.echo("")
            click.echo("Terminal window startup latency:")
            if "error" in terminal_startup:
                click.echo(f"  ✗ {terminal_startup['error']}")
            else:
                startup_ms = terminal_startup["startup_ms"]
                terminal_name = terminal_startup.get("terminal", "?")
                args_str = " ".join(terminal_startup.get("args", [])) or "(defaults)"
                click.echo(f"  terminal: {terminal_name} {args_str}")
                click.echo(f"  startup:  {startup_ms} ms")
                click.echo("")
                if startup_ms < 500:
                    click.echo("✓ Terminal startup is fast (< 500 ms)")
                elif startup_ms < 2000:
                    click.echo(
                        "⚠ Terminal startup is slow (500 ms–2 s).\n"
                        "  Likely cause: X11 font loading (fontconfig scan).\n"
                        "  Fix: use a bitmap font to bypass Xft: xterm -fn fixed\n"
                        "  Or: run `fc-cache -f` once to warm the font cache.\n"
                        "  Or: set XTerm*font: fixed in ~/.Xdefaults"
                    )
                else:
                    click.echo(
                        "✗ Terminal startup is very slow (> 2 s).\n"
                        "  Root cause: X11 font loading is scanning system font dirs.\n"
                        "  Quick fix: xterm -fn fixed  (skips Xft, uses built-in bitmap)\n"
                        "  Permanent: add 'XTerm*font: fixed' to ~/.Xdefaults\n"
                        "  Alternative: use st (suckless terminal) — starts in < 100 ms:\n"
                        "    sudo apt install stterm  &&  st &"
                    )
    finally:
        if display is not None:
            if original_display is None:
                os.environ.pop("DISPLAY", None)
            else:
                os.environ["DISPLAY"] = original_display


# ---------------------------------------------------------------------------
# doctor command
# ---------------------------------------------------------------------------

_PASS = click.style("✓", fg="green")
_WARN = click.style("!", fg="yellow")
_FAIL = click.style("✗", fg="red")


def _check(label: str, ok: bool, warn: bool = False, hint: str = "") -> bool:
    """Print one doctor check line and return True if it passed."""
    if warn:
        click.echo(f"  {_WARN}  {label}" + (f"\n       {hint}" if hint else ""))
    elif ok:
        click.echo(f"  {_PASS}  {label}")
    else:
        click.echo(f"  {_FAIL}  {label}" + (f"\n       {hint}" if hint else ""))
    return ok


@computer.command("doctor")
@click.option(
    "--display",
    default=None,
    metavar="DISPLAY",
    help="X11 display string (Linux only). Defaults to $DISPLAY.",
)
def doctor_cmd(display: str | None):
    """Check computer-use prerequisites and report what is (not) working.

    Verifies that all required system tools, Python packages, and display
    infrastructure are available for the ``computer`` and ``browser`` tools.

    This command directly addresses the "figure out what is causing the delays"
    checklist item in gptme/gptme#216 by surfacing missing dependencies and
    reporting a screenshot latency sample.

    Examples::

        # Check current setup
        gptme-util computer doctor

        # Check a specific X11 display
        gptme-util computer doctor --display :1
    """
    import platform
    import statistics
    import time
    from contextlib import contextmanager

    system = platform.system()
    errors = 0
    effective_display = display or os.environ.get("DISPLAY") or ""

    @contextmanager
    def _temporary_display(display_value: str):
        old_display = os.environ.get("DISPLAY")
        if display_value:
            os.environ["DISPLAY"] = display_value
        try:
            yield
        finally:
            if old_display is None:
                os.environ.pop("DISPLAY", None)
            else:
                os.environ["DISPLAY"] = old_display

    click.echo("Computer-use doctor\n")

    # --- Platform ---
    click.echo(f"Platform: {system} ({platform.machine()})\n")

    # --- Display / X11 (Linux) ---
    if system == "Linux":
        click.echo("Display:")
        ok_display = bool(effective_display)
        if not _check(
            f"$DISPLAY={effective_display!r}" if ok_display else "$DISPLAY not set",
            ok=ok_display,
            hint="Start Xvfb:  Xvfb :1 -screen 0 1024x768x24 &  && export DISPLAY=:1",
        ):
            errors += 1

        ok_xdotool = bool(shutil.which("xdotool"))
        if not _check(
            "xdotool installed" if ok_xdotool else "xdotool not found (mouse/keyboard)",
            ok=ok_xdotool,
            hint="sudo apt install xdotool",
        ):
            errors += 1

        ok_scrot = bool(shutil.which("scrot"))
        ok_ffmpeg_scap = bool(shutil.which("ffmpeg"))
        # scrot is preferred; ffmpeg fallback works too
        if ok_scrot:
            _check("scrot installed (screenshot backend)", ok=True)
        elif ok_ffmpeg_scap:
            _check("scrot not found, ffmpeg available (fallback)", ok=True, warn=True)
        else:
            _check(
                "scrot not found (screenshots will fail)",
                ok=False,
                hint="sudo apt install scrot",
            )
            errors += 1

        # AT-SPI (optional — needed for accessibility_tree action)
        try:
            import pyatspi  # type: ignore[import-not-found]  # noqa: F401

            _check("pyatspi installed (AT-SPI accessibility tree)", ok=True)
        except ImportError:
            _check(
                "pyatspi not installed (accessibility_tree action disabled)",
                ok=True,
                warn=True,
                hint="pip install pyatspi  (optional — needed for accessibility_tree action)",
            )

        # ~/.Xdefaults / ~/.Xresources bitmap-font check (terminal startup delay fix — #216).
        # xterm's default Xft renderer scans all system font dirs on first launch
        # (fontconfig scan), adding 1–3 s of startup latency.  Setting XTerm*font
        # to a built-in bitmap font ("fixed") bypasses Xft entirely.
        # Many modern distros apply X resources via ~/.Xresources (loaded by the
        # display manager / xrdb), so we probe both files.
        _xhome = Path.home()
        _xres_content = ""
        for _fname in (".Xresources", ".Xdefaults"):
            _xp = _xhome / _fname
            if _xp.exists():
                _xres_content += (
                    _xp.read_text(encoding="utf-8", errors="replace") + "\n"
                )
        _has_bitmap_font = bool(
            re.search(
                r"(?m)^XTerm\*(?:bold)?[Ff]ont\s*:\s*(?:fixed|6x13|7x13|8x13|9x15)",
                _xres_content,
            )
        )
        _check(
            "~/.Xresources / ~/.Xdefaults uses bitmap font (fast xterm startup)"
            if _has_bitmap_font
            else "~/.Xresources / ~/.Xdefaults missing bitmap font (xterm startup may be slow — 1–3 s)",
            ok=True,
            warn=not _has_bitmap_font,
            hint=(
                "Add  XTerm*font: fixed  to ~/.Xdefaults and run  xrdb -merge ~/.Xdefaults\n"
                "       This cuts xterm startup from ~2 s to < 100 ms (issue #216).\n"
                "       See: gptme-util computer latency --terminal"
            ),
        )
        click.echo()

    # --- macOS native tools ---
    if system == "Darwin":
        click.echo("macOS tools:")
        # screencapture is a built-in macOS utility — always present
        ok_sc = bool(shutil.which("screencapture"))
        if not _check(
            "screencapture available"
            if ok_sc
            else "screencapture missing (unexpected)",
            ok=ok_sc,
        ):
            errors += 1

        ok_cliclick = bool(shutil.which("cliclick"))
        if not _check(
            "cliclick installed (mouse/keyboard)"
            if ok_cliclick
            else "cliclick not found (mouse/keyboard disabled)",
            ok=ok_cliclick,
            hint="brew install cliclick",
        ):
            errors += 1

        # osascript: built-in, needed for accessibility tree on macOS
        ok_osa = bool(shutil.which("osascript"))
        if not _check(
            "osascript available (macOS accessibility tree)"
            if ok_osa
            else "osascript missing (unexpected)",
            ok=ok_osa,
        ):
            errors += 1
        click.echo()

    # --- Browser / Playwright ---
    click.echo("Browser (Playwright):")
    try:
        from playwright.sync_api import (
            sync_playwright,  # type: ignore[import-not-found]
        )

        _check("playwright package installed", ok=True)

        # Check that at least one browser binary is present
        try:
            with sync_playwright() as pw:
                chromium_path = pw.chromium.executable_path
                ok_chromium = bool(chromium_path) and Path(chromium_path).exists()
        except Exception:
            ok_chromium = False

        # Chromium binary is optional — the computer tool works without it.
        # Treat a missing binary the same way as a missing playwright package.
        _check(
            "Playwright chromium available"
            if ok_chromium
            else "Playwright chromium not installed (browser tool disabled)",
            ok=True,
            warn=not ok_chromium,
            hint="python -m playwright install chromium  (optional — needed for browser tool)",
        )
    except ImportError:
        _check(
            "playwright not installed (browser tool disabled)",
            ok=True,
            warn=True,
            hint="pip install playwright  &&  python -m playwright install chromium  (optional — needed for browser tool)",
        )
    click.echo()

    # --- Screenshot latency sample ---
    click.echo("Screenshot latency:")
    try:
        from ..tools.computer_transport import NativeComputerTransport, get_transport

        with _temporary_display(effective_display if system == "Linux" else ""):
            transport = get_transport()
            if transport is None:
                if (system == "Linux" and effective_display) or system == "Darwin":
                    transport = NativeComputerTransport()

            if transport is None:
                _check(
                    "no display available — skipping latency sample",
                    ok=False,
                    hint="Set $DISPLAY first",
                )
                errors += 1
            else:
                # Warm-up shot (not measured)
                try:
                    transport.screenshot()
                except Exception as exc:
                    _check(f"warm-up screenshot failed: {exc}", ok=False)
                    errors += 1
                    transport = None  # skip the timed loop

                if transport is not None:
                    durations_ms: list[float] = []
                    for _ in range(3):
                        t0 = time.perf_counter()
                        try:
                            transport.screenshot()
                            durations_ms.append((time.perf_counter() - t0) * 1000)
                        except Exception:
                            pass

                    if durations_ms:
                        median_ms = statistics.median(durations_ms)
                        p = _PASS if median_ms < 300 else _WARN
                        click.echo(
                            f"  {p}  median={median_ms:.0f} ms  "
                            f"min={min(durations_ms):.0f} ms  max={max(durations_ms):.0f} ms"
                            " (3 shots)"
                        )
                        if median_ms >= 300:
                            click.echo(
                                "       High latency — possible causes: remote X11, high load, "
                                "missing scrot.\n"
                                "       Run `gptme-util computer latency --shots 10` for a "
                                "detailed breakdown."
                            )
                    else:
                        _check("all screenshot attempts failed", ok=False)
                        errors += 1
    except Exception as exc:
        _check(f"could not measure latency: {exc}", ok=False)
        errors += 1
    click.echo()

    # --- Summary ---
    if errors == 0:
        click.echo(
            click.style("✅  All checks passed — computer-use is ready.", fg="green")
        )
    else:
        click.echo(
            click.style(f"❌  {errors} check(s) failed.", fg="red")
            + "  Fix the items above and re-run `gptme-util computer doctor`."
        )
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# demo command
# ---------------------------------------------------------------------------

# Same HTML fixture used by the eval suite's computer-use-web-tweet-compose spec
# (gptme/eval/suites/computer.py).  Uses Twitter's real data-testid selectors so
# the same fill_element / click_element calls that pass here work against a real
# X.com compose page once the user provides authenticated browser state.
_DEMO_TWEET_HTML = (
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


def _demo_tweet_url() -> str:
    import base64

    return "data:text/html;base64," + base64.b64encode(
        _DEMO_TWEET_HTML.encode()
    ).decode("ascii")


@computer.command("demo")
@click.option(
    "--text",
    default="Hello from gptme!",
    show_default=True,
    help="Tweet text to type into the compose box.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Output result as JSON.",
)
def demo_cmd(text: str, as_json: bool):
    """Run a self-contained end-to-end demo of the browser automation pipeline.

    Opens a local HTML fixture that mimics the Twitter/X compose UI (same
    ``data-testid`` selectors as the real page), types a tweet, clicks the
    submit button, and verifies that the DOM updated — all without an LLM or
    network access.

    This is the tool-layer equivalent of the "Can it Tweet?" milestone from
    gptme/gptme#216.  If the demo passes, the full
    ``open_page → fill_element → click_element → read_page_text`` pipeline is
    working and ready for a live gptme computer-use session.

    Examples::

        # Run the demo (exit 0 = pass, exit 1 = fail)
        gptme-util computer demo

        # Custom tweet text
        gptme-util computer demo --text "Shipped it!"

        # Machine-readable output for scripting
        gptme-util computer demo --json
    """
    import time

    # sync_playwright is imported at module level; None when playwright is absent.
    if sync_playwright is None:
        msg = (
            "playwright is not installed — browser demo unavailable.\n"
            "Install it with:\n"
            "  pip install playwright\n"
            "  python -m playwright install chromium"
        )
        if as_json:
            click.echo(
                json.dumps(
                    {
                        "status": "error",
                        "error": "playwright not installed",
                        "steps": [],
                    }
                )
            )
        else:
            click.echo(f"✗ {msg}", err=True)
        raise SystemExit(1)

    steps: list[dict] = []
    overall_t0 = time.perf_counter()

    def _step(name: str, ok: bool, elapsed_ms: float, detail: str = "") -> None:
        steps.append(
            {"step": name, "ok": ok, "elapsed_ms": round(elapsed_ms), "detail": detail}
        )
        if not as_json:
            icon = _PASS if ok else _FAIL
            detail_str = f"  ({detail})" if detail else ""
            click.echo(f"  {icon}  {name}{detail_str}  [{elapsed_ms:.0f} ms]")

    if not as_json:
        click.echo("Computer-use browser demo (tweet-compose fixture)\n")

    fixture_url = _demo_tweet_url()

    with sync_playwright() as pw:
        t0 = time.perf_counter()
        try:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            _step("launch browser", True, (time.perf_counter() - t0) * 1000)
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            _step("launch browser", False, elapsed, str(exc))
            _finish(steps, overall_t0, as_json, failed=True)
            raise SystemExit(1) from None

        # Step 1: open_page — load the fixture
        t0 = time.perf_counter()
        try:
            page.goto(fixture_url, wait_until="domcontentloaded", timeout=10_000)
            _step("open_page (load fixture)", True, (time.perf_counter() - t0) * 1000)
        except Exception as exc:
            _step(
                "open_page (load fixture)",
                False,
                (time.perf_counter() - t0) * 1000,
                str(exc),
            )
            browser.close()
            _finish(steps, overall_t0, as_json, failed=True)
            raise SystemExit(1) from None

        # Step 2: wait_for_element — compose box ready
        t0 = time.perf_counter()
        try:
            page.wait_for_selector('[data-testid="tweetTextarea_0"]', timeout=5_000)
            _step(
                'wait_for_element [data-testid="tweetTextarea_0"]',
                True,
                (time.perf_counter() - t0) * 1000,
            )
        except Exception as exc:
            _step(
                'wait_for_element [data-testid="tweetTextarea_0"]',
                False,
                (time.perf_counter() - t0) * 1000,
                str(exc),
            )
            browser.close()
            _finish(steps, overall_t0, as_json, failed=True)
            raise SystemExit(1) from None

        # Step 3: fill_element — type the tweet text
        t0 = time.perf_counter()
        try:
            el = page.locator('[data-testid="tweetTextarea_0"]')
            el.click()
            el.fill(text)
            actual = el.inner_text()
            ok_fill = text.strip() in actual or actual.strip() == text.strip()
            _step(
                "fill_element (type tweet)",
                ok_fill,
                (time.perf_counter() - t0) * 1000,
                f"typed {len(text)} chars",
            )
        except Exception as exc:
            _step(
                "fill_element (type tweet)",
                False,
                (time.perf_counter() - t0) * 1000,
                str(exc),
            )
            browser.close()
            _finish(steps, overall_t0, as_json, failed=True)
            raise SystemExit(1) from None

        # Step 4: click_element — submit the tweet
        t0 = time.perf_counter()
        try:
            page.locator('[data-testid="tweetButtonInline"]').click()
            _step(
                'click_element [data-testid="tweetButtonInline"]',
                True,
                (time.perf_counter() - t0) * 1000,
            )
        except Exception as exc:
            _step(
                'click_element [data-testid="tweetButtonInline"]',
                False,
                (time.perf_counter() - t0) * 1000,
                str(exc),
            )
            browser.close()
            _finish(steps, overall_t0, as_json, failed=True)
            raise SystemExit(1) from None

        # Step 5: read_page_text — verify the DOM updated
        t0 = time.perf_counter()
        try:
            page_text = page.locator("#status").inner_text(timeout=3_000)
            ok_verify = page_text.startswith("tweet-posted:")
            _step(
                "read_page_text (verify submit)",
                ok_verify,
                (time.perf_counter() - t0) * 1000,
                repr(page_text[:60]) if page_text else "(empty)",
            )
        except Exception as exc:
            _step(
                "read_page_text (verify submit)",
                False,
                (time.perf_counter() - t0) * 1000,
                str(exc),
            )
            browser.close()
            _finish(steps, overall_t0, as_json, failed=True)
            raise SystemExit(1) from None

        browser.close()

    failed = any(not s["ok"] for s in steps)
    _finish(steps, overall_t0, as_json, failed=failed)
    if failed:
        raise SystemExit(1)


def _finish(steps: list[dict], t0: float, as_json: bool, failed: bool) -> None:
    import time

    total_ms = round((time.perf_counter() - t0) * 1000)
    status = "fail" if failed else "pass"
    if as_json:
        click.echo(
            json.dumps(
                {"status": status, "total_ms": total_ms, "steps": steps}, indent=2
            )
        )
    else:
        click.echo("")
        if failed:
            click.echo(
                click.style("✗  Demo failed.", fg="red")
                + "  Check the steps above and run `gptme-util computer doctor`."
            )
        else:
            click.echo(
                click.style(f"✅  Demo passed in {total_ms} ms.", fg="green")
                + "  The browser automation pipeline is working end-to-end."
            )
