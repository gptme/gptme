"""CLI commands for gptme status — portable operator handoff document.

Provides ``gptme-util status`` (via lazy registration in ``util.py``) and
``gptme-status`` (standalone entry point registered in ``pyproject.toml``).

Produces a compact, human-readable briefing: recent commits, disk usage, and
any extra sections contributed by installed :class:`~gptme.status_provider.StatusProvider`
plugins registered under the ``gptme.status_providers`` entry-point group.

Agent- or workspace-specific status fields (task queues, service health,
blockers, journal entries) live in dedicated packages that register a provider.
Core only collects generic information that is useful to any gptme user.
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

from ..status_provider import StatusProvider, load_providers
from ..util.git_cmd import GIT_CMD

logger = logging.getLogger(__name__)


# ── generic helpers ────────────────────────────────────────────────────


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
    """Fetch open PR counts for the given repos.

    This is a generic helper exposed for use by :class:`~gptme.status_provider.StatusProvider`
    implementations.  Core ``build_document`` / ``_status_data`` do **not** call
    it with any hardcoded repo list.
    """
    if not repos:
        return []
    if author is None:
        author = _run(["gh", "api", "user", "--jq", ".login"], timeout=10) or ""
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
        rows.append({"repo": repo, "count": len(prs), "cap": cap})
    return rows


def _pr_queue_display(count: int, cap: int | None) -> str:
    """Format a PR count as a human-readable string with optional cap."""
    if cap is not None:
        return f"{count}/{cap}" + (" ⚠ at limit" if count >= cap else "")
    return str(count)


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
    """Escape dynamic values for a single markdown table cell.

    Lists and dicts are serialised as compact JSON so the cell content is
    machine-parseable rather than a raw Python repr.
    """
    structured = isinstance(value, (list, dict))
    if structured:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            # U+2028 (LINE SEPARATOR) and U+2029 (PARAGRAPH SEPARATOR) are not
            # escaped by json.dumps(ensure_ascii=False), but they act as
            # newlines in every line-oriented parser — including markdown table
            # renderers and str.splitlines().  Replace them with their JSON
            # \uXXXX escape forms so that the cell is safe for all consumers
            # while still round-tripping correctly through json.loads.
            text = text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        except (TypeError, ValueError):
            text = str(value)
            structured = False
    else:
        text = str(value)
    # Collapse newlines in plain-text values.  Skip for structured output:
    # JSON is compact and single-line after the U+2028/U+2029 escaping above.
    if not structured:
        text = " ".join(text.splitlines()).strip()
    else:
        text = text.strip()
    if not text:
        return "none"
    # Markdown's ``\|`` escape is invalid inside JSON strings.  A JSON unicode
    # escape hides the delimiter from the Markdown parser while preserving the
    # value when a consumer decodes the cell.
    return text.replace("|", r"\u007c" if structured else r"\|")


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


# ── core sections ──────────────────────────────────────────────────────


def section_header() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    model = os.environ.get("CLAUDE_MODEL", os.environ.get("CC_MODEL", "unknown"))
    agent_name = os.environ.get("GPTME_AGENT_NAME", "")
    agent_part = f" | **Agent**: {agent_name}" if agent_name else ""
    return f"# gptme Status — {now}\n\n**Model**: {model}{agent_part}\n"


def section_active_work() -> str:
    lines = ["## Active Work"]
    commits = _recent_commits(3)
    if commits:
        lines.append("- **Recent commits** (last 3):")
        for commit in commits:
            sha, _, msg = commit.partition(" ")
            lines.append(f"  - `{sha}` {msg[:65]}")
    else:
        lines.append("- No recent commits")
    return "\n".join(lines)


def section_disk() -> str:
    root = _git_root()
    disk = _disk_usage(root)
    return f"## Disk\n\n- **Usage**: {disk}"


# ── build ─────────────────────────────────────────────────────────────


def _provider_name(provider: StatusProvider) -> str:
    """Return the provider's name without raising.

    Defense-in-depth companion to the name probe in :func:`~gptme.status_provider.load_providers`.
    If a provider's name property raises despite the load-time probe, this
    helper returns a safe fallback so that error-handler log lines never
    themselves raise and escape isolation.
    """
    try:
        return provider.name
    except Exception:
        return "<unnamed-provider>"


_CORE_KEYS = frozenset({"timestamp", "session_id", "recent_commits", "disk_usage"})
"""Reserved top-level keys owned by gptme core.

Providers must not use these names; any collision is logged and skipped so that
core fields are never silently overwritten.
"""


def _json_default(obj: object) -> object:
    """Fallback JSON serialiser for non-standard types returned by providers.

    Converts unknown objects to their ``str()`` representation, using a fixed
    placeholder when the object's string conversion itself fails.  This ensures
    that a provider returning an object with a broken ``__str__()`` cannot abort
    ``gptme-util status --json``.
    """
    try:
        return str(obj)
    except Exception:
        return "<unserializable>"


def _sanitize_nested_dict_keys(obj: object) -> object:
    """Recursively convert non-string keys in nested dicts to their ``str()`` form.

    :func:`_status_data` already drops top-level provider keys that are not
    strings, but a provider value can itself be a :class:`dict` whose *nested*
    keys are non-strings.  ``json.dumps`` raises :exc:`TypeError` on such keys
    because the ``default=`` hook only handles non-serializable **values**, not
    invalid dict **keys**.

    This helper traverses :class:`dict`, :class:`list`, and :class:`tuple`
    values recursively, converting any non-string key to its ``str()``
    representation so the entire value tree is safe to pass to ``json.dumps``.
    Tuples are reconstructed as tuples so the original container type is
    preserved; ``json.dumps`` serialises them as JSON arrays, the same as lists.
    """
    if isinstance(obj, dict):
        return {
            (k if isinstance(k, str) else str(k)): _sanitize_nested_dict_keys(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_sanitize_nested_dict_keys(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_sanitize_nested_dict_keys(v) for v in obj)
    return obj


def _status_data(providers: list[StatusProvider] | None = None) -> dict[str, object]:
    """Collect status data shared by JSON and presentation renderers.

    Core fields are generic and workspace-agnostic.  Extra fields from installed
    :class:`~gptme.status_provider.StatusProvider` implementations are merged in
    at the top level, with collision detection against reserved core keys.

    Parameters
    ----------
    providers:
        Pre-loaded providers (for testing).  When ``None``, :func:`~gptme.status_provider.load_providers`
        is called automatically.
    """
    if providers is None:
        providers = load_providers()

    root = _git_root()
    status_data: dict[str, object] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": _session_id(),
        "recent_commits": _recent_commits(3),
        "disk_usage": _disk_usage(root),
    }

    for provider in providers:
        try:
            extra = provider.collect()
            for key, val in extra.items():
                if not isinstance(key, str):
                    logger.debug(
                        "Provider %r returned non-string key %r (type %s) — skipping;"
                        " JSON requires string keys",
                        _provider_name(provider),
                        key,
                        type(key).__name__,
                    )
                    continue
                if key in _CORE_KEYS:
                    logger.debug(
                        "Provider %r tried to overwrite reserved core key %r — skipping",
                        _provider_name(provider),
                        key,
                    )
                    continue
                if key in status_data:
                    logger.debug(
                        "Provider %r key %r collides with an earlier provider's key — skipping"
                        " (first-writer wins, consistent with table output)",
                        _provider_name(provider),
                        key,
                    )
                    continue
                # Sanitize nested dict keys: json.dumps requires all dict keys
                # at every nesting level to be strings, and default= only handles
                # non-serializable *values*, not invalid *keys*.  Isolate malformed
                # values to one field so valid fields from the provider survive.
                try:
                    status_data[key] = _sanitize_nested_dict_keys(val)
                except Exception as exc:
                    logger.debug(
                        "Provider %r value for key %r failed to sanitize: %s — skipping",
                        _provider_name(provider),
                        key,
                        exc,
                    )
        except Exception as exc:
            logger.debug(
                "Provider %r collect() failed: %s", _provider_name(provider), exc
            )

    return status_data


def build_json_status(providers: list[StatusProvider] | None = None) -> str:
    """Build the structured JSON status document."""
    return json.dumps(_status_data(providers), indent=2, default=_json_default)


def build_table_document(providers: list[StatusProvider] | None = None) -> str:
    """Build a machine-readable markdown table of session state."""
    if providers is None:
        providers = load_providers()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    session_id = _session_id()
    root = _git_root()

    commits = _recent_commits(1)
    last_commit = commits[0] if commits else "none"
    disk = _markdown_table_cell(_disk_usage(root))

    lines = [
        f"# gptme Status — {now}",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| session_id | `{_markdown_table_cell(session_id)}` |",
        f"| last_commit | `{_markdown_table_cell(last_commit)}` |",
        f"| disk_usage | {disk} |",
    ]

    # Track keys already in the table so providers cannot produce contradictory
    # duplicate rows.  Initialised with the core table fields plus the names
    # reserved for JSON output (_CORE_KEYS) — some differ (last_commit vs
    # recent_commits) so both sets are merged.
    seen_keys: set[str] = {"session_id", "last_commit", "disk_usage"} | _CORE_KEYS

    for provider in providers:
        try:
            extra = provider.collect()
            for key, val in extra.items():
                if not isinstance(key, str):
                    logger.debug(
                        "Provider %r returned non-string key %r (type %s) — skipping",
                        _provider_name(provider),
                        key,
                        type(key).__name__,
                    )
                    continue
                if key in seen_keys:
                    logger.debug(
                        "Provider %r key %r is already present in the status"
                        " table — skipping to prevent contradictory duplicate rows",
                        _provider_name(provider),
                        key,
                    )
                    continue
                seen_keys.add(key)
                try:
                    cell = _markdown_table_cell(val)
                except Exception:
                    cell = "<unserializable>"
                lines.append(f"| {key} | {cell} |")
        except Exception as exc:
            logger.debug(
                "Provider %r collect() failed in table: %s",
                _provider_name(provider),
                exc,
            )

    return "\n".join(lines)


def build_document(providers: list[StatusProvider] | None = None) -> str:
    """Build the narrative Markdown status document."""
    if providers is None:
        providers = load_providers()

    sections: list[str] = [
        section_header(),
        section_active_work(),
        section_disk(),
    ]

    for provider in providers:
        try:
            extra_sections = provider.narrative_sections()
            if not isinstance(extra_sections, list):
                logger.debug(
                    "Provider %r narrative_sections() returned %r, expected list[str]; skipping",
                    _provider_name(provider),
                    type(extra_sections).__name__,
                )
            else:
                valid = [s for s in extra_sections if isinstance(s, str)]
                if len(valid) < len(extra_sections):
                    logger.debug(
                        "Provider %r narrative_sections() returned %d non-string item(s); dropped",
                        _provider_name(provider),
                        len(extra_sections) - len(valid),
                    )
                sections.extend(valid)
        except Exception as exc:
            logger.debug(
                "Provider %r narrative_sections() failed: %s",
                _provider_name(provider),
                exc,
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

    Produces a compact briefing: recent commits, disk usage, and any extra
    sections contributed by installed StatusProvider plugins.

    Extra status fields (task queues, service health, journal entries) are
    provided by installed packages that register a ``gptme.status_providers``
    entry point — no workspace auto-detection, no cwd code loading.

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
