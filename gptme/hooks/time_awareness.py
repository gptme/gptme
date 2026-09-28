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

import logging
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
_last_notice_var: ContextVar[dict[str, datetime] | None] = ContextVar(
    "time_awareness_last_notice", default=None
)
_log_lengths_var: ContextVar[dict[str, int] | None] = ContextVar(
    "time_awareness_log_lengths", default=None
)


@dataclass
class _LogScan:
    """Time facts derived from the tail of the conversation log."""

    last_notice: datetime | None
    gap: tuple[datetime, datetime] | None


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


def _scan_log(log: Log | None) -> _LogScan | None:
    """Scan the log backwards up to the most recent time notice.

    Returns the last notice time and the latest gap >= GAP_THRESHOLD between
    consecutive messages since that notice. Only messages after the last
    notice are visited, so the cost per tool call stays proportional to
    recent activity rather than to the whole history.
    """
    messages = getattr(log, "messages", None)
    if not messages:
        return None
    last_notice: datetime | None = None
    gap: tuple[datetime, datetime] | None = None
    later: datetime | None = None
    for msg in reversed(messages):
        ts = to_local(msg.timestamp)
        if gap is None and later is not None and later - ts >= GAP_THRESHOLD:
            gap = (ts, later)
        if _is_notice(msg):
            last_notice = ts
            break
        later = ts
    return _LogScan(last_notice=last_notice, gap=gap)


def _ensure_locals():
    """Initialize context-local storage if needed."""
    if _conversation_start_times_var.get() is None:
        _conversation_start_times_var.set({})
    if _shown_milestones_var.get() is None:
        _shown_milestones_var.set({})
    if _last_notice_var.get() is None:
        _last_notice_var.set({})
    if _log_lengths_var.get() is None:
        _log_lengths_var.set({})


def _elapsed_milestone(start: datetime, at: datetime) -> int | None:
    return _get_next_milestone(int((at - start).total_seconds() // 60))


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
        last_notices = _last_notice_var.get()
        log_lengths = _log_lengths_var.get()
        assert conversation_start_times is not None
        assert shown_milestones is not None
        assert last_notices is not None
        assert log_lengths is not None

        current = clock.now()

        # Real session start from the log survives resume and always reflects
        # the current log (e.g. after /backtrack or /edit). Recomputed every
        # call: measured ~1.5µs/message (~3ms at 2000 messages), negligible
        # next to tool execution, so no cache to keep in sync.
        log_start = _session_start(data.log)
        log_len = len(data.log.messages) if data.log and data.log.messages else 0
        prev_start = conversation_start_times.get(workspace_str)
        prev_len = log_lengths.get(workspace_str)
        if log_start is not None and log_start <= current:
            start_changed = prev_start is not None and to_local(prev_start) != log_start
            log_shrunk = prev_len is not None and log_len < prev_len
            if start_changed or log_shrunk:
                # Log rewritten (backtrack/edit): milestones and in-context
                # last-notice were relative to the old log and would suppress
                # notices for the new, shorter session.
                shown_milestones[workspace_str] = set()
                last_notices.pop(workspace_str, None)
            conversation_start_times[workspace_str] = log_start
            log_lengths[workspace_str] = log_len
        elif workspace_str not in conversation_start_times:
            # Without a log, fall back to the first hook call in this context.
            conversation_start_times[workspace_str] = current
            log_lengths[workspace_str] = log_len
        shown_milestones.setdefault(workspace_str, set())

        start = to_local(conversation_start_times[workspace_str])
        elapsed = current - start
        milestone = _elapsed_milestone(start, current)

        # Last notice: from the log, or from this context if more recent (an
        # earlier tool in the same step whose output isn't in the log yet).
        scan = _scan_log(data.log)
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
