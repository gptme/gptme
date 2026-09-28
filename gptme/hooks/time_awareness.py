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
Elapsed time is measured from the real session start (the first message in the
log, which survives resume), and gaps in the conversation (e.g. a resume after
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

# Context-local fallback state, used only when no log is available.
_conversation_start_times_var: ContextVar[dict[str, datetime] | None] = ContextVar(
    "conversation_start_times", default=None
)
_shown_milestones_var: ContextVar[dict[str, set[int]] | None] = ContextVar(
    "shown_milestones", default=None
)


@dataclass
class _LogScan:
    """Time facts derived from the conversation log."""

    start: datetime
    last_notice: datetime | None
    gap: tuple[datetime, datetime] | None


def _is_notice(msg: Message) -> bool:
    return msg.role == "system" and msg.content.startswith(NOTICE_PREFIX)


def _scan_log(log: Log | None) -> _LogScan | None:
    """Scan the log backwards up to the most recent time notice.

    Returns the session start (first message), the last notice time, and the
    latest gap >= GAP_THRESHOLD between consecutive messages since that notice.
    Only messages after the last notice are visited, so the cost per tool call
    stays proportional to recent activity rather than to the whole history.
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
    return _LogScan(
        start=to_local(messages[0].timestamp), last_notice=last_notice, gap=gap
    )


def _ensure_locals():
    """Initialize context-local storage if needed."""
    if _conversation_start_times_var.get() is None:
        _conversation_start_times_var.set({})
    if _shown_milestones_var.get() is None:
        _shown_milestones_var.set({})


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
        assert conversation_start_times is not None
        assert shown_milestones is not None

        current = clock.now()
        scan = _scan_log(data.log)

        if workspace_str not in conversation_start_times:
            # Real session start from the log survives resume; without a log,
            # fall back to the first hook call in this context.
            start_guess = scan.start if scan and scan.start <= current else current
            conversation_start_times[workspace_str] = start_guess
            shown_milestones[workspace_str] = set()

        if scan and scan.start <= current:
            start = scan.start
        else:
            start = to_local(conversation_start_times[workspace_str])
        elapsed = current - start
        milestone = _elapsed_milestone(start, current)

        # A milestone is new unless it was already reached at the last notice
        # recorded in the log, or already shown in this context.
        already_shown = milestone in shown_milestones[workspace_str]
        if scan and scan.last_notice is not None:
            prev = _elapsed_milestone(start, scan.last_notice)
            already_shown = already_shown or (
                prev is not None and milestone is not None and milestone <= prev
            )
        new_milestone = milestone is not None and not already_shown
        gap = scan.gap if scan else None
        if not (new_milestone or gap):
            return
        if milestone is not None:
            shown_milestones[workspace_str].add(milestone)

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
            # Stamp with the same clock (as naive local, matching the Message
            # default) so the next scan knows exactly when this was reported.
            timestamp=current.replace(tzinfo=None),
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
