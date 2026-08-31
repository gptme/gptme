"""
gptme-util knowledge — cross-session knowledge base CLI.

Saves and retrieves problem/resolution pairs backed by JSONL storage at
``~/.local/share/gptme/knowledge/entries.jsonl``.

When ``gptme-rag`` is available the knowledge directory is also re-indexed
after each ``save`` so semantic search stays current.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from pathlib import Path


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _strip_controls(value: str) -> str:
    """Strip terminal control characters from human-readable output."""
    return _CONTROL_CHARS_RE.sub("", value)


@click.group("knowledge")
def knowledge():
    """Cross-session knowledge base: save and retrieve problem/resolution pairs."""


@knowledge.command("save")
@click.argument("problem")
@click.argument("resolution")
@click.option(
    "--tag",
    "-t",
    "tags",
    multiple=True,
    help="Tag to attach (repeatable: -t git -t pytest).",
)
@click.option("--json", "as_json", is_flag=True, help="Print saved entry as JSON.")
def knowledge_save_cmd(
    problem: str, resolution: str, tags: tuple[str, ...], as_json: bool
):
    """Save a PROBLEM/RESOLUTION pair to the knowledge base.

    Example:

    \b
        gptme-util knowledge save \\
          "pytest discovers no tests despite test file existing" \\
          "The test function was not prefixed with test_; rename it." \\
          -t pytest -t testing
    """
    from ..knowledge import knowledge_save  # fmt: skip

    try:
        entry = knowledge_save(problem, resolution, list(tags))
    except (ValueError, OSError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(entry, indent=2))
    else:
        click.echo(f"Saved knowledge entry {entry['id'][:8]}")
        if entry["tags"]:
            click.echo(
                f"  Tags: {', '.join(_strip_controls(t) for t in entry['tags'])}"
            )

    # Re-index with gptme-rag regardless of output mode so the mirror stays
    # in sync whether the caller asked for JSON or human-readable output.
    if shutil.which("gptme-rag"):
        from ..knowledge import _knowledge_dir  # fmt: skip

        kb_dir = _knowledge_dir()
        # Export entries as markdown files that gptme-rag can index.
        # Wrap in OSError handler: the entry is already persisted; a non-writable
        # rag directory should warn, not crash and mislead the user.
        try:
            _export_for_rag(kb_dir)
        except (OSError, ValueError) as e:
            click.echo(f"Warning: gptme-rag mirror export failed: {e}", err=True)
        else:
            # Fire-and-forget: the entry is already persisted in JSONL, so
            # keyword search works immediately.  gptme-rag indexing is a
            # semantic-search enhancement; blocking the CLI for it (up to
            # the full 30 s timeout) is a poor UX trade-off.
            try:
                subprocess.Popen(
                    ["gptme-rag", "index", str(kb_dir / "rag")],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                )
            except OSError as e:
                click.echo(f"Warning: could not start gptme-rag: {e}", err=True)


def _export_for_rag(kb_dir: Path) -> None:
    """Write entries as markdown files under kb_dir/rag/ for gptme-rag indexing.

    Loads the entry snapshot under the exclusive lock so the orphan sweep and
    mirror writes reflect the same consistent view of the JSONL file, preventing
    a concurrent delete from being resurrected by a stale export snapshot.
    """
    from ..knowledge import _exclusive_lock, _load_entries  # fmt: skip

    rag_dir = kb_dir / "rag"
    rag_dir.mkdir(parents=True, exist_ok=True)
    with _exclusive_lock():
        entries = _load_entries()
        live_ids = {e["id"] for e in entries}
        # Remove orphan mirror files left by prior deletions.
        for existing in rag_dir.glob("*.md"):
            if existing.stem not in live_ids:
                existing.unlink(missing_ok=True)
        for entry in entries:
            eid = entry.get("id", "unknown")
            fpath = rag_dir / f"{eid}.md"
            tags_line = ""
            if entry.get("tags"):
                tags_line = f"\n**Tags**: {', '.join(entry['tags'])}\n"
            content = (
                f"# Knowledge Entry\n\n"
                f"**Problem**: {entry.get('problem', '')}\n\n"
                f"**Resolution**: {entry.get('resolution', '')}\n"
                f"{tags_line}"
            )
            fpath.write_text(content, encoding="utf-8")


@knowledge.command("search")
@click.argument("query")
@click.option(
    "--top-k",
    default=5,
    show_default=True,
    type=click.IntRange(min=1),
    help="Number of results.",
)
@click.option("--tag", "-t", "tags", multiple=True, help="Filter by tag (repeatable).")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def knowledge_search_cmd(query: str, top_k: int, tags: tuple[str, ...], as_json: bool):
    """Search the knowledge base for QUERY.

    Example:

    \b
        gptme-util knowledge search "pytest test discovery"
    """
    from ..knowledge import knowledge_search  # fmt: skip

    try:
        results = knowledge_search(
            query, top_k=top_k, tags=list(tags) if tags else None
        )
    except (ValueError, OSError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(results, indent=2))
        return

    if not results:
        click.echo("No matching entries found.")
        return

    for i, entry in enumerate(results, 1):
        click.echo(f"\n[{i}] {entry['id'][:8]}  {entry.get('created_at', '')[:10]}")
        if entry.get("tags"):
            click.echo(f"    Tags: {_strip_controls(', '.join(entry['tags']))}")
        click.echo(f"    Problem:    {_strip_controls(entry['problem'])}")
        click.echo(f"    Resolution: {_strip_controls(entry['resolution'])}")


@knowledge.command("list")
@click.option("--tag", "-t", "tags", multiple=True, help="Filter by tag (repeatable).")
@click.option(
    "--limit",
    default=20,
    show_default=True,
    type=click.IntRange(min=1),
    help="Maximum entries.",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def knowledge_list_cmd(tags: tuple[str, ...], limit: int, as_json: bool):
    """List knowledge entries, newest first."""
    from ..knowledge import knowledge_list  # fmt: skip

    try:
        entries = knowledge_list(tags=list(tags) if tags else None, limit=limit)
    except (OSError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(entries, indent=2))
        return

    if not entries:
        click.echo("No entries in knowledge base.")
        return

    click.echo(f"Knowledge base ({len(entries)} entries):\n")
    for entry in entries:
        eid = entry.get("id", "")[:8]
        date = entry.get("created_at", "")[:10]
        tags_str = (
            f"  [{_strip_controls(', '.join(entry['tags']))}]"
            if entry.get("tags")
            else ""
        )
        click.echo(f"  {eid}  {date}{tags_str}")
        click.echo(f"    {_strip_controls(entry['problem'][:80])}")


@knowledge.command("delete")
@click.argument("entry_id")
def knowledge_delete_cmd(entry_id: str):
    """Delete a knowledge entry by ID (or ID prefix)."""
    from ..knowledge import knowledge_delete_by_prefix  # fmt: skip

    # Prefix resolution and delete are done atomically under the exclusive lock
    # so a concurrent save or delete cannot change the entry set between the
    # prefix lookup and the actual write.
    try:
        full_id, status, matches = knowledge_delete_by_prefix(entry_id)
    except (ValueError, OSError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    safe_id = _strip_controls(entry_id)
    if status == "ambiguous":
        click.echo(f"Ambiguous prefix '{safe_id}' — matches {len(matches)} entries:")
        for m in matches:
            click.echo(f"  {m['id']}")
        sys.exit(1)
    if status == "not_found":
        click.echo(f"No entry found with ID or prefix '{safe_id}'")
        sys.exit(1)

    # status == 'deleted'
    assert full_id is not None
    click.echo(f"Deleted entry {full_id[:8]}")
    from ..knowledge import _knowledge_dir  # fmt: skip

    # Always remove the mirror file regardless of whether gptme-rag is
    # installed — the file lives on disk independently and must be cleaned
    # up so that a later install of gptme-rag does not index stale entries.
    # Use missing_ok=True to avoid a TOCTOU race: _export_for_rag's orphan
    # sweep (inside the exclusive lock) can unlink the same file concurrently.
    # Catch OSError: the entry is already deleted; a permission or I/O error
    # on the mirror should warn, not crash and confuse the user about success.
    mirror = _knowledge_dir() / "rag" / f"{full_id}.md"
    mirror_removed = True
    try:
        mirror.unlink(missing_ok=True)
    except OSError as e:
        mirror_removed = False
        click.echo(f"Warning: could not remove mirror file {mirror}: {e}", err=True)
    # Re-index only when the mirror is clean and the rag directory actually
    # exists. unlink(missing_ok=True) succeeds when gptme-rag was never
    # installed, so indexing a missing directory would only emit a misleading
    # "re-index failed" warning after a successful JSONL delete.
    rag_dir = _knowledge_dir() / "rag"
    if mirror_removed and shutil.which("gptme-rag") and rag_dir.is_dir():
        # Fire-and-forget: re-indexing after delete is best-effort; the JSONL
        # store is already consistent and keyword search will not return the
        # deleted entry.  Don't block the CLI on the RAG index operation.
        try:
            subprocess.Popen(
                ["gptme-rag", "index", str(rag_dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except OSError as e:
            click.echo(
                f"Warning: could not start gptme-rag re-index after delete: {e}",
                err=True,
            )
