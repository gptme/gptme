"""Append-only lifecycle evidence for explicitly invoked skills.

Queueing is not completion. Callers must supply terminal evidence explicitly;
the CLI session boundary only abandons unresolved invocations from its own run.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from ..logmanager.eventlog import _event_log_lock

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)

SkillPhase = Literal["started", "queued", "completed", "failed", "abandoned"]
EVENTS_FILENAME = "skill-events.jsonl"
_TERMINAL = {"completed", "failed", "abandoned"}
_PHASES = {"started", "queued", *_TERMINAL}
_session: ContextVar[tuple[Path, str] | None] = ContextVar(
    "skill_session", default=None
)


@dataclass(frozen=True)
class SkillEvent:
    invocation_id: str
    session_id: str
    skill_name: str
    skill_path: str
    surface: str
    phase: SkillPhase
    timestamp: str
    duration_seconds: float | None = None
    error_type: str | None = None
    schema_version: int = 1


def read_skill_events(logdir: Path) -> list[SkillEvent]:
    """Read the ledger; refuse malformed history instead of losing terminal evidence.

    Writers call this under the conversation lock. Consumers should hold that
    same lock if they need a snapshot concurrent with a writer.
    """
    path = logdir / EVENTS_FILENAME
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = SkillEvent(**json.loads(line))
        if event.schema_version != 1 or event.phase not in _PHASES:
            raise ValueError("Unsupported skill event schema or phase")
        events.append(event)
    return events


def _append(logdir: Path, event: SkillEvent) -> None:
    # Reuse conversation serialization, but not its compacted recovery ledger.
    with (logdir / EVENTS_FILENAME).open("a+b") as stream:
        # A complete JSON record may survive without its final newline. Preserve
        # that record and append a separator instead of concatenating two objects.
        if stream.seek(0, 2):
            stream.seek(-1, 2)
            if stream.read(1) != b"\n":
                stream.write(b"\n")
        stream.write((json.dumps(asdict(event)) + "\n").encode("utf-8"))


def start_skill_invocation(
    logdir: Path,
    skill_name: str,
    skill_path: Path,
    *,
    surface: str = "gptme-slash-command",
    session_id: str | None = None,
) -> str | None:
    """Record a built skill prompt. Storage failures never block invocation."""
    try:
        current = _session.get()
        if session_id is None:
            session_id = (
                current[1]
                if current and current[0] == logdir.resolve()
                else str(logdir.resolve())
            )
        event = SkillEvent(
            invocation_id=str(uuid4()),
            session_id=session_id,
            skill_name=skill_name,
            skill_path=str(skill_path.resolve()),
            surface=surface,
            phase="started",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        with _event_log_lock(logdir):
            read_skill_events(logdir)
            _append(logdir, event)
        return event.invocation_id
    except (OSError, ValueError, TypeError):
        logger.warning("Could not record skill invocation", exc_info=True)
        return None


def _transition(
    logdir: Path,
    events: list[SkillEvent],
    invocation_id: str,
    phase: SkillPhase,
    error_type: str | None,
) -> bool:
    matching = [event for event in events if event.invocation_id == invocation_id]
    if not matching:
        return False
    first, last = matching[0], matching[-1]
    if last.phase in _TERMINAL or last.phase == phase:
        return False
    allowed = {"failed", "abandoned"}
    allowed.add("queued" if last.phase == "started" else "completed")
    if phase not in allowed:
        return False
    now = datetime.now(timezone.utc)
    event = replace(
        first,
        phase=phase,
        timestamp=now.isoformat(),
        duration_seconds=(
            max(0.0, (now - datetime.fromisoformat(first.timestamp)).total_seconds())
            if phase in _TERMINAL
            else None
        ),
        error_type=error_type,
    )
    _append(logdir, event)
    events.append(event)
    return True


def record_skill_phase(
    logdir: Path,
    invocation_id: str | None,
    phase: SkillPhase,
    *,
    error_type: str | None = None,
) -> bool:
    """Append a valid transition once, returning whether an event was written.

    ``completed`` requires caller-observed completion; neither a queued prompt
    nor a generic turn/session hook supplies that evidence.
    """
    if invocation_id is None:
        return False
    try:
        with _event_log_lock(logdir):
            return _transition(
                logdir, read_skill_events(logdir), invocation_id, phase, error_type
            )
    except (OSError, ValueError, TypeError):
        logger.warning("Could not record skill phase", exc_info=True)
        return False


def abandon_skill_invocations(logdir: Path, session_id: str) -> None:
    """Reconcile unresolved starts from exactly one run; preserve all history."""
    try:
        if not (logdir / EVENTS_FILENAME).exists():
            return
        with _event_log_lock(logdir):
            events = read_skill_events(logdir)
            invocation_ids = {
                event.invocation_id
                for event in events
                if event.session_id == session_id and event.phase == "started"
            }
            for invocation_id in invocation_ids:
                _transition(logdir, events, invocation_id, "abandoned", None)
    except (OSError, ValueError, TypeError):
        logger.warning("Could not reconcile skill invocations", exc_info=True)


@contextmanager
def skill_invocation_owner(logdir: Path, session_id: str) -> Iterator[None]:
    """Bind admission to an owner whose lifetime may span threads or requests.

    The owner must reconcile its invocations when it closes. Unlike
    ``skill_session``, leaving this context does not end the run.
    """
    token = _session.set((logdir.resolve(), session_id))
    try:
        yield
    finally:
        _session.reset(token)


@contextmanager
def skill_session(logdir: Path) -> Iterator[str]:
    """Give CLI invocations a run identity and reconcile them on every exit path."""
    session_id = str(uuid4())
    with skill_invocation_owner(logdir, session_id):
        try:
            yield session_id
        finally:
            abandon_skill_invocations(logdir, session_id)
