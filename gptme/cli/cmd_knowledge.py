"""
gptme-util knowledge — cross-session knowledge base CLI.

Saves and retrieves knowledge entries backed by JSONL storage at
``~/.local/share/gptme/knowledge/entries.jsonl``.

Supported entry types:
  problem_resolution  (default)  Problem / Resolution
  decision                        Decision / Rationale
  fact                            Topic / Fact
  how_to                          Task / Steps
  note                            Title / Content

When ``gptme-rag`` is available the knowledge directory is also re-indexed
after each ``save`` so semantic search stays current.  Search prefers
gptme-rag's semantic ranking when available and falls back to keyword scoring.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from ..knowledge import KnowledgeEntry


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _strip_controls(value: str) -> str:
    """Strip terminal control characters from human-readable output."""
    return _CONTROL_CHARS_RE.sub("", value)


@click.group("knowledge")
def knowledge():
    """Cross-session knowledge base: save and retrieve knowledge entries."""


@knowledge.command("save")
@click.argument("primary")
@click.argument("secondary")
@click.option(
    "--tag",
    "-t",
    "tags",
    multiple=True,
    help="Tag to attach (repeatable: -t git -t pytest).",
)
@click.option(
    "--type",
    "-T",
    "entry_type",
    default=None,
    help=(
        "Entry type: problem_resolution (default), decision, fact, how_to, note. "
        "Controls how the entry is labelled in search results and session injection."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Print saved entry as JSON.")
def knowledge_save_cmd(
    primary: str,
    secondary: str,
    tags: tuple[str, ...],
    entry_type: str | None,
    as_json: bool,
):
    """Save a knowledge entry (PRIMARY / SECONDARY) to the knowledge base.

    The meaning of PRIMARY and SECONDARY depends on --type:

    \b
        problem_resolution  Problem / Resolution  (default)
        decision            Decision / Rationale
        fact                Topic / Fact
        how_to              Task / Steps
        note                Title / Content

    Example:

    \b
        gptme-util knowledge save \\
          "pytest discovers no tests despite test file existing" \\
          "The test function was not prefixed with test_; rename it." \\
          -t pytest -t testing

    \b
        gptme-util knowledge save \\
          "Switched auth library from X to Y" \\
          "X had no async support; Y supports both sync and async callers." \\
          --type decision
    """
    from ..knowledge import ENTRY_TYPES, knowledge_save  # fmt: skip

    if entry_type is None:
        entry_type = ENTRY_TYPES[0]
    if entry_type not in ENTRY_TYPES:
        click.echo(
            f"Error: invalid --type {entry_type!r}; choose one of: {', '.join(ENTRY_TYPES)}",
            err=True,
        )
        sys.exit(1)

    try:
        entry = knowledge_save(primary, secondary, list(tags), entry_type=entry_type)
    except (ValueError, OSError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(entry, indent=2))
    else:
        type_suffix = (
            f" [{_strip_controls(entry.get('entry_type', ''))}]"
            if entry.get("entry_type") != "problem_resolution"
            else ""
        )
        click.echo(f"Saved knowledge entry {entry['id'][:8]}{type_suffix}")
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
        from ..knowledge import _ENTRY_TYPE_LABELS, _resolved_entry_type  # fmt: skip

        for entry in entries:
            eid = entry.get("id", "unknown")
            fpath = rag_dir / f"{eid}.md"
            et = _resolved_entry_type(entry)
            primary_label, secondary_label = _ENTRY_TYPE_LABELS.get(
                et, ("Problem", "Resolution")
            )
            tags_line = ""
            if entry.get("tags"):
                tags_line = f"\n**Tags**: {', '.join(entry['tags'])}\n"
            content = (
                f"# Knowledge Entry\n\n"
                f"**{primary_label}**: {entry.get('problem', '')}\n\n"
                f"**{secondary_label}**: {entry.get('resolution', '')}\n"
                f"{tags_line}"
            )
            fpath.write_text(content, encoding="utf-8")


def _rag_search(
    query: str,
    top_k: int,
    rag_dir: Path,
) -> list[str] | None:
    """Search via gptme-rag and return ordered entry IDs, or None on unavailability/failure.

    Returns None (not an empty list) when gptme-rag is absent, rag_dir has no
    indexed files, or the subprocess fails.  An empty list means gptme-rag ran
    successfully but found no matches.
    """
    if not shutil.which("gptme-rag"):
        return None
    if not rag_dir.is_dir() or not any(rag_dir.glob("*.md")):
        return None
    try:
        result = subprocess.run(
            [
                "gptme-rag",
                "search",
                # Options must precede the ``--`` terminator; anything after it
                # is treated as a positional argument (query/paths), so a
                # trailing --json would be silently ignored.
                "--json",
                "--n-results",
                str(top_k),
                "--",
                query,
                str(rag_dir),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        results = data.get("results", [])
        if not isinstance(results, list):
            return None
        ids: list[str] = []
        for r in results:
            if not isinstance(r, dict):
                return None
            source = r.get("source", "")
            if not isinstance(source, str) or not source:
                continue
            stem = Path(source).stem
            if stem:
                ids.append(stem)
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        # Malformed/aliens-shaped output falls back to keyword search.
        return None
    return ids


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

    Uses gptme-rag semantic search when available; falls back to keyword search.
    RAG indexing is asynchronous after save/delete, so a search inside the
    staleness window falls back to keyword search rather than returning an
    empty result.

    Example:

    \b
        gptme-util knowledge search "pytest test discovery"
    """
    from ..knowledge import (  # fmt: skip
        _knowledge_dir,
        knowledge_list,
        knowledge_search,
    )

    try:
        kb_dir = _knowledge_dir()
        rag_dir = kb_dir / "rag"
        # Over-fetch when tag filtering is applied post-hoc, so filtered-out
        # entries can be backfilled from deeper in the rag ranking.
        rag_fetch_k = top_k * 5 if tags else top_k
        rag_ids = _rag_search(query, rag_fetch_k, rag_dir)

        if rag_ids is not None:
            # Build an ID-indexed map from the FULL JSONL store: RAG may rank
            # any entry, not just the newest `knowledge_list()` default page.
            all_entries = knowledge_list(limit=sys.maxsize)
            entry_map = {e["id"]: e for e in all_entries}
            # Preserve rag ranking order; apply tag filter post-hoc, then
            # truncate to top_k. The rag CLI has no metadata filter, so the
            # tag filter can only run on the entries rag returned.
            tag_set = {t.strip().lower() for t in tags if t.strip()} if tags else set()

            def _filter(ids: list[str]) -> list[KnowledgeEntry]:
                out = []
                for eid in ids:
                    entry = entry_map.get(eid)
                    if entry is None:
                        continue
                    if tag_set and not tag_set.issubset(
                        {t.lower() for t in entry.get("tags", [])}
                    ):
                        continue
                    out.append(entry)
                    if len(out) >= top_k:
                        break
                return out

            results = _filter(rag_ids)

            # Fixed over-fetch cannot *guarantee* top_k: eligible tagged
            # entries ranked below the page are never seen. When the first
            # page was truncated (rag filled the request) and the filter still
            # cannot fill top_k, re-query the whole index, whose size bounds
            # how many entries exist to rank. If rag returns fewer than asked,
            # the index is exhausted and a wider query cannot help.
            if tag_set and len(results) < top_k and len(rag_ids) >= rag_fetch_k:
                total_indexed = sum(1 for _ in rag_dir.glob("*.md"))
                if total_indexed > rag_fetch_k:
                    wider = _rag_search(query, total_indexed, rag_dir)
                    if wider is not None:
                        results = _filter(wider)
        else:
            results = knowledge_search(
                query, top_k=top_k, tags=list(tags) if tags else None
            )

        # RAG succeeded but returned no live entries: the index may be empty,
        # stale (async re-index after save/delete, #3691), or the returned IDs
        # no longer map to JSONL entries. A successful-but-empty RAG result is
        # not authoritative — fall back to keyword search so a search inside
        # the staleness window still returns matching entries.
        if rag_ids is not None and not results:
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

    from ..knowledge import _ENTRY_TYPE_LABELS, _resolved_entry_type  # fmt: skip

    for i, entry in enumerate(results, 1):
        et = _resolved_entry_type(entry)
        primary_label, secondary_label = _ENTRY_TYPE_LABELS.get(
            et, ("Problem", "Resolution")
        )
        type_str = f"  ({_strip_controls(et)})" if et != "problem_resolution" else ""
        click.echo(
            f"\n[{i}] {entry['id'][:8]}  {entry.get('created_at', '')[:10]}{type_str}"
        )
        if entry.get("tags"):
            click.echo(f"    Tags: {_strip_controls(', '.join(entry['tags']))}")
        click.echo(f"    {primary_label}:    {_strip_controls(entry['problem'])}")
        click.echo(f"    {secondary_label}: {_strip_controls(entry['resolution'])}")


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

    from ..knowledge import _resolved_entry_type  # fmt: skip

    click.echo(f"Knowledge base ({len(entries)} entries):\n")
    for entry in entries:
        eid = entry.get("id", "")[:8]
        date = entry.get("created_at", "")[:10]
        et = _resolved_entry_type(entry)
        type_str = f"  ({_strip_controls(et)})" if et != "problem_resolution" else ""
        tags_str = (
            f"  [{_strip_controls(', '.join(entry['tags']))}]"
            if entry.get("tags")
            else ""
        )
        click.echo(f"  {eid}  {date}{type_str}{tags_str}")
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
