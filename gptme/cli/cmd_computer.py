"""CLI commands for computer-use tooling (audit-log, screenshot, etc.)."""

import json
import os
import re
import sys
from pathlib import Path

import click

from ..dirs import get_logs_dir
from ..logmanager import _gen_read_jsonl
from ..tools.base import ToolUse

# Patterns that indicate text/key content (redact for privacy)
_SENSITIVE_ACTIONS = frozenset({"type", "key"})

# Browser interaction functions whose first arg is a URL
_URL_BROWSER_FNS = frozenset({"observe_web", "snapshot_url", "open_page"})

# Browser interaction functions whose first arg is a CSS/DOM selector
_SELECTOR_BROWSER_FNS = frozenset({"click_element"})

# ---------------------------------------------------------------------------
# Action risk classification
# ---------------------------------------------------------------------------
# read      — no side effects; safe to run without confirmation
# write     — modifies visible state (mouse/keyboard/browser interaction)
# sensitive — write action that also handles potentially private data
#             (text content is redacted in the audit log, but the action itself
#             is flagged so reviewers know private data may have been processed)
#
# This mirrors the three-tier permission model described in the computer-use
# profile system prompt: observation → structured interaction → raw input.

#: Actions that only read state and have no side effects.
ACTION_RISK_READ: frozenset[str] = frozenset(
    {
        "screenshot",
        "cursor_position",
        "accessibility_tree",
        "wait_for_change",
        # browser observation
        "snapshot_url",
        "observe_web",
        "read_page_text",
        # high-level wrappers
        "observe_desktop",
    }
)

#: Actions that change visible state (clicks, navigation, scrolling).
ACTION_RISK_WRITE: frozenset[str] = frozenset(
    {
        "left_click",
        "right_click",
        "middle_click",
        "double_click",
        "mouse_move",
        "scroll",
        "window_focus",
        # browser interaction
        "click_element",
        "scroll_page",
        "open_page",
    }
)

#: Actions that handle potentially private data (keyboard input, form fills).
#: Text content is always redacted in the audit log; only length is recorded.
ACTION_RISK_SENSITIVE: frozenset[str] = frozenset(
    {
        "type",
        "key",
        "left_click_drag",
        # browser
        "fill_element",
    }
)


def action_risk_level(action: str) -> str:
    """Return the risk level for a computer-use action.

    Returns one of ``"read"``, ``"write"``, or ``"sensitive"``.
    Unknown actions default to ``"write"`` (conservative).
    """
    if action in ACTION_RISK_READ:
        return "read"
    if action in ACTION_RISK_SENSITIVE:
        return "sensitive"
    # write is the conservative default for any unclassified action
    return "write"


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
    - ``fill_element(selector, value)`` — form fill (value length logged, not raw text)
    - ``read_page_text()`` — read page text content
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
                        "risk_level": "read",
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
                        "risk_level": "sensitive",
                    },
                )
                for m in re.finditer(
                    r"""\bfill_element\s*\(\s*(?:'([^']*)'|"([^"]*)")\s*,\s*(?:'([^']*)'|"([^"]*)")""",
                    code,
                )
            )

            # read_page_text() — no arguments to extract
            browser_positioned.extend(
                (
                    m.start(),
                    {
                        "timestamp": ts,
                        "action": "read_page_text",
                        "source": "browser",
                        "risk_level": "read",
                    },
                )
                for m in re.finditer(r"\bread_page_text\s*\(", code)
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
                        "risk_level": "write",
                    },
                )
                for m in re.finditer(r"""\bscroll_page\s*\(\s*['"]([^'"]+)['"]""", code)
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
    "--json", "as_json", is_flag=True, help="Output raw JSON instead of table."
)
def audit_log(conversation: str | None, last: int, as_json: bool):
    """Extract computer-use actions from session trajectories.

    Reads conversation JSONL logs (the authoritative audit trail) and prints a
    structured summary of every computer(), act_and_observe(), observe_desktop(),
    and browser interaction call (observe_web, open_page, fill_element,
    click_element, …). Typed/key text and fill_element values are redacted to
    just their length.

    CONVERSATION is a conversation name or ID. Omit to scan the most-recent
    session(s) (controlled by --last).

    Examples:

    \b
        gptme-util computer audit-log
        gptme-util computer audit-log --last 3
        gptme-util computer audit-log my-session-name --json
    """
    logs_dir = get_logs_dir()

    if conversation:
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

    if not all_records:
        click.echo("No computer-use actions found.")
        return

    if as_json:
        click.echo(json.dumps(all_records, indent=2))
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
