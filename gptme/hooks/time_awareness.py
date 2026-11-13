from __future__ import annotations

"""
Time awareness hook.

Provides time feedback during conversations to help the assistant manage
long-running sessions effectively.

This helps the assistant:
- Understand conversation duration
- Plan work within time constraints
- Manage long-running autonomous sessions effectively
- Avoid timeouts and performance issues

Shows time elapsed messages at: 1min, 5min, 10min, 15min, 20min, then every 10min.

All times come from a single clock (:func:`gptme.util.clock.now`): the wall clock as a
timezone-aware datetime in the system's local timezone. Notices always include
the full date and UTC offset, so they stay unambiguous across date boundaries.
Elapsed time is measured from the real session start (the earliest message in
the log, which survives resume), and gaps in the conversation (e.g. a resume after
hours of inactivity) are reported explicitly instead of silently jumping.
"""

import hashlib
import logging
import math
import os
import re
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from ..hooks import HookType, StopPropagation, register_hook
from ..message import Message
from ..util import clock
from ..util.clock import format_duration, format_timestamp, to_local

if TYPE_CHECKING:
    from collections.abc import Generator

    from ..hooks.types import ToolExecutePostData
    from ..logmanager import Log

logger = logging.getLogger(__name__)

# A gap between consecutive messages at least this long is reported as a
# resume/idle jump, so the assistant is told the clock moved on.
GAP_THRESHOLD = timedelta(minutes=30)

# Opt-in deadline env: ISO-8601 aware timestamps plus comma-separated budget
# fractions in (0, 1]. Invalid values fall back to elapsed milestones.
_DEADLINE_ENV = "GPTME_SESSION_DEADLINE"
_START_ENV = "GPTME_SESSION_START"
_FRACTIONS_ENV = "GPTME_TIME_NOTICE_FRACTIONS"
_DEFAULT_FRACTIONS = "0.7,0.9"
# Every variable that configures deadline mode, e.g. for test isolation.
DEADLINE_ENV_VARS = (_START_ENV, _DEADLINE_ENV, _FRACTIONS_ENV)
# Log markers for threshold state, so suppression survives resume and is
# scoped to the exact start+deadline pair (a new runner budget must not be
# suppressed by an old run's warning).
# ``:g`` formatting can emit scientific notation for tiny fractions (1e-05%).
_DEADLINE_RE = re.compile(
    r"Deadline warning: (\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)% of budget used"
)
_DEADLINE_ID_RE = re.compile(r"\[deadline-id:([0-9a-f]+)\]")
# Exact threshold, so a fraction whose display rounds (0.8333333333 shows as
# 83.3333%) is recognized as already reported on resume.
_DEADLINE_FRACTION_RE = re.compile(r"\[deadline-fraction:([0-9.eE+-]+)\]")

# Prefix identifying notices emitted by this hook. Notices are appended to the
# conversation log, so the log itself records what was already reported. That
# keeps the hook correct across resumes, process restarts, and gptme-server
# turns (which run in copied contexts where ContextVar writes don't persist).
NOTICE_PREFIX = "<system_info>The time is now "

# Context-local state. The log is the source of truth for what was reported;
# these cover what the log can't show yet: no log at all, or notices from
# earlier tools in the same step that the CLI has buffered but not yet
# appended to the log.
_conversation_start_times_var: ContextVar[dict[str, datetime] | None] = ContextVar(
    "conversation_start_times", default=None
)
_shown_milestones_var: ContextVar[dict[str, set[int]] | None] = ContextVar(
    "shown_milestones", default=None
)
# Highest deadline threshold reported per (workspace, deadline identity).
# Kept separate from elapsed milestones (ints) so the two schedules can't
# suppress each other.
_shown_thresholds_var: ContextVar[dict[str, dict[str, float]] | None] = ContextVar(
    "shown_deadline_thresholds", default=None
)
_last_notice_var: ContextVar[dict[str, datetime] | None] = ContextVar(
    "time_awareness_last_notice", default=None
)
# Length and last message of the log at the previous call, to detect a
# rewritten log (/backtrack, /edit) even when the session start is unchanged
# or the log has since grown back to its old length.
_log_tails_var: ContextVar[dict[str, tuple[int, Message | None]] | None] = ContextVar(
    "time_awareness_log_tails", default=None
)


@dataclass
class _LogScan:
    """Time facts derived from the tail of the conversation log."""

    last_notice: datetime | None
    gap: tuple[datetime, datetime] | None
    deadline_threshold: float | None = None


@dataclass(frozen=True)
class _DeadlineConfig:
    start: datetime
    deadline: datetime
    fractions: tuple[float, ...]


def _parse_aware_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO timestamp, rejecting missing/naive/invalid values."""
    if not value or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return to_local(parsed)


def _load_deadline_config() -> _DeadlineConfig | None:
    """Return validated deadline config, or None for elapsed behavior."""
    raw_start = os.environ.get(_START_ENV)
    raw_deadline = os.environ.get(_DEADLINE_ENV)
    if not raw_start or not raw_deadline:
        return None
    start = _parse_aware_timestamp(raw_start)
    deadline = _parse_aware_timestamp(raw_deadline)
    if start is None or deadline is None or deadline <= start:
        return None

    fractions: list[float] = []
    for part in os.environ.get(_FRACTIONS_ENV, _DEFAULT_FRACTIONS).split(","):
        try:
            fraction = float(part.strip())
        except ValueError:
            return None
        if not math.isfinite(fraction) or not 0 < fraction <= 1:
            return None
        fractions.append(fraction)
    return _DeadlineConfig(start, deadline, tuple(sorted(set(fractions))))


def _deadline_id(start: datetime, deadline: datetime) -> str:
    """Return a short stable identity for a start+deadline pair."""
    raw = f"{start.timestamp():.6f}|{deadline.timestamp():.6f}".encode()
    return hashlib.sha1(raw).hexdigest()[:12]


def _is_notice(msg: Message) -> bool:
    return msg.role == "system" and msg.content.startswith(NOTICE_PREFIX)


def _session_start(log: Log | None) -> datetime | None:
    """Earliest message timestamp in the log.

    Not simply the first message: a conversation created with imported history
    can start with a freshly generated system prompt newer than the history.
    """
    messages = getattr(log, "messages", None)
    if not messages:
        return None
    return min(to_local(m.timestamp) for m in messages)


def _scan_log(log: Log | None, deadline_id: str | None = None) -> _LogScan | None:
    """Scan backwards for the latest notice, a post-notice gap, and the
    highest threshold recorded for ``deadline_id``.

    Default mode stops at the latest notice. Deadline mode continues past it
    to recover threshold state, but never reports a gap from before that
    notice.
    """
    messages = getattr(log, "messages", None)
    if not messages:
        return None
    last_notice: datetime | None = None
    gap: tuple[datetime, datetime] | None = None
    deadline_threshold: float | None = None
    later: datetime | None = None
    for msg in reversed(messages):
        ts = to_local(msg.timestamp)
        if last_notice is None:
            if gap is None and later is not None and later - ts >= GAP_THRESHOLD:
                gap = (ts, later)
            if _is_notice(msg):
                last_notice = ts
                if deadline_id is None:
                    break
        if deadline_id is not None and _is_notice(msg):
            match = _DEADLINE_RE.search(msg.content)
            ident = _DEADLINE_ID_RE.search(msg.content)
            if match and ident and ident.group(1) == deadline_id:
                exact = _DEADLINE_FRACTION_RE.search(msg.content)
                deadline_threshold = (
                    float(exact.group(1)) if exact else float(match.group(1)) / 100
                )
                break
        later = ts
    return _LogScan(last_notice, gap, deadline_threshold)


def _ensure_locals():
    """Initialize context-local storage if needed."""
    if _conversation_start_times_var.get() is None:
        _conversation_start_times_var.set({})
    if _shown_milestones_var.get() is None:
        _shown_milestones_var.set({})
    if _shown_thresholds_var.get() is None:
        _shown_thresholds_var.set({})
    if _last_notice_var.get() is None:
        _last_notice_var.set({})
    if _log_tails_var.get() is None:
        _log_tails_var.set({})


def _log_rewritten(
    messages: list[Message], prev_tail: tuple[int, Message | None] | None
) -> bool:
    """Whether messages seen at the previous call are no longer in the log.

    True if the log shrank, or if the message that was last at the previous
    call is gone from its position: a /backtrack followed by new messages can
    bring the log back to (or past) its old length.
    """
    if prev_tail is None:
        return False
    prev_len, prev_last = prev_tail
    if len(messages) < prev_len:
        return True
    if prev_len == 0 or prev_last is None:
        return False
    at = messages[prev_len - 1]
    # Message.__eq__ ignores timestamps; a replacement often repeats content.
    return at != prev_last or to_local(at.timestamp) != to_local(prev_last.timestamp)


def _elapsed_milestone(start: datetime, at: datetime) -> int | None:
    return _get_next_milestone(int((at - start).total_seconds() // 60))


def _deadline_notice(
    current: datetime,
    cfg: _DeadlineConfig,
    scan: _LogScan | None,
    gap: tuple[datetime, datetime] | None,
    workspace: str,
    shown: dict[str, dict[str, float]],
    last_notices: dict[str, datetime],
) -> Message | None:
    """Build the highest newly crossed threshold notice, or a gap notice."""
    ident = _deadline_id(cfg.start, cfg.deadline)
    elapsed = (current - cfg.start).total_seconds()
    fraction_elapsed = elapsed / (cfg.deadline - cfg.start).total_seconds()
    logged = scan.deadline_threshold if scan and scan.deadline_threshold else 0.0
    reported = max(logged, shown.get(workspace, {}).get(ident, 0.0))
    # Exact comparison: the reported threshold is persisted exactly, and a
    # tolerance would drop distinct configured fractions closer than it.
    crossed = [f for f in cfg.fractions if reported < f <= fraction_elapsed]
    threshold = max(crossed) if crossed else None
    if threshold is None and gap is None:
        return None

    if threshold is not None:
        shown.setdefault(workspace, {})[ident] = threshold
    last_notices[workspace] = current
    remaining = max(cfg.deadline - current, timedelta(0))
    parts = [
        f"The time is now {format_timestamp(current)}.",
        f"Session deadline: {format_timestamp(cfg.deadline)}.",
        f"Remaining time: {format_duration(remaining)}.",
    ]
    if threshold is not None:
        parts.append(
            f"Deadline warning: {threshold * 100:g}% of budget used "
            f"[deadline-id:{ident}] [deadline-fraction:{threshold!r}]."
        )
        if threshold == cfg.fractions[-1]:
            parts.append(
                "Stop editing and send a reply now. If the work is unfinished, "
                "save a handoff and commit before the session ends."
            )
    if gap:
        before, after = gap
        parts.append(
            f"Resumed after {format_duration(after - before)} of inactivity"
            f" (last activity {format_timestamp(before)})."
        )
    content = f"<system_info>{' '.join(parts)}</system_info>"
    assert content.startswith(NOTICE_PREFIX)
    return Message("system", content, timestamp=current, hide=True)


def add_time_message(
    data: ToolExecutePostData,
) -> Generator[Message | StopPropagation, None, None]:
    """Add time elapsed message after tool execution.

    Shows messages at: 1min, 5min, 10min, 15min, 20min, then every 10min,
    plus an immediate notice when the conversation resumed after a gap.

    Args:
        data: Post-execution context (log, workspace, tool_use).
    """
    try:
        workspace = data.workspace
        if workspace is None:
            return

        workspace_str = str(workspace)

        # Ensure context-local storage is initialized
        _ensure_locals()

        conversation_start_times = _conversation_start_times_var.get()
        shown_milestones = _shown_milestones_var.get()
        shown_thresholds = _shown_thresholds_var.get()
        last_notices = _last_notice_var.get()
        log_tails = _log_tails_var.get()
        assert conversation_start_times is not None
        assert shown_milestones is not None
        assert shown_thresholds is not None
        assert last_notices is not None
        assert log_tails is not None

        current = clock.now()

        # Real session start from the log survives resume and always reflects
        # the current log (e.g. after /backtrack or /edit). Recomputed every
        # call: measured ~1.5µs/message (~3ms at 2000 messages), negligible
        # next to tool execution, so no cache to keep in sync.
        log_start = _session_start(data.log)
        messages = data.log.messages if data.log and data.log.messages else []
        tail = (len(messages), messages[-1] if messages else None)
        prev_start = conversation_start_times.get(workspace_str)
        if log_start is not None and log_start <= current:
            start_changed = prev_start is not None and to_local(prev_start) != log_start
            if start_changed or _log_rewritten(messages, log_tails.get(workspace_str)):
                # Log rewritten (backtrack/edit): milestones and in-context
                # last-notice were relative to the old log and would suppress
                # notices for the new, shorter session.
                shown_milestones[workspace_str] = set()
                shown_thresholds[workspace_str] = {}
                last_notices.pop(workspace_str, None)
            conversation_start_times[workspace_str] = log_start
            log_tails[workspace_str] = tail
        elif workspace_str not in conversation_start_times:
            # Without a log, fall back to the first hook call in this context.
            conversation_start_times[workspace_str] = current
            log_tails[workspace_str] = tail
        shown_milestones.setdefault(workspace_str, set())
        shown_thresholds.setdefault(workspace_str, {})

        start = to_local(conversation_start_times[workspace_str])
        elapsed = current - start
        milestone = _elapsed_milestone(start, current)

        deadline = _load_deadline_config()
        deadline_id = (
            _deadline_id(deadline.start, deadline.deadline) if deadline else None
        )

        # Last notice: from the log, or from this context if more recent (an
        # earlier tool in the same step whose output isn't in the log yet).
        scan = _scan_log(data.log, deadline_id)
        candidates = [scan.last_notice] if scan and scan.last_notice else []
        if workspace_str in last_notices:
            candidates.append(to_local(last_notices[workspace_str]))
        last_notice = max(candidates) if candidates else None

        # A milestone is new unless it was already reached at the last notice,
        # or already shown in this context.
        already_shown = milestone in shown_milestones[workspace_str]
        if last_notice is not None:
            prev = _elapsed_milestone(start, last_notice)
            already_shown = already_shown or (
                prev is not None and milestone is not None and milestone <= prev
            )
        new_milestone = milestone is not None and not already_shown
        gap = scan.gap if scan else None
        if gap and last_notice is not None and gap[1] <= last_notice:
            gap = None  # already reported

        # Opt-in deadline mode replaces elapsed milestones with a fraction of
        # the externally enforced budget. Invalid config falls through to the
        # unchanged elapsed behavior below.
        if deadline is not None:
            notice = _deadline_notice(
                current,
                deadline,
                scan,
                gap,
                workspace_str,
                shown_thresholds,
                last_notices,
            )
            if notice is not None:
                yield notice
            return

        if not (new_milestone or gap):
            return
        if milestone is not None:
            shown_milestones[workspace_str].add(milestone)
        last_notices[workspace_str] = current

        parts = [
            f"The time is now {format_timestamp(current)}.",
            (
                f"Time elapsed: {format_duration(elapsed)}"
                f" since session start at {format_timestamp(start)}."
            ),
        ]
        if gap:
            before, after = gap
            parts.append(
                f"Resumed after {format_duration(after - before)} of inactivity"
                f" (last activity {format_timestamp(before)})."
            )
        content = f"<system_info>{' '.join(parts)}</system_info>"
        assert content.startswith(NOTICE_PREFIX)
        yield Message(
            "system",
            content,
            # Stamp with the same clock, keeping the UTC offset so the instant
            # stays unambiguous across DST changes and save/reload.
            timestamp=current,
            hide=True,
        )

    except Exception as e:
        logger.exception(f"Error adding time message: {e}")


def _get_next_milestone(elapsed_minutes: int) -> int | None:
    """Get the next milestone to show based on elapsed minutes.

    Milestones: 1, 5, 10, 15, 20, then every 10 minutes.
    """
    if elapsed_minutes < 1:
        return None
    if elapsed_minutes < 5:
        return 1
    if elapsed_minutes < 10:
        return 5
    if elapsed_minutes < 15:
        return 10
    if elapsed_minutes < 20:
        return 15
    if elapsed_minutes < 30:
        return 20
    # Every 10 minutes after 20
    return (elapsed_minutes // 10) * 10


def register() -> None:
    """Register the time awareness hook with the hook system."""
    register_hook(
        "time_awareness.time_message",
        HookType.TOOL_EXECUTE_POST,
        add_time_message,
        priority=0,  # Normal priority
    )
    logger.debug("Registered time awareness hook")
