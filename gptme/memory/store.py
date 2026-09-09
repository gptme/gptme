"""Layered memory store: union reads across roots, scoped writes, index generation."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

from .roots import MemoryRoot, default_write_root, resolve_roots
from .schema import (
    DEFAULT_TYPE,
    TYPE_ORDER,
    MemoryEntry,
    MemoryParseError,
    is_index_file,
    parse_entry,
    slugify,
)

logger = logging.getLogger(__name__)

INDEX_FILENAME = "MEMORY.md"
INDEX_HEADER = "# Persistent Memory"

try:
    import fcntl as _fcntl

    def _lock_exclusive(f) -> None:
        _fcntl.flock(f, _fcntl.LOCK_EX)

    def _unlock(f) -> None:
        _fcntl.flock(f, _fcntl.LOCK_UN)

except ImportError:  # Windows: no flock, best effort

    def _lock_exclusive(f) -> None:
        pass

    def _unlock(f) -> None:
        pass


def update_index_line(memory_dir: Path, entry: MemoryEntry) -> None:
    """Upsert one ``- [title](file) — description`` line in ``MEMORY.md``.

    This is the Claude Code convention: append a pointer line, never rewrite
    the whole index. It is what ``save`` does so a hand-curated index is never
    clobbered; full regeneration is the explicit ``index --write`` path.

    The file name is the key, so renaming an entry's title updates the same
    line instead of adding a duplicate. An exclusive lock serialises
    concurrent writers.
    """
    index_path = memory_dir / INDEX_FILENAME
    line = entry.index_line()
    pattern = rf"^- \[[^\]]*\]\({re.escape(entry.filename)}\).*$"
    with open(index_path, "a+", encoding="utf-8") as f:
        _lock_exclusive(f)
        try:
            f.seek(0)
            content = f.read()
            if not content:
                new_content = f"{INDEX_HEADER}\n\n{line}\n"
            elif re.search(pattern, content, re.MULTILINE):
                new_content = re.sub(
                    pattern, lambda _: line, content, flags=re.MULTILINE
                )
            else:
                if not content.endswith("\n"):
                    content += "\n"
                new_content = content + line + "\n"
            f.seek(0)
            f.truncate()
            f.write(new_content)
        finally:
            _unlock(f)


class MemoryStore:
    """Reads union every root (nearest layer wins on name); writes target one scope."""

    def __init__(self, roots: Iterable[MemoryRoot]):
        self.roots = list(roots)
        self.errors: list[tuple[Path, str]] = []

    @classmethod
    def from_workspace(
        cls, workspace: Path | None = None, **kwargs: Any
    ) -> MemoryStore:
        return cls(resolve_roots(workspace, **kwargs))

    # -- reads -------------------------------------------------------------

    def root(self, scope: str | None) -> MemoryRoot:
        if scope is None:
            return default_write_root(self.roots)
        for r in self.roots:
            if r.scope == scope:
                return r
        available = ", ".join(r.scope for r in self.roots) or "none"
        raise KeyError(f"no memory root for scope {scope!r} (available: {available})")

    def _entries_from_roots(self, roots: Iterable[MemoryRoot]) -> list[MemoryEntry]:
        self.errors = []
        seen: dict[str, MemoryEntry] = {}
        for r in roots:
            if not r.path.is_dir():
                continue
            for path in sorted(r.path.glob("*.md")):
                if is_index_file(path):
                    continue
                try:
                    entry = parse_entry(path, scope=r.scope)
                except (MemoryParseError, OSError, UnicodeDecodeError) as e:
                    self.errors.append((path, str(e)))
                    continue
                seen.setdefault(entry.name, entry)  # nearest root wins
        return list(seen.values())

    def entries(
        self,
        *,
        type: str | None = None,
        status: str | None = None,
        scope: str | None = None,
    ) -> list[MemoryEntry]:
        if scope is None:
            roots = self.roots
        else:
            roots = [r for r in self.roots if r.scope == scope]
        out = self._entries_from_roots(roots)
        if type is not None:
            out = [e for e in out if e.type == type]
        if status is not None:
            out = [e for e in out if e.status == status]
        return sorted(out, key=lambda e: e.name)

    def index_entries(self, scope: str | None = None) -> list[MemoryEntry]:
        """Entries that belong in the index of one physical root.

        ``entries(scope=...)`` unions every root with that scope name, which
        would put later-directory filenames into the first directory's
        ``MEMORY.md``. Index generation is always per-directory.
        """
        return self._entries_from_roots([self.root(scope)])

    def get(self, name: str, scope: str | None = None) -> MemoryEntry | None:
        wanted = {name, slugify(name)}
        for e in self.entries(scope=scope):
            if e.name in wanted or e.path is not None and e.path.stem in wanted:
                return e
        return None

    # -- writes ------------------------------------------------------------

    def save(
        self,
        name: str,
        description: str,
        body: str = "",
        *,
        type: str = DEFAULT_TYPE,
        scope: str | None = None,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Write ``<slug>.md`` into the scope's root and upsert its index line."""
        slug = slugify(name)
        if slug.upper().startswith("MEMORY"):
            raise ValueError(
                f"memory name {name!r} is reserved for memory indexes; "
                "choose a name that does not start with 'memory'"
            )
        root = self.root(scope)
        root.path.mkdir(parents=True, exist_ok=True)
        entry = MemoryEntry(
            name=slug,
            description=description.strip(),
            type=type,
            body=body,
            title=title,
            metadata=dict(metadata or {}),
            scope=root.scope,
        )
        entry.path = root.path / entry.filename
        entry.path.write_text(entry.to_markdown(), encoding="utf-8")
        update_index_line(root.path, entry)
        logger.debug("saved memory %s to %s", entry.name, entry.path)
        return entry.path

    # -- index -------------------------------------------------------------

    @staticmethod
    def render_index(
        entries: Iterable[MemoryEntry], *, budget: int | None = None
    ) -> str:
        """Generate the always-on index: living entries grouped by type, one line each.

        ``budget`` caps the output in bytes. Entries that do not fit — including
        every later type group — are replaced by one trailing line naming how
        many were omitted. The marker is included in the cap, so the returned
        text never exceeds ``budget``.
        """
        if budget is not None and budget <= 0:
            raise ValueError(f"budget {budget} is too small for the index header")

        living = [e for e in entries if e.is_living]
        groups: dict[str, list[MemoryEntry]] = {}
        for e in living:
            groups.setdefault(e.type, []).append(e)
        order = [t for t in TYPE_ORDER if t in groups] + sorted(
            t for t in groups if t not in TYPE_ORDER
        )
        grouped: list[tuple[str, list[str]]] = [
            (
                f"## {t.capitalize()}",
                [e.index_line() for e in sorted(groups[t], key=lambda e: e.name)],
            )
            for t in order
        ]
        total = sum(len(lines) for _, lines in grouped)

        def build(n_entries: int) -> str:
            omitted = total - n_entries
            lines = [INDEX_HEADER, ""]
            remaining = n_entries
            for header, entry_lines in grouped:
                if remaining <= 0:
                    break
                take = min(len(entry_lines), remaining)
                lines.append(header)
                lines.extend(entry_lines[:take])
                lines.append("")
                remaining -= take
            while lines and lines[-1] == "":
                lines.pop()
            if omitted:
                lines.extend(
                    [
                        "",
                        f"- … {omitted} more entries omitted (budget {budget} bytes)",
                    ]
                )
            return "\n".join(lines) + "\n"

        if budget is None:
            return build(total)

        n = total
        while True:
            text = build(n)
            if len(text.encode()) <= budget:
                return text
            if n == 0:
                raise ValueError(f"budget {budget} is too small for the index header")
            n -= 1

    def index_path(self, scope: str | None = None) -> Path:
        return self.root(scope).path / INDEX_FILENAME

    def write_index(
        self, scope: str | None = None, *, budget: int | None = None
    ) -> Path:
        root = self.root(scope)
        text = self.render_index(self.index_entries(scope), budget=budget)
        root.path.mkdir(parents=True, exist_ok=True)
        path = root.path / INDEX_FILENAME
        path.write_text(text, encoding="utf-8")
        return path

    def check_index(
        self, scope: str | None = None, *, budget: int | None = None
    ) -> bool:
        """True when the on-disk index equals the regenerated one byte for byte."""
        path = self.root(scope).path / INDEX_FILENAME
        expected = self.render_index(self.index_entries(scope), budget=budget)
        return path.is_file() and path.read_text(encoding="utf-8") == expected
