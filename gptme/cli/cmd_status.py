"""CLI commands for gptme status — portable operator handoff document.

Provides ``gptme-util status`` (via lazy registration in ``util.py``) and
``gptme-status`` (standalone entry point registered in ``pyproject.toml``).

Produces a compact, human-readable briefing: active work, PR queue, service
health, blockers, and ready backlog items.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

import click

from ..util.git_cmd import GIT_CMD

logger = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────────────────


def _run(cmd: list[str], *, timeout: int = 10) -> str:
    try:
        return subprocess.check_output(
            cmd, text=True, stderr=subprocess.DEVNULL, timeout=timeout
        ).strip()
    except Exception:
        return ""


def _git_root() -> Path | None:
    """Return the git root for the current working directory."""
    raw = _run([GIT_CMD, "rev-parse", "--show-toplevel"])
    return Path(raw) if raw else None


def _gh_user() -> str:
    """Return the current gh CLI authenticated username."""
    return _run(["gh", "api", "user", "--jq", ".login"], timeout=10)


def _is_bob_workspace() -> bool:
    """Detect if we are inside Bob's workspace by checking for Bob-specific files."""
    root = _git_root()
    if not root:
        return False
    return (root / "tasks").is_dir() and (root / "gptme.toml").is_file()


def _active_tasks(lines: int = 3) -> list[dict]:
    """Parse gptodo status --compact output for active tasks (Bob workspace)."""
    raw = _run(["gptodo", "status", "--compact"], timeout=15)
    if not raw:
        return []
    tasks: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        # Lines look like "  task-id  Title text here  (N ago)"
        if not line or line.startswith("📋") or "0 tasks" in line or "Summary" in line:
            continue
        parts = line.split(None, 1)
        if len(parts) >= 2:
            task_id = parts[0]
            # Strip trailing " (N unit ago)" timestamp to get the full title
            title = re.sub(r"\s+\(\d+\s+\w+\s+ago\)\s*$", "", parts[1]).strip()
            tasks.append({"_id": task_id, "title": title})
    return tasks[:lines]


def _recent_commits(n: int = 3) -> list[str]:
    raw = _run([GIT_CMD, "log", "--oneline", f"-{n}", "--no-merges"])
    return raw.splitlines() if raw else []


class _PRQueueRow(TypedDict):
    repo: str
    count: int
    cap: int | None


def _pr_queue(
    repos: list[tuple[str, int | None]], author: str | None = None
) -> list[_PRQueueRow]:
    if author is None:
        author = _gh_user() or "TimeToBuildBob"
    rows: list[_PRQueueRow] = []
    for repo, cap in repos:
        prs_json = _run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--author",
                author,
                "--state",
                "open",
                "--json",
                "number,title",
            ],
            timeout=15,
        )
        if not prs_json:
            continue
        try:
            prs = json.loads(prs_json)
        except json.JSONDecodeError:
            continue
        count = len(prs)
        rows.append({"repo": repo, "count": count, "cap": cap})
    return rows


def _pr_queue_display(count: int, cap: int | None) -> str:
    """Format a PR count as a human-readable string with optional cap."""
    if cap is not None:
        return f"{count}/{cap}" + (" ⚠ at limit" if count >= cap else "")
    return str(count)


def _service_status() -> list[dict[str, str]]:
    services = [
        ("Operator loop", "bob-operator-loop.service"),
        ("Autonomous", "bob-autonomous.service"),
    ]
    results: list[dict[str, str]] = []
    for label, unit in services:
        status = _run(["systemctl", "--user", "is-active", unit])
        icon = "✓" if status == "active" else ("⚠" if status == "activating" else "✗")
        results.append({"label": label, "icon": icon, "status": status})
    return results


def _dead_timers() -> int:
    out = _run(["systemctl", "--user", "list-timers", "--all"])
    return sum(
        1
        for line in out.splitlines()
        if "dead" in line.lower() and "bob-" in line.lower()
    )


def _blockers(limit: int = 3) -> list[dict]:
    raw = _run(["gptodo", "ready", "--state", "waiting", "--jsonl"], timeout=15)
    if not raw:
        return []
    blockers: list[dict] = []
    for line in raw.splitlines():
        try:
            t = json.loads(line)
        except json.JSONDecodeError:
            continue
        if t.get("waiting_for"):
            blockers.append(t)
    return blockers[:limit]


def _ready_tasks(limit: int = 3) -> list[dict]:
    raw = _run(["gptodo", "ready", "--state", "backlog", "--jsonl"], timeout=15)
    if not raw:
        return []
    tasks: list[dict] = []
    for line in raw.splitlines():
        try:
            t = json.loads(line)
        except json.JSONDecodeError:
            continue
        if t.get("waiting_for") or t.get("wait"):
            continue
        tasks.append(t)
    return tasks[:limit]


def _session_id() -> str:
    """Return the current session ID from environment variables."""
    for key in (
        "GPTME_SESSION_ID",
        "BOB_SESSION_ID",
        "SESSION_ID",
        "GIT_COMMITTER_SESSION_ID",
    ):
        val = os.environ.get(key)
        if val:
            return val
    return "none"


def _disk_usage(path: Path | None = None) -> str:
    """Return human-readable usage for the filesystem containing the path."""
    target = path or Path.cwd()
    try:
        usage = shutil.disk_usage(target)
        total_gb = usage.total / (1024**3)
        used_gb = usage.used / (1024**3)
        percent = (usage.used / usage.total) * 100
        return f"{used_gb:.1f}G / {total_gb:.1f}G ({percent:.0f}%)"
    except Exception:
        return "unknown"


def _markdown_table_cell(value: object) -> str:
    """Escape dynamic values for a single markdown table cell."""
    text = " ".join(str(value).splitlines()).strip()
    if not text:
        return "none"
    return text.replace("|", r"\|")


def _journal_entries(limit: int = 5) -> list[str]:
    """Return the last N journal entry filenames (Bob workspace)."""
    root = _git_root()
    if not root:
        return []
    journal_dir = root / "journal"
    if not journal_dir.is_dir():
        return []
    entries: list[Path] = []
    for day_dir in sorted(journal_dir.iterdir(), reverse=True):
        if not day_dir.is_dir():
            continue
        day_entries = [
            entry.relative_to(root)
            for entry in sorted(day_dir.iterdir(), reverse=True)
            if entry.is_file() and entry.suffix == ".md"
        ]
        entries.extend(day_entries)
        if len(entries) >= limit:
            break
    return [str(e) for e in entries[:limit]]


def _strip_markdown(doc: str) -> str:
    """Strip Markdown formatting for plain-text output."""
    lines = []
    for line in doc.splitlines():
        line = re.sub(r"^#+\s+", "", line)  # Remove headings
        line = re.sub(r"\*+([^*]*)\*+", r"\1", line)  # Remove bold/italic
        line = re.sub(r"`([^`]*)`", r"\1", line)  # Remove inline code
        if re.match(r"^[|\s\-:]+$", line):  # Skip table dividers
            continue
        lines.append(line)
    return "\n".join(lines)


# ── sections ──────────────────────────────────────────────────────────


def section_header() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    model = os.environ.get("CLAUDE_MODEL", os.environ.get("CC_MODEL", "unknown"))
    agent_name = os.environ.get("GPTME_AGENT_NAME", "")
    agent_part = f" | **Agent**: {agent_name}" if agent_name else ""
    return f"# gptme Status — {now}\n\n**Model**: {model}{agent_part}\n"


def section_active_work(is_bob: bool = False) -> str:
    lines = ["## Active Work"]
    if is_bob:
        tasks = _active_tasks(3)
        if tasks:
            lines.extend(
                f"- **Task**: `{t['_id']}` — {t.get('title', '')[:60]}" for t in tasks
            )
    commits = _recent_commits(3)
    if commits:
        lines.append("- **Recent commits** (last 3):")
        for commit in commits:
            sha, _, msg = commit.partition(" ")
            lines.append(f"  - `{sha}` {msg[:65]}")
    return "\n".join(lines)


def section_pr_queue() -> str:
    lines = ["## PR Queue"]
    rows = _pr_queue(_TRACKED_REPOS)
    if rows:
        lines.append("| Repo | Open |")
        lines.append("|------|------|")
        for row in rows:
            display = _pr_queue_display(row["count"], row["cap"])
            lines.append(f"| {row['repo']} | {display} |")
    else:
        lines.append("- Unable to fetch PR data")
    return "\n".join(lines)


def section_services() -> str:
    lines = ["## Services"]
    services = _service_status()
    lines.extend(f"- {svc['label']}: {svc['icon']} {svc['status']}" for svc in services)
    dead = _dead_timers()
    if dead:
        lines.append(f"- ⚠ {dead} dead bob-* timer(s)")
    return "\n".join(lines)


def section_blockers() -> str:
    lines = ["## Top Blockers"]
    blockers = _blockers(3)
    if blockers:
        for t in blockers:
            wf = str(t.get("waiting_for", "")).split("\n")[0][:70]
            since = t.get("waiting_since", "")
            since_str = f" (since {since})" if since else ""
            lines.append(f"- `{t['id']}`: {wf}{since_str}")
    else:
        lines.append("- No active blockers with waiting_for set")
    return "\n".join(lines)


def section_ready_next() -> str:
    lines = ["## Ready Next (top 3)"]
    ready = _ready_tasks(3)
    if ready:
        for i, t in enumerate(ready, 1):
            title = str(t.get("name", t.get("id", "")))[:65]
            lines.append(f"{i}. `{t['id']}` — {title}")
    else:
        lines.append("- No ready backlog tasks found")
    return "\n".join(lines)


# ── build ─────────────────────────────────────────────────────────────


_TRACKED_REPOS: list[tuple[str, int | None]] = [
    ("gptme/gptme", 10),
    ("gptme/gptme-cloud", 3),
    ("ErikBjare/bob", None),
    ("gptme/gptme-contrib", None),
]


def _status_data() -> dict[str, object]:
    """Collect status data shared by JSON and presentation renderers."""
    is_bob = _is_bob_workspace()
    root = _git_root()
    status_data: dict[str, object] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": _session_id(),
        "active_tasks": [
            {"id": task.get("_id", task.get("id", "")), "title": task.get("title", "")}
            for task in _active_tasks(3)
        ],
        "recent_commits": _recent_commits(3),
        "pr_queue": _pr_queue(_TRACKED_REPOS),
        "disk_usage": _disk_usage(root),
        "journal_entries": _journal_entries(5),
    }
    if is_bob:
        status_data.update(
            services=_service_status(),
            dead_timers=_dead_timers(),
            blockers=_blockers(3),
            ready_tasks=_ready_tasks(3),
        )
    return status_data


def build_json_status() -> str:
    """Build the structured JSON status document."""
    return json.dumps(_status_data(), indent=2)


def build_table_document() -> str:
    """Build a machine-readable markdown table of session state."""
    is_bob = _is_bob_workspace()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    session_id = _session_id()
    root = _git_root()

    # active_task
    active = _active_tasks(1)
    active_task = active[0].get("_id", "none") if active else "none"

    # last_commit
    commits = _recent_commits(1)
    last_commit = commits[0] if commits else "none"

    # pending_prs
    pr_rows = _pr_queue(_TRACKED_REPOS)
    pending_prs = (
        ", ".join(
            f"{r['repo']}:{_pr_queue_display(r['count'], r['cap'])}" for r in pr_rows
        )
        if pr_rows
        else "none"
    )

    # waiting_for
    blockers = _blockers(1)
    waiting_for = (
        _markdown_table_cell(blockers[0].get("waiting_for", "none"))
        if blockers
        else "none"
    )

    # disk_usage
    disk = _markdown_table_cell(_disk_usage(root))

    # journal_entries
    journals = _journal_entries(5)
    journal_str = _markdown_table_cell(", ".join(journals) if journals else "none")

    lines = [
        f"# gptme Status — {now}",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| session_id | `{_markdown_table_cell(session_id)}` |",
        f"| active_task | `{_markdown_table_cell(active_task)}` |",
        f"| last_commit | `{_markdown_table_cell(last_commit)}` |",
        f"| pending_prs | {_markdown_table_cell(pending_prs)} |",
        f"| waiting_for | {waiting_for} |",
        f"| disk_usage | {disk} |",
        f"| journal_entries | {journal_str} |",
    ]

    if is_bob:
        services = _service_status()
        svc_str = _markdown_table_cell(
            ", ".join(f"{s['label']}={s['status']}" for s in services)
        )
        lines.append(f"| services | {svc_str} |")
        dead = _dead_timers()
        if dead:
            lines.append(f"| dead_timers | {dead} |")

    return "\n".join(lines)


def build_document() -> str:
    is_bob = _is_bob_workspace()
    sections: list[str] = [
        section_header(),
        section_active_work(is_bob=is_bob),
        section_pr_queue(),
    ]
    if is_bob:
        sections.extend(
            [
                section_services(),
                section_blockers(),
                section_ready_next(),
            ]
        )
    doc = "\n\n".join(sections)
    token_est = len(doc) // 4
    doc += f"\n\n---\n*~{token_est} tokens*"
    return doc


# ── click command ─────────────────────────────────────────────────────


@click.command("status")
@click.option(
    "--write",
    is_flag=True,
    help="Write status document to status.md in repo root.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(writable=True),
    default=None,
    metavar="FILE",
    help="Output file path (implies --write).",
)
@click.option(
    "--markdown/--no-markdown",
    default=True,
    help="Output as Markdown (default: enabled). Use --no-markdown for plain text.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["narrative", "table"], case_sensitive=False),
    default="narrative",
    help="Output format: narrative (default) or table.",
)
@click.option("--json", "as_json", is_flag=True, help="Output status as JSON.")
def status(
    write: bool,
    output: str | None,
    markdown: bool,
    output_format: str,
    as_json: bool,
) -> None:
    """Generate a portable operator handoff / session-status document.

    Produces a compact briefing: active work, PR queue, service health,
    blockers, and ready-next tasks.

    \b
    Examples:

        gptme-util status                       # stdout

        gptme-util status --write               # write to status.md

        gptme-util status -o /tmp/handoff.md    # write to custom path

        gptme-util status --no-markdown         # plain-text output

        gptme-util status --format table        # compact Markdown table

        gptme-util status --json                # structured JSON
    """
    if as_json and (
        not markdown or output_format != "narrative" or (write and not output)
    ):
        raise click.UsageError(
            "--json cannot be combined with --no-markdown, --format, or --write"
            " (use -o/--output to write JSON to a file)"
        )

    if as_json:
        doc = build_json_status()
    elif output_format == "table":
        doc = build_table_document()
    else:
        doc = build_document()
    if not markdown:
        doc = _strip_markdown(doc)
    out_path: Path | None = None

    if output:
        out_path = Path(output)
    elif write:
        root = _git_root()
        out_path = (root or Path.cwd()) / "status.md"

    if out_path:
        out_path.write_text(doc, encoding="utf-8")
        click.echo(f"Written to {out_path}")
    else:
        click.echo(doc)


if __name__ == "__main__":
    status()  # pragma: no cover
