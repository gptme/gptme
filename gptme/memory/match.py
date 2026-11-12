"""Triggered delivery of keyworded memories through the existing lesson matcher."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..lessons.parser import Lesson
    from .schema import MemoryEntry
    from .store import MemoryStore


@dataclass(frozen=True)
class MemoryMatch:
    """A living memory and the explicit keywords that triggered it."""

    entry: MemoryEntry
    score: float
    matched_by: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            **self.entry.to_dict(),
            "body": self.entry.body,
            "score": self.score,
            "matched_by": self.matched_by,
        }


def _keyworded_entries(store: MemoryStore) -> dict[Path, MemoryEntry]:
    return {
        entry.path: entry
        for entry in store.entries(status="living")
        if entry.keywords and entry.path is not None
    }


def _entries_as_lessons(entries: dict[Path, MemoryEntry]) -> list[Lesson]:
    from ..lessons.parser import Lesson, LessonMetadata

    return [
        Lesson(
            path=path,
            metadata=LessonMetadata(keywords=entry.keywords),
            title=entry.index_title,
            description=entry.description,
            category="memory",
            # Description-only rules must still deliver their text.
            body=entry.body.strip() or entry.description,
        )
        for path, entry in entries.items()
    ]


def memory_lessons(store: MemoryStore) -> list[Lesson]:
    """Keyworded living entries as lessons, for gptme's lesson auto-inclusion.

    Same conversion :func:`match_memories` uses, so a memory triggers in gptme
    chat exactly when ``memory match`` would report it (keywords only; category
    ``memory``; path is the entry file, which the hook uses for session dedup).
    """
    return _entries_as_lessons(_keyworded_entries(store))


#: ``scope`` and ``type`` of a match that came from a lesson dir, not a memory root.
LESSON_SCOPE = "lesson"


def lesson_candidates(
    lesson_dirs: Iterable[Path] = (), *, include_default: bool = False
) -> list[Lesson]:
    """Active lessons from lesson dirs, reduced to their explicit triggers.

    ``include_default`` adds the directories gptme's own lesson index uses
    (user and workspace ``lessons/``, ``[lessons] dirs`` from config,
    ``GPTME_LESSONS_EXTRA_DIRS``); ``lesson_dirs`` adds explicit ones.

    Only ``keywords`` and ``patterns`` survive, so a lesson triggers from
    ``memory match`` under the rule memories follow: explicit phrases only.
    Skill names, tools, and Cursor rules never trigger here (a Cursor rule's
    keywords are generated from its globs and ``alwaysApply``, not written by
    its author), and entries without keywords or patterns (plain skills) are
    not candidates. Skills listed in an ``index.json`` manifest are read in
    full, so their ``SKILL.md`` patterns and keywords count as well as the
    manifest's keywords.

    With ``include_default`` and no project config loaded yet (``gptme-util``
    does not load one), the current directory's ``gptme.toml`` is read for the
    duration of directory discovery so its ``[lessons] dirs`` are searched, then
    dropped again: a read-only matcher leaves the process config as it found it.

    Discovery failures (an unreadable ``gptme.toml``, a broken lesson dir) log a
    warning and yield no lesson candidates, so memories are still delivered.
    """
    try:
        dirs = _lesson_search_dirs(lesson_dirs, include_default)
        if not dirs:
            return []
        return _reduce_to_triggers(dirs)
    except Exception as e:
        logger.warning(f"Skipping lesson matching, discovery failed: {e}")
        return []


def _lesson_search_dirs(
    lesson_dirs: Iterable[Path], include_default: bool
) -> list[Path]:
    """Existing lesson dirs, resolved so differently spelled aliases collapse."""
    from ..config import get_config, get_project_config
    from ..lessons.index import LessonIndex

    dirs: list[Path] = []
    if include_default:
        config = get_config()
        loaded_here = config.project is None
        if loaded_here:
            # quiet: a "Using project configuration" log line would land in
            # --format json output whenever the host process has a stdout
            # logging handler installed.
            config.project = get_project_config(Path.cwd(), quiet=True)
        try:
            dirs.extend(LessonIndex._default_dirs())
        finally:
            if loaded_here:
                config.project = None
    dirs.extend(Path(d).expanduser() for d in lesson_dirs)
    return [d for d in dict.fromkeys(d.resolve() for d in dirs) if d.is_dir()]


def _reduce_to_triggers(dirs: list[Path]) -> list[Lesson]:
    from ..lessons.index import LessonIndex
    from ..lessons.parser import LessonMetadata

    index = LessonIndex(lesson_dirs=dirs)
    candidates: list[Lesson] = []
    for lesson in index.lessons:
        manifest_keywords: list[str] = []
        if lesson.is_stub:
            manifest_keywords = list(lesson.metadata.keywords)
            lesson = index.materialize_lesson(lesson)
        meta = lesson.metadata
        # Re-check status: a stub's manifest entry defaults to active, so the
        # index admits it before SKILL.md (which may be archived) is read.
        if meta.status != "active":
            continue
        if lesson.path.suffix == ".mdc" or meta.globs or meta.always_apply:
            continue
        keywords = list(dict.fromkeys(manifest_keywords + list(meta.keywords)))
        if not (keywords or meta.patterns):
            continue
        candidates.append(
            replace(
                lesson,
                metadata=LessonMetadata(
                    id=meta.id, keywords=keywords, patterns=meta.patterns
                ),
            )
        )
    return candidates


def _lesson_file_name(path: Path) -> str:
    """A skill is named by its directory, any other lesson by its file stem."""
    return path.parent.name if path.name == "SKILL.md" else path.stem


def _lesson_as_entry(lesson: Lesson) -> MemoryEntry:
    """A matched lesson in the shape hits are rendered, deduped and serialized."""
    from .schema import MemoryEntry

    return MemoryEntry(
        name=lesson.metadata.id or _lesson_file_name(lesson.path),
        title=lesson.title or None,
        description=lesson.description or "",
        type=LESSON_SCOPE,
        body=lesson.body,
        keywords=list(lesson.metadata.keywords),
        path=lesson.path,
        scope=LESSON_SCOPE,
    )


def match_memories(
    store: MemoryStore,
    text: str,
    *,
    limit: int | None = 5,
    lessons: list[Lesson] | None = None,
) -> list[MemoryMatch]:
    """Match living entries by explicit keywords, using lesson wildcard semantics.

    Layering happens before lifecycle filtering, so a historical project entry
    still shadows a same-named user entry. Index selection is independent:
    unselected living memories remain eligible for triggered delivery.

    Memory names are identifiers, not skill names: only ``keywords`` participate
    in matching. No description/body similarity or tool-name bonus is added.
    ``limit=None`` returns every match (session dedup filters before capping).

    ``lessons`` (see :func:`lesson_candidates`) are matched in the same pass
    and ranked with the memories by score; ties keep memories first. A lesson
    hit is reported as an entry with scope and type ``lesson``. A file that is
    both a memory entry and a lesson is reported once, as the memory.
    """
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    if not text.strip():
        return []

    entries = _keyworded_entries(store)
    if not entries and not lessons:
        return []

    # Keep the lesson subsystem lazy for memory readers/writers that don't match.
    from ..lessons.matcher import LessonMatcher, MatchContext

    candidates = _entries_as_lessons(entries) + list(lessons or [])
    return [
        MemoryMatch(
            entries.get(result.lesson.path) or _lesson_as_entry(result.lesson),
            result.score,
            result.matched_by,
        )
        for result in LessonMatcher().match(candidates, MatchContext(message=text))[
            :limit
        ]
    ]


#: Session dedup state older than this is pruned on the next dedup call.
SEEN_TTL_SECONDS = 7 * 24 * 3600


def _seen_file(session_id: str, state_dir: Path | None) -> Path:
    if state_dir is None:
        from ..dirs import get_state_dir

        state_dir = get_state_dir()
    # Hash the harness-supplied id so it can never act as a path component.
    digest = hashlib.sha256(session_id.encode()).hexdigest()[:32]
    return state_dir / "memory-match" / f"{digest}.json"


def filter_unseen(
    matches: list[MemoryMatch],
    session_id: str,
    *,
    limit: int | None = None,
    state_dir: Path | None = None,
) -> list[MemoryMatch]:
    """Drop matches already delivered in this session, and record the rest.

    Hooks such as Claude Code's ``PreToolUse`` fire on every tool call, so a
    keyword that keeps matching would re-inject the same entry each time. This
    keeps a per-session set of delivered entry paths in the gptme state dir
    (keyed by a hash of ``session_id``) and returns only new matches, capped at
    ``limit``. Only returned matches are recorded, so entries cut by the cap
    stay eligible for a later call.

    Best effort by design: concurrent hook processes may both deliver an entry
    once (last writer wins), an unreadable or misshapen state file counts as
    empty, and a state write failure (unwritable dir, full disk) only loses
    dedup: the matches are still returned.
    Every call refreshes the session's state file, so pruning (state idle for
    :data:`SEEN_TTL_SECONDS`) tracks session activity, not the last new
    delivery.
    """
    if not session_id:
        return matches[:limit]
    path = _seen_file(session_id, state_dir)
    seen = _load_seen(path)
    fresh = [m for m in matches if str(m.entry.path) not in seen][:limit]
    if not fresh:
        _touch_seen(path)
        return []
    seen.update(str(m.entry.path) for m in fresh)
    _save_seen(path, seen)
    return fresh


def _load_seen(path: Path) -> set[str]:
    """Read delivered paths; anything unreadable or misshapen counts as empty."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return set()
    seen = data.get("seen") if isinstance(data, dict) else None
    if not isinstance(seen, list) or not all(isinstance(s, str) for s in seen):
        return set()
    return set(seen)


def _touch_seen(path: Path) -> None:
    """Mark an existing session state as active (keeps it from being pruned)."""
    try:
        os.utime(path)
    except OSError:
        pass  # no state yet, or not writable: best effort, like _save_seen


def _save_seen(path: Path, seen: set[str]) -> None:
    """Persist delivered paths and prune stale state, best effort.

    Any filesystem failure (unwritable state dir, full disk) only loses dedup;
    the caller still delivers the matches.
    """
    tmp: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".seen-", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump({"seen": sorted(seen)}, f)
        os.replace(tmp, path)
        tmp = None
    except OSError:
        if tmp is not None:
            try:
                Path(tmp).unlink(missing_ok=True)
            except OSError:
                pass
        return
    cutoff = time.time() - SEEN_TTL_SECONDS
    try:
        olds = list(path.parent.glob("*.json"))
    except OSError:
        return
    for old in olds:
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


def render_matches(matches: list[MemoryMatch], *, body_chars: int = 1200) -> str:
    """Render bounded memory bodies with source paths for harness injection."""
    if body_chars <= 0:
        raise ValueError("body_chars must be positive")
    if not matches:
        return ""
    lines = ["<memory_triggered_entries>", "## Triggered Memories"]
    for hit in matches:
        entry = hit.entry
        lines.append(f"### {entry.index_title}")
        lines.append(f"Source: {entry.path} (scope: {entry.scope})")
        body = entry.body.strip() or entry.description
        if len(body) > body_chars:
            # Python 3 str is indexed by Unicode code point, so this cannot
            # split a UTF-8 sequence. json.dumps of the result stays valid.
            body = (
                body[: body_chars - 3].rstrip() + "..."
                if body_chars > 3
                else body[:body_chars]
            )
        lines.append(body)
    lines.append("</memory_triggered_entries>")
    return "\n\n".join(lines)
