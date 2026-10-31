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
        for line in config.read_text(errors="replace").splitlines():
            m = re.match(r'^name\s*=\s*"(.+)"', line.strip())
            if m:
                return m.group(1)
    name = session_dir.name
    return re.sub(r"^run-[a-z]+-", "", name)


def _task_from_user_content(content: str) -> str:
    """Prefer the first explicit, unquoted task heading; otherwise keep the request.

    Heading recognition is deliberately conservative: column-zero Markdown
    headings/labels outside fenced code. This is not an intent classifier for
    arbitrary quoted prose. Keep the original text intact for the fallback.
    """
    lines = content.splitlines()
    # Use the same fence state for candidate headings and section boundaries.
    unfenced = []
    fence: str | None = None
    for line in lines:
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        unfenced.append(fence is None and marker is None)
        if fence is not None:
            if marker and re.fullmatch(
                re.escape(fence[0]) + "{" + str(len(fence)) + ",}\\s*", line.lstrip()
            ):
                fence = None
        elif marker:
            fence = marker.group(1)

    for index, line in enumerate(lines):
        if not unfenced[index]:
            continue
        heading = re.fullmatch(
            r"## (?:Mission|Task|Objective|Your Mission)(?:[ \t]+(.*))?",
            line,
            re.IGNORECASE,
        )
        label = re.match(
            r"^\*\*(?:Mission|Title|Objective)\*\*:\s*(.*)$", line, re.IGNORECASE
        )
        if heading:
            body = [heading.group(1) or ""]
            for offset in range(index + 1, len(lines)):
                following = lines[offset]
                if unfenced[offset] and re.match(r"^(?:##|---|>)", following):
                    break
                body.append(following)
            task = "\n".join(body).strip()
            if len(task) > 30:
                return task[:2000]
        elif label:
            task = label.group(1).strip()
            if len(task) > 50:
                return task[:2000]
    return content.strip()[:2000]


def _user_message(session_dir: Path) -> str | None:
    """Extract the original task only from the first user-authored message."""
    conv = session_dir / "conversation.jsonl"
    if not conv.exists():
        return None

    for line in conv.read_text(errors="replace").splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = str(msg.get("content", ""))
            content = re.sub(r"\n{3,}", "\n\n", content)
            return _task_from_user_content(content) or None
    return None


def _last_reasoning(session_dir: Path) -> str | None:
    """Extract the final assistant message's reasoning or content tail."""
    conv = session_dir / "conversation.jsonl"
    if not conv.exists():
        return None

    last = None
    for line in conv.read_text(errors="replace").splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and msg.get("role") == "assistant":
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
    """Build a <<RESUMED SESSION>> bootstrap prompt from *session_dir*.

    ``max_chars`` bounds the generated prompt. Raise ``ValueError`` if it
    cannot hold the framing lines; otherwise truncate the context sections
    (original task, last reasoning) to fit the remaining budget. A section is
    omitted entirely when there is no room for its body, so the prompt never
    contains a header with an empty body.
    """
    name = _session_name(session_dir)
    user_msg = _user_message(session_dir)
    reasoning = _last_reasoning(session_dir)
    tools = _tools_used(session_dir)

    mtime = datetime.fromtimestamp(
        (session_dir / "conversation.jsonl").stat().st_mtime,
        tz=timezone.utc,
    ).strftime("%Y-%m-%d %H:%M UTC")

    header = ["<<RESUMED SESSION>>\n"]
    header.append(
        "Your prior session was interrupted. Continue from where you left off.\n"
    )
    header.append(f"[SESSION: {name}]")
    header.append(f"[LAST ACTIVE: {mtime}]")
    if tools:
        header.append(f"[TOOLS USED: {', '.join(tools)}]")
    header.append("")

    footer = [
        "--- End of prior session ---\n",
        "Continue your work. What was your next step?",
    ]

    # Joining inserts one newline between parts, but none after the last part.
    framing_size = len("\n".join(header + footer))
    if max_chars < framing_size:
        raise ValueError(
            f"{max_chars} is too small for the resume prompt framing "
            f"(requires at least {framing_size} characters)."
        )
    budget = max_chars - framing_size

    parts = list(header)
    for label, text in (("[ORIGINAL TASK]", user_msg), ("[LAST REASONING]", reasoning)):
        if not text:
            continue
        # A section costs the label line, the body line, and a blank separator.
        overhead = len(label) + 3
        if budget <= overhead:
            continue
        chunk = text[: budget - overhead]
        parts.extend((label, chunk, ""))
        budget -= overhead + len(chunk)

    parts.extend(footer)
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
    help="Maximum prompt characters; must fit the session's framing lines.",
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

    Prints the resume prompt to stdout so it can be passed to gptme:

    \b
        gptme -- "$(gptme-resume)"
        gptme-resume --last 1 --output json
    """
    logs_dir = _get_logs_dir()

    if not logs_dir.exists():
        raise click.ClickException(f"gptme logs directory not found: {logs_dir}")

    sessions = _list_sessions(logs_dir, n=20)

    if do_list:
        if not sessions:
            click.echo("No sessions found.", err=True)
            return
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
        if not (session_dir / "conversation.jsonl").exists():
            raise click.ClickException(
                f"Not a valid gptme session (missing conversation.jsonl): {session_path}"
            )
    else:
        if not sessions:
            raise click.ClickException(
                f"No sessions found in {logs_dir}. Run a gptme session first."
            )
        if last < 0 or last >= len(sessions):
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
        try:
            prompt = synthesize_prompt(session_dir, max_chars=max_chars)
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="--max-chars") from exc
        click.echo(prompt)
