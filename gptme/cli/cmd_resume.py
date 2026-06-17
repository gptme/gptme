"""CLI commands for gptme resume — rehydrate context from a prior session.

Provides ``gptme-util resume`` (via lazy registration in ``util.py``) and
``gptme-resume`` (standalone entry point registered in ``pyproject.toml``).

Produces a lossy <<RESUMED SESSION>> bootstrap prompt from the most recent
(or specified) session trajectory — the middle tier of the resume ladder:
    high-fidelity   session/load   (full snapshot — not yet implemented)
    lossy           <<RESUMED SESSION>> prompt from trajectory   ← THIS
    fresh           clean new session (fallback when no trajectory exists)

Inspired by OpenHands' ACP resume ladder (OpenHands/OpenHands#14640).
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

# ── helpers ────────────────────────────────────────────────────────────


def _get_logs_dir() -> Path:
    """Return the gptme logs directory, raising UsageError if not found."""
    try:
        from ..dirs import get_logs_dir  # fmt: skip

        return get_logs_dir()
    except Exception as exc:
        raise click.UsageError("Could not locate gptme logs directory") from exc


def _list_sessions(logs_dir: Path, n: int = 20) -> list[Path]:
    """Return up to *n* session dirs, most-recently-modified first."""
    dirs = [
        d
        for d in logs_dir.iterdir()
        if d.is_dir() and (d / "conversation.jsonl").exists()
    ]
    dirs.sort(key=lambda d: (d / "conversation.jsonl").stat().st_mtime, reverse=True)
    return dirs[:n]


def _session_name(session_dir: Path) -> str:
    """Extract human-readable name from config.toml or directory name."""
    config = session_dir / "config.toml"
    if config.exists():
        for line in config.read_text().splitlines():
            m = re.match(r'^name\s*=\s*"(.+)"', line.strip())
            if m:
                return m.group(1)
    name = session_dir.name
    return re.sub(r"^run-[a-z]+-", "", name)


def _user_message(session_dir: Path) -> str | None:
    """Extract the original task/mission from user or system messages."""
    conv = session_dir / "conversation.jsonl"
    if not conv.exists():
        return None

    messages = []
    for line in conv.read_text().splitlines():
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    mission_patterns = [
        r"## Mission\s*\n(.*?)(?=\n##|\n---|\Z)",
        r"\*\*Mission\*\*:\s*(.*?)(?=\n|$)",
        r"\*\*Title\*\*:\s*(.*?)(?=\n|$)",
        r"\*\*Objective\*\*:\s*(.*?)(?=\n|$)",
    ]
    for msg in messages:
        content = str(msg.get("content", ""))
        for pattern in mission_patterns:
            m = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
            if m:
                task = m.group(1).strip()
                if len(task) > 50:
                    return task[:2000]

    for msg in messages:
        if msg.get("role") == "user":
            content = str(msg.get("content", ""))
            content = re.sub(
                r"<!-- BOB_SESSION_SENTINEL=.*?-->",
                "",
                content,
                flags=re.DOTALL,
            ).strip()
            content = re.sub(r"\n{3,}", "\n\n", content)
            m = re.search(
                r"(?:## Mission|## Task|## Objective|## Your Mission)(.*?)(?=\n##|\n---|\n>|---|\Z)",
                content,
                re.DOTALL | re.IGNORECASE,
            )
            if m:
                task = m.group(1).strip()
                if len(task) > 30:
                    return task[:2000]
            return content[:2000]
    return None


def _last_reasoning(session_dir: Path) -> str | None:
    """Extract the final assistant message's reasoning or content tail."""
    conv = session_dir / "conversation.jsonl"
    if not conv.exists():
        return None

    last = None
    for line in conv.read_text().splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("role") == "assistant":
            last = msg

    if not last:
        return None

    content = str(last.get("content", ""))
    m = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
    if m:
        thinking = m.group(1).strip()
        return thinking[-500:] if len(thinking) > 500 else thinking
    return content[-300:] if len(content) > 300 else content


def _tools_used(session_dir: Path) -> list[str]:
    """List tools called in the session (from tool-outputs directory)."""
    tool_outputs = session_dir / "tool-outputs"
    if not tool_outputs.exists():
        return []
    tools = []
    for entry in sorted(tool_outputs.iterdir()):
        if entry.is_dir():
            count = sum(1 for _ in entry.iterdir())
            if count:
                tools.append(f"{entry.name} ({count} calls)")
    return tools


def synthesize_prompt(session_dir: Path, max_chars: int = 3000) -> str:
    """Build a <<RESUMED SESSION>> bootstrap prompt from *session_dir*."""
    name = _session_name(session_dir)
    user_msg = _user_message(session_dir)
    reasoning = _last_reasoning(session_dir)
    tools = _tools_used(session_dir)

    mtime = datetime.fromtimestamp(
        (session_dir / "conversation.jsonl").stat().st_mtime,
        tz=timezone.utc,
    ).strftime("%Y-%m-%d %H:%M UTC")

    parts = ["<<RESUMED SESSION>>\n"]
    parts.append(
        "Your prior session was interrupted. Continue from where you left off.\n"
    )
    parts.append(f"[SESSION: {name}]")
    parts.append(f"[LAST ACTIVE: {mtime}]")
    if tools:
        parts.append(f"[TOOLS USED: {', '.join(tools)}]")
    parts.append("")

    if user_msg:
        parts.append("[ORIGINAL TASK]")
        available = max_chars - sum(len(p) for p in parts) - 500
        if available > 0:
            parts.append(user_msg[:available])
        parts.append("")

    if reasoning:
        parts.append("[LAST REASONING]")
        parts.append(reasoning)
        parts.append("")

    parts.append("--- End of prior session ---\n")
    parts.append("Continue your work. What was your next step?")
    return "\n".join(parts)


# ── command ────────────────────────────────────────────────────────────


@click.command("resume")
@click.option("--list", "do_list", is_flag=True, help="List recent sessions and exit.")
@click.option(
    "--last",
    default=0,
    show_default=True,
    help="Nth-from-last session to resume (0 = most recent).",
)
@click.option(
    "--session",
    "session_path",
    default=None,
    metavar="DIR",
    help="Explicit path to a session directory.",
)
@click.option(
    "--max-chars",
    default=3000,
    show_default=True,
    help="Maximum context characters in the resume prompt.",
)
@click.option(
    "--output",
    "output_fmt",
    type=click.Choice(["prompt", "json"], case_sensitive=False),
    default="prompt",
    show_default=True,
    help="Output format: 'prompt' (default) or 'json' metadata.",
)
def resume(
    do_list: bool,
    last: int,
    session_path: str | None,
    max_chars: int,
    output_fmt: str,
) -> None:
    """Rehydrate a <<RESUMED SESSION>> prompt from a prior session trajectory.

    Prints the resume prompt to stdout so it can be piped into gptme:

    \b
        gptme-resume | gptme -c "$(cat)"
        gptme-resume --last 1 --output json
    """
    logs_dir = _get_logs_dir()

    if not logs_dir.exists():
        raise click.ClickException(f"gptme logs directory not found: {logs_dir}")

    sessions = _list_sessions(logs_dir, n=20)

    if do_list:
        if not sessions:
            click.echo("No sessions found.", err=True)
            sys.exit(0)
        click.echo(f"Recent sessions in {logs_dir}:")
        for i, s in enumerate(sessions):
            name = _session_name(s)
            mtime = datetime.fromtimestamp(
                (s / "conversation.jsonl").stat().st_mtime,
                tz=timezone.utc,
            ).strftime("%Y-%m-%d %H:%M")
            has_task = _user_message(s) is not None
            status = "has_task" if has_task else "no_user_msg"
            click.echo(f"  [{i}] {mtime}  {name}  ({status})")
        return

    if session_path is not None:
        session_dir = Path(session_path)
        if not session_dir.exists():
            raise click.ClickException(f"Session directory not found: {session_path}")
    else:
        if not sessions:
            raise click.ClickException(
                f"No sessions found in {logs_dir}. Run a gptme session first."
            )
        if last >= len(sessions):
            raise click.ClickException(
                f"Only {len(sessions)} session(s) available; requested index {last}."
            )
        session_dir = sessions[last]

    if output_fmt == "json":
        name = _session_name(session_dir)
        mtime_ts = (session_dir / "conversation.jsonl").stat().st_mtime
        mtime = datetime.fromtimestamp(mtime_ts, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
        has_task = _user_message(session_dir) is not None
        tools = _tools_used(session_dir)
        click.echo(
            json.dumps(
                {
                    "session": str(session_dir),
                    "name": name,
                    "last_active": mtime,
                    "has_task": has_task,
                    "tools_used": tools,
                },
                indent=2,
            )
        )
    else:
        prompt = synthesize_prompt(session_dir, max_chars=max_chars)
        click.echo(prompt)
