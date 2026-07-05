"""CLI commands for computer-use tooling (audit-log, screenshot, etc.)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

import click

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
_SELECTOR_BROWSER_FNS = frozenset({"click_element"})

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

            # read_page_text() — no arguments to extract
            browser_positioned.extend(
                (
                    m.start(),
                    {
                        "timestamp": ts,
                        "action": "read_page_text",
                        "source": "browser",
                        "risk_level": action_risk_level("read_page_text"),
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
                        "risk_level": action_risk_level("scroll_page"),
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
def latency_cmd(shots: int, as_json: bool, display: str | None):
    """Measure screenshot and action latency to diagnose computer-use delays.

    Takes SHOTS screenshots and reports min/median/max latency, plus a
    breakdown of where time is spent.  Use this to identify whether delays
    are caused by X11 capture, image I/O, or the display pipeline.

    This command directly addresses the "figure out what is causing the delays"
    item from gptme/gptme#216.

    Examples::

        # Quick 5-shot screenshot latency check
        gptme-util computer latency

        # Take 10 shots for a more stable estimate
        gptme-util computer latency --shots 10

        # Machine-readable output for scripting
        gptme-util computer latency --json
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

        result = {
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
    finally:
        if display is not None:
            if original_display is None:
                os.environ.pop("DISPLAY", None)
            else:
                os.environ["DISPLAY"] = original_display
