"""Append-only lifecycle evidence for explicitly invoked skills.

Queueing is not completion. Callers must supply terminal evidence explicitly;
the CLI session boundary only abandons unresolved invocations from its own run.
"""

from __future__ import annotations

import json
import logging
import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from ..logmanager.eventlog import _event_log_lock
from ..util.cost_tracker import CostTracker

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
    schema_version: int = 2
    cost_tracker_id: str | None = None
    cost_baseline: dict[str, int | float] | None = None
    usage: dict[str, int | float] | None = None


def _cost_snapshot(logdir: Path) -> tuple[str | None, dict[str, int | float] | None]:
    """Unknown accounting is distinct from a measured zero-cost invocation."""
    costs = CostTracker.get_session_costs()
    if costs is None or costs.session_id != str(logdir.resolve()):
        return None, None
    # Snapshot the entries once so a concurrent append cannot split the totals.
    entries = list(costs.entries)
    usage: dict[str, int | float] = {
        "input_tokens": sum(e.input_tokens for e in entries),
        "output_tokens": sum(e.output_tokens for e in entries),
        "cache_read_tokens": sum(e.cache_read_tokens for e in entries),
        "cache_creation_tokens": sum(e.cache_creation_tokens for e in entries),
        "cost_usd": sum(e.cost for e in entries),
        "requests": len(entries),
    }
    if not all(math.isfinite(value) and value >= 0 for value in usage.values()):
        return None, None
    return costs.tracking_id, usage


def _usage_delta(logdir: Path, first: SkillEvent) -> dict[str, int | float] | None:
    tracker_id, current = _cost_snapshot(logdir)
    if (
        first.cost_baseline is None
        or current is None
        or tracker_id != first.cost_tracker_id
        or current.keys() != first.cost_baseline.keys()
    ):
        return None
    delta = {key: value - first.cost_baseline[key] for key, value in current.items()}
    if not all(math.isfinite(value) and value >= 0 for value in delta.values()):
        return None
    return delta


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
        if event.schema_version not in {1, 2} or event.phase not in _PHASES:
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
    # Export only committed transitions. A metric failure must not turn a saved
    # start into a missing invocation ID or cause a retry to append it twice.
    try:
        from ..telemetry import record_skill_event

        record_skill_event(event)
    except Exception:
        logger.warning("Could not export skill metrics", exc_info=True)


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
        tracker_id, baseline = _cost_snapshot(logdir)
        event = SkillEvent(
            invocation_id=str(uuid4()),
            session_id=session_id,
            skill_name=skill_name,
            skill_path=str(skill_path.resolve()),
            surface=surface,
            phase="started",
            timestamp=datetime.now(timezone.utc).isoformat(),
            cost_tracker_id=tracker_id,
            cost_baseline=baseline,
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
        schema_version=2,
        phase=phase,
        timestamp=now.isoformat(),
        duration_seconds=(
            max(0.0, (now - datetime.fromisoformat(first.timestamp)).total_seconds())
            if phase in _TERMINAL
            else None
        ),
        error_type=error_type,
        usage=_usage_delta(logdir, first) if phase in _TERMINAL else None,
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
