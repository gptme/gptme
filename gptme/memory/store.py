"""Layered memory store: union reads across roots, scoped writes, index generation."""

from __future__ import annotations

import importlib
import logging
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

from .roots import MemoryRoot, default_write_root, resolve_roots
from .schema import (
    DEFAULT_TYPE,
    TYPE_ORDER,
    MemoryEntry,
    MemoryFrontmatterError,
    MemoryParseError,
    entry_from_text,
    is_index_file,
    parse_entry,
    slugify,
)

logger = logging.getLogger(__name__)

INDEX_FILENAME = "MEMORY.md"
INDEX_HEADER = "# Persistent Memory"
LOCK_FILENAME = ".memory.lock"


def _preserve_mode(tmp: Path, dest: Path) -> None:
    """Copy dest's permission bits onto tmp so ``os.replace`` does not widen access."""
    try:
        mode = stat.S_IMODE(dest.stat().st_mode)
    except FileNotFoundError:
        return
    os.chmod(tmp, mode)


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a same-directory temp file and ``os.replace``.

    Staging the content first means ENOSPC cannot truncate the destination.
    ``os.replace`` is atomic on POSIX when source and dest share a filesystem.
    Existing destination permission bits are copied onto the staged file so a
    private ``0600`` memory file is not rewritten as world-readable.

    The temp file is opened with a restrictive mode (0o600) so that a private
    destination is never readable by other users even during the staging window.
    ``_preserve_mode`` then adjusts the mode to match the destination before the
    atomic rename, restoring broader read bits when the destination is public.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
        _preserve_mode(tmp, path)
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _commit_replacements(pairs: list[tuple[Path, str]]) -> None:
    """Replace each destination with staged content; restore on failure.

    All payloads land in sibling temp files before any destination is renamed
    into place, so a disk-full error during staging leaves originals untouched.
    Destinations already renamed are restored from the in-memory snapshot.
    Existing destination modes are copied onto each staged file before replace.
    """
    staged: list[tuple[Path, Path, str | None]] = []
    replaced: list[tuple[Path, str | None]] = []
    try:
        for dest, text in pairs:
            original = dest.read_text(encoding="utf-8") if dest.is_file() else None
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
            fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(text.encode("utf-8"))
            _preserve_mode(tmp, dest)
            staged.append((dest, tmp, original))
        for dest, tmp, original in staged:
            os.replace(tmp, dest)
            replaced.append((dest, original))
    except OSError:
        for _dest, tmp, _original in staged:
            tmp.unlink(missing_ok=True)
        restore_errors: list[OSError] = []
        for dest, original in replaced:
            try:
                if original is None:
                    dest.unlink(missing_ok=True)
                else:
                    dest.write_text(original, encoding="utf-8")
            except OSError as restore_exc:
                restore_errors.append(restore_exc)
        if restore_errors:
            raise OSError(
                f"write failed and rollback incomplete ({restore_errors[0]})"
            ) from restore_errors[0]
        raise


@dataclass(frozen=True)
class AuditIssue:
    """One actionable consistency problem in a memory root."""

    code: str
    entry: str
    detail: str


try:
    _fcntl: Any = importlib.import_module("fcntl")
except ImportError:  # pragma: no cover - Windows
    _fcntl = None

try:
    _msvcrt: Any = importlib.import_module("msvcrt")
except ImportError:  # pragma: no cover - POSIX
    _msvcrt = None


def _lock_exclusive(f) -> None:
    """Lock ``MEMORY.md`` for in-place upserts. Unix flock only.

    Windows first-save opens an empty index; byte-range locking that file is
    unreliable, so concurrent ``save`` stays best-effort there. ``supersede``
    serializes through ``_locked_root`` instead.
    """
    if _fcntl is not None:
        _fcntl.flock(f, _fcntl.LOCK_EX)


def _unlock(f) -> None:
    if _fcntl is not None:
        _fcntl.flock(f, _fcntl.LOCK_UN)


@contextmanager
def _locked_root(root_path: Path):
    """Exclusive lock covering one memory root's read-validate-commit window.

    ``update_index_line`` already flocks ``MEMORY.md`` for single-line upserts.
    ``supersede`` mutates two entries plus the index and cannot use that file as
    the lock target: ``os.replace`` of ``MEMORY.md`` would drop the flock onto
    a replaced inode. A dedicated lock file stays put for the whole window.

    Unix uses ``fcntl.flock``; Windows uses ``msvcrt.locking`` on a 1-byte
    sidecar (same pattern as ``gptme.logmanager.eventlog``).
    """
    root_path.mkdir(parents=True, exist_ok=True)
    with open(root_path / LOCK_FILENAME, "a+b") as lock:
        if _fcntl is not None:
            _fcntl.flock(lock, _fcntl.LOCK_EX)
        elif _msvcrt is not None:  # pragma: no cover - Windows
            if lock.seek(0, os.SEEK_END) == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            _msvcrt.locking(lock.fileno(), _msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if _fcntl is not None:
                _fcntl.flock(lock, _fcntl.LOCK_UN)
            elif _msvcrt is not None:  # pragma: no cover - Windows
                lock.seek(0)
                _msvcrt.locking(lock.fileno(), _msvcrt.LK_UNLCK, 1)


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
        """Write ``<slug>.md`` into the scope's root and upsert its index line.

        The root lock serialises this write against concurrent ``supersede`` calls.
        ``supersede`` replaces ``MEMORY.md`` via ``os.replace``, which would drop
        a POSIX flock held on the old inode by a concurrent ``update_index_line``.
        Acquiring ``_locked_root`` here prevents that interleaving.
        """
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
        with _locked_root(root.path):
            entry.path.write_text(entry.to_markdown(), encoding="utf-8")
            update_index_line(root.path, entry)
        logger.debug("saved memory %s to %s", entry.name, entry.path)
        return entry.path

    def supersede(
        self, old_name: str, new_name: str, *, scope: str | None = None
    ) -> tuple[MemoryEntry, MemoryEntry]:
        """Mark ``old_name`` superseded by ``new_name`` and link both entries.

        Supersession is scoped to one root so a project memory can never mutate
        a same-named user memory. Strict parsing prevents the write from
        normalizing malformed frontmatter through the lenient read fallback.
        A root-scoped lock serializes concurrent supersedes so two replacements
        cannot both observe the same living entry and corrupt the links.
        """
        root = self.root(scope)
        with _locked_root(root.path):
            old = self.get(old_name, scope=root.scope)
            new = self.get(new_name, scope=root.scope)
            if old is None:
                raise KeyError(f"no memory entry named {old_name!r} in {root.scope}")
            if new is None:
                raise KeyError(f"no memory entry named {new_name!r} in {root.scope}")
            # Reject cross-root supersession: both entries must live in the locked root.
            assert old.path is not None and new.path is not None
            if old.path.parent != root.path:
                raise KeyError(
                    f"entry {old_name!r} lives in {old.path.parent}, not the locked root {root.path}"
                )
            if new.path.parent != root.path:
                raise KeyError(
                    f"entry {new_name!r} lives in {new.path.parent}, not the locked root {root.path}"
                )
            if old.path == new.path:
                raise ValueError("an entry cannot supersede itself")
            old_path, new_path = old.path, new.path
            old = parse_entry(old_path, scope=root.scope, strict=True)
            new = parse_entry(new_path, scope=root.scope, strict=True)
            if not new.is_living:
                raise ValueError(f"replacement entry {new.name!r} is not living")
            if not old.is_living and old.superseded_by != new.name:
                raise ValueError(f"entry {old.name!r} is not living")

            old = replace(old, status="superseded", superseded_by=new.name)
            new_supersedes = list(new.supersedes)
            if old.name not in new_supersedes:
                new_supersedes.append(old.name)
            new = replace(new, supersedes=new_supersedes)
            old_text = old.to_markdown()
            new_text = new.to_markdown()

            # Validate both complete serializations before the first write.
            entry_from_text(old_text, path=old_path, scope=root.scope, strict=True)
            entry_from_text(new_text, path=new_path, scope=root.scope, strict=True)

            updated_entries = []
            for entry in self.index_entries(root.scope):
                if entry.path == old_path:
                    updated_entries.append(old)
                elif entry.path == new_path:
                    updated_entries.append(new)
                else:
                    updated_entries.append(entry)
            seen_paths = {entry.path for entry in updated_entries}
            if old_path not in seen_paths:
                updated_entries.append(old)
            if new_path not in seen_paths:
                updated_entries.append(new)
            index_text = self.render_index(updated_entries)
            _commit_replacements(
                [
                    (old_path, old_text),
                    (new_path, new_text),
                    (self.index_path(root.scope), index_text),
                ]
            )
            return old, new

    def audit(self, *, scope: str | None = None) -> list[AuditIssue]:
        """Return parse and supersession consistency issues for one root."""
        root = self.root(scope)
        entries: dict[str, MemoryEntry] = {}
        issues: list[AuditIssue] = []
        if not root.path.is_dir():
            return issues
        for path in sorted(root.path.glob("*.md")):
            if is_index_file(path):
                continue
            try:
                entry = parse_entry(path, scope=root.scope, strict=True)
            except MemoryFrontmatterError as exc:
                issues.append(AuditIssue("invalid-yaml", path.name, str(exc)))
                continue
            except (MemoryParseError, OSError, UnicodeDecodeError) as exc:
                issues.append(AuditIssue("invalid-entry", path.name, str(exc)))
                continue
            if entry.name in entries:
                issues.append(
                    AuditIssue(
                        "duplicate-name",
                        path.name,
                        f"name {entry.name!r} is also used by {entries[entry.name].path}",
                    )
                )
                continue
            entries[entry.name] = entry

        for entry in entries.values():
            if entry.superseded_by:
                replacement = entries.get(entry.superseded_by)
                if replacement is None:
                    issues.append(
                        AuditIssue(
                            "dangling-superseded-by",
                            entry.name,
                            f"replacement {entry.superseded_by!r} does not exist",
                        )
                    )
                elif entry.name not in replacement.supersedes:
                    issues.append(
                        AuditIssue(
                            "asymmetric-supersession",
                            entry.name,
                            f"{entry.superseded_by!r} does not point back to this entry",
                        )
                    )
            for old_name in entry.supersedes:
                old = entries.get(old_name)
                if old is None:
                    issues.append(
                        AuditIssue(
                            "dangling-supersedes",
                            entry.name,
                            f"superseded entry {old_name!r} does not exist",
                        )
                    )
                elif old.superseded_by != entry.name:
                    issues.append(
                        AuditIssue(
                            "asymmetric-supersession",
                            entry.name,
                            f"{old_name!r} does not point back to this entry",
                        )
                    )
        return issues

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
        path = root.path / INDEX_FILENAME
        with _locked_root(root.path):
            _atomic_write(path, text)
        return path

    def check_index(
        self, scope: str | None = None, *, budget: int | None = None
    ) -> bool:
        """True when the on-disk index equals the regenerated one byte for byte."""
        path = self.root(scope).path / INDEX_FILENAME
        expected = self.render_index(self.index_entries(scope), budget=budget)
        return path.is_file() and path.read_text(encoding="utf-8") == expected
