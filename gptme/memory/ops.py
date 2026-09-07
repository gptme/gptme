"""
Core operations for the memory store.

All operations work across the layered roots defined in roots.py.
"""

from __future__ import annotations

import fcntl
import re
import threading
from typing import TYPE_CHECKING

from .schema import MemoryEntry, parse_entry, write_entry

if TYPE_CHECKING:
    from pathlib import Path

    from .roots import ScopeType


_thread_lock = threading.Lock()


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-") or "memory"


def _lock_exclusive(f) -> None:
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
    except (ImportError, OSError):
        pass


def _unlock(f) -> None:
    try:
        fcntl.flock(f, fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass


def save_memory(
    name: str,
    content: str,
    description: str | None = None,
    memory_type: str = "general",
    keywords: list[str] | None = None,
    scope: ScopeType | None = None,
    workspace: Path | None = None,
    provenance: dict[str, str] | None = None,
) -> Path:
    """Save a memory entry, returning the path written.

    The content's first line is used as the description when description is
    not given explicitly.
    """
    from .roots import write_root

    root = write_root(scope=scope, workspace=workspace)
    root.mkdir(parents=True, exist_ok=True)

    slug = _slugify(name)
    lines = content.strip().splitlines()

    if description is None:
        description = lines[0].strip() if lines else name
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else content.strip()
    else:
        body = content.strip()

    entry = MemoryEntry(
        name=slug,
        description=description,
        body=body,
        metadata={"type": memory_type},
        keywords=keywords or [],
        provenance=provenance or {},
    )

    path = root / f"{slug}.md"
    write_entry(path, entry)
    _update_index(root, slug, f"{slug}.md", description)
    return path


def _update_index(memory_dir: Path, slug: str, filename: str, description: str) -> None:
    """Add or update the entry in MEMORY.md under an exclusive file lock."""
    index_path = memory_dir / "MEMORY.md"
    entry = f"- [{slug}]({filename}) — {description}\n"

    with _thread_lock, open(index_path, "a+", encoding="utf-8") as f:
        _lock_exclusive(f)
        try:
            f.seek(0)
            content = f.read()
            if not content:
                new_content = f"# Memory\n\n{entry}"
            else:
                pattern = rf"^- \[{re.escape(slug)}\]\({re.escape(filename)}\).*$"
                if re.search(pattern, content, re.MULTILINE):
                    new_content = re.sub(
                        pattern,
                        lambda _: entry.rstrip(),
                        content,
                        flags=re.MULTILINE,
                    )
                else:
                    if not content.endswith("\n"):
                        content += "\n"
                    new_content = content + entry
            f.seek(0)
            f.truncate()
            f.write(new_content)
        finally:
            _unlock(f)


def list_entries(
    roots: list[Path] | None = None,
    workspace: Path | None = None,
    include_superseded: bool = False,
) -> list[MemoryEntry]:
    """Return all memory entries from the given roots, deduplicating by slug.

    When the same slug appears in multiple roots the highest-priority root wins.
    """
    from .roots import resolve_roots

    if roots is None:
        roots = resolve_roots(workspace=workspace)

    seen_slugs: set[str] = set()
    entries: list[MemoryEntry] = []
    for root in roots:
        if not root.is_dir():
            continue
        for md in sorted(root.glob("*.md")):
            if md.name == "MEMORY.md":
                continue
            try:
                entry = parse_entry(md)
            except Exception:
                continue
            if entry.name in seen_slugs:
                continue
            if not include_superseded and not entry.is_living():
                continue
            seen_slugs.add(entry.name)
            entries.append(entry)

    return entries


def show_entry(
    name_or_slug: str,
    roots: list[Path] | None = None,
    workspace: Path | None = None,
) -> MemoryEntry | None:
    """Return the first entry matching name_or_slug, or None."""
    slug = _slugify(name_or_slug)
    for entry in list_entries(
        roots=roots, workspace=workspace, include_superseded=True
    ):
        if entry.name == slug or (entry.path and entry.path.stem == slug):
            return entry
    return None


def index_memory(
    roots: list[Path] | None = None,
    workspace: Path | None = None,
    budget: int = 200,
    check: bool = False,
) -> str:
    """Generate MEMORY.md index content from all living entries.

    Args:
        roots: Memory roots to scan. Defaults to resolve_roots().
        workspace: Workspace root for root resolution.
        budget: Maximum number of entries to include in the index.
        check: If True, verify the primary root's MEMORY.md is byte-stable
               and return "" if it is, or the new content if it would change.

    Returns:
        The generated MEMORY.md content as a string.
    """
    from .roots import resolve_roots

    if roots is None:
        roots = resolve_roots(workspace=workspace)

    entries = list_entries(roots=roots, workspace=workspace)
    entries = entries[:budget]

    lines: list[str] = ["# Memory\n"]
    for entry in entries:
        # Link is relative to the primary root (first root)
        if entry.path and roots and entry.path.parent == roots[0]:
            link = entry.path.name
        else:
            link = str(entry.path) if entry.path else f"{entry.name}.md"
        lines.append(f"- [{entry.name}]({link}) — {entry.description}")

    content = "\n".join(lines) + "\n"

    if check and roots:
        index_path = roots[0] / "MEMORY.md"
        if index_path.exists():
            existing = index_path.read_text(encoding="utf-8")
            if existing == content:
                return ""

    return content


def supersede(
    old_name: str,
    new_name: str,
    roots: list[Path] | None = None,
    workspace: Path | None = None,
) -> tuple[MemoryEntry, MemoryEntry] | None:
    """Mark old_name as superseded by new_name.

    Updates old entry's status and superseded_by, and new entry's supersedes.
    Returns (old_entry, new_entry) or None if either is not found.
    """
    old = show_entry(old_name, roots=roots, workspace=workspace)
    new = show_entry(new_name, roots=roots, workspace=workspace)
    if old is None or new is None:
        return None

    old.status = "superseded"
    old.superseded_by = new.name
    new.supersedes = old.name

    if old.path:
        write_entry(old.path, old)
    if new.path:
        write_entry(new.path, new)

    return old, new
