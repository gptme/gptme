"""
gptme-util knowledge — cross-session knowledge base CLI.

Saves and retrieves problem/resolution pairs backed by JSONL storage at
``~/.local/share/gptme/knowledge/entries.jsonl``.

When ``gptme-rag`` is available the knowledge directory is also re-indexed
after each ``save`` so semantic search stays current.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from pathlib import Path


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
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(entry, indent=2))
    else:
        click.echo(f"Saved knowledge entry {entry['id'][:8]}")
        if entry["tags"]:
            click.echo(f"  Tags: {', '.join(entry['tags'])}")

    # Re-index with gptme-rag regardless of output mode so the mirror stays
    # in sync whether the caller asked for JSON or human-readable output.
    if shutil.which("gptme-rag"):
        from ..knowledge import _knowledge_dir  # fmt: skip

        kb_dir = _knowledge_dir()
        # Export entries as markdown files that gptme-rag can index.
        _export_for_rag(kb_dir)
        try:
            subprocess.run(
                ["gptme-rag", "index", str(kb_dir / "rag")],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            click.echo(f"Warning: gptme-rag index failed: {e}", err=True)


def _export_for_rag(kb_dir: Path) -> None:
    """Write entries as markdown files under kb_dir/rag/ for gptme-rag indexing."""
    from ..knowledge import _load_entries  # fmt: skip

    rag_dir = kb_dir / "rag"
    rag_dir.mkdir(parents=True, exist_ok=True)
    entries = _load_entries()
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
    except ValueError as e:
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
            click.echo(f"    Tags: {', '.join(entry['tags'])}")
        click.echo(f"    Problem:    {entry['problem']}")
        click.echo(f"    Resolution: {entry['resolution']}")


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

    entries = knowledge_list(tags=list(tags) if tags else None, limit=limit)

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
        tags_str = f"  [{', '.join(entry['tags'])}]" if entry.get("tags") else ""
        click.echo(f"  {eid}  {date}{tags_str}")
        click.echo(f"    {entry['problem'][:80]}")


@knowledge.command("delete")
@click.argument("entry_id")
def knowledge_delete_cmd(entry_id: str):
    """Delete a knowledge entry by ID (or ID prefix)."""
    from ..knowledge import _load_entries, knowledge_delete  # fmt: skip

    # Support prefix matching
    entries = _load_entries()
    matches = [e for e in entries if e.get("id", "").startswith(entry_id)]
    if len(matches) > 1:
        click.echo(f"Ambiguous prefix '{entry_id}' — matches {len(matches)} entries:")
        for m in matches:
            click.echo(f"  {m['id']}")
        sys.exit(1)
    if not matches:
        click.echo(f"No entry found with ID or prefix '{entry_id}'")
        sys.exit(1)

    full_id = matches[0]["id"]
    if knowledge_delete(full_id):
        click.echo(f"Deleted entry {full_id[:8]}")
        # Remove the RAG mirror file so semantic retrieval doesn't return stale results.
        if shutil.which("gptme-rag"):
            from ..knowledge import _knowledge_dir  # fmt: skip

            mirror = _knowledge_dir() / "rag" / f"{full_id}.md"
            if mirror.exists():
                mirror.unlink()
                try:
                    subprocess.run(
                        ["gptme-rag", "index", str(_knowledge_dir() / "rag")],
                        check=True,
                        capture_output=True,
                        timeout=30,
                    )
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                    click.echo(
                        f"Warning: gptme-rag re-index after delete failed: {e}",
                        err=True,
                    )
    else:
        click.echo(f"Failed to delete entry {full_id[:8]}")
        sys.exit(1)
