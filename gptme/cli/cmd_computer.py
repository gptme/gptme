"""CLI commands for computer-use tooling (audit-log, etc.)."""

import json
import re
import sys
from pathlib import Path

import click

from ..dirs import get_logs_dir
from ..logmanager import _gen_read_jsonl
from ..tools.base import ToolUse

# Patterns that indicate text/key content (redact for privacy)
_SENSITIVE_ACTIONS = frozenset({"type", "key"})


def _extract_computer_calls(messages) -> list[dict]:
    """Extract computer() and observe_desktop() calls from a message list.

    Scans executable tool-use blocks (ipython codeblocks) for calls to the
    computer() function and the observe_desktop() helper. Typed text is never
    logged raw — only its length is recorded.
    """
    records: list[dict] = []
    for msg in messages:
        if msg.role != "assistant":
            continue
        for tu in ToolUse.iter_from_content(msg.content):
            if not tu.is_runnable or not tu.content:
                continue
            code = tu.content
            # Match computer("action", ...) and computer('action', ...)
            for m in re.finditer(r"""computer\s*\(\s*['"]([^'"]+)['"]""", code):
                action = m.group(1)
                record: dict = {
                    "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
                    "action": action,
                }
                # Extract coordinate if present: coordinate=(x, y)
                coord_m = re.search(r"coordinate\s*=\s*\((\d+)\s*,\s*(\d+)\)", code)
                if coord_m:
                    record["coordinate"] = [
                        int(coord_m.group(1)),
                        int(coord_m.group(2)),
                    ]
                # For type/key actions, redact the text value — log only length
                if action in _SENSITIVE_ACTIONS:
                    text_m = re.search(r"""text\s*=\s*['"]([^'"]*)['"]""", code)
                    if text_m:
                        record["text_len"] = len(text_m.group(1))
                    else:
                        record["text_len"] = None
                records.append(record)
            # Also capture observe_desktop() calls
            if "observe_desktop(" in code:
                records.append(
                    {
                        "timestamp": msg.timestamp.isoformat()
                        if msg.timestamp
                        else None,
                        "action": "screenshot",
                        "source": "observe_desktop",
                    }
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
    structured summary of every computer() and observe_desktop() call, with
    typed/key text redacted to just its length.

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
    click.echo(f"{'Timestamp':<30} {'Conv':<25} {'Action':<25} Details")
    click.echo("-" * 100)
    for r in all_records:
        ts = (r.get("timestamp") or "")[:19]
        conv = (r.get("conversation") or "")[:24]
        action = r.get("action", "")[:24]
        details = ""
        if "coordinate" in r:
            details = f"@ {r['coordinate']}"
        if "text_len" in r and r["text_len"] is not None:
            details += f" ({r['text_len']} chars, redacted)"
        if r.get("source") == "observe_desktop":
            details = "via observe_desktop()"
        click.echo(f"{ts:<30} {conv:<25} {action:<25} {details}")
