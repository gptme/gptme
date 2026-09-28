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
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from ..hooks import HookType, StopPropagation, register_hook
from ..message import Message
from ..util import clock
from ..util.clock import format_duration, format_timestamp, to_local

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable

    from ..hooks.types import ToolExecutePostData

logger = logging.getLogger(__name__)

# A gap between consecutive messages at least this long is reported as a
# resume/idle jump, so the assistant is told the clock moved on.
GAP_THRESHOLD = timedelta(minutes=30)

# On the first hook call in a process we have no record of what was already
# reported, so only gaps that ended this recently count as "just resumed".
FIRST_CALL_LOOKBACK = timedelta(minutes=15)

# Context-local storage for time tracking (ensures context safety in gptme-server)
_conversation_start_times_var: ContextVar[dict[str, datetime] | None] = ContextVar(
    "conversation_start_times", default=None
)
_shown_milestones_var: ContextVar[dict[str, set[int]] | None] = ContextVar(
    "shown_milestones", default=None
)
# Time of the previous hook call per workspace, used to report each gap once.
_last_check_var: ContextVar[dict[str, datetime] | None] = ContextVar(
    "time_awareness_last_check", default=None
)


def _message_times(log: Iterable[Message] | None) -> list[datetime]:
    if log is None:
        return []
    try:
        return [to_local(m.timestamp) for m in log]
    except (TypeError, AttributeError):
        return []


def _session_start(times: list[datetime], current: datetime) -> datetime | None:
    """Real session start: the earliest message timestamp, if sane."""
    if not times:
        return None
    start = min(times)
    # Ignore timestamps from the future (clock skew); fall back to process start.
    return start if start <= current else None


def _find_gap(
    times: list[datetime], since: datetime
) -> tuple[datetime, datetime] | None:
    """Latest gap >= GAP_THRESHOLD between consecutive messages that ended after ``since``."""
    ordered = sorted(times)
    for before, after in reversed(list(zip(ordered, ordered[1:], strict=False))):
        if after <= since:
            break
        if after - before >= GAP_THRESHOLD:
            return before, after
    return None


def _ensure_locals():
    """Initialize context-local storage if needed."""
    if _conversation_start_times_var.get() is None:
        _conversation_start_times_var.set({})
    if _shown_milestones_var.get() is None:
        _shown_milestones_var.set({})
    if _last_check_var.get() is None:
        _last_check_var.set({})


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
        last_checks = _last_check_var.get()
        assert conversation_start_times is not None
        assert shown_milestones is not None
        assert last_checks is not None

        current = clock.now()
        times = _message_times(data.log)

        first_call = workspace_str not in conversation_start_times
        if first_call:
            # Prefer the real session start from the log (survives resume);
            # fall back to the first hook call in this process.
            conversation_start_times[workspace_str] = (
                _session_start(times, current) or current
            )
            shown_milestones[workspace_str] = set()

        since = last_checks.get(workspace_str, current - FIRST_CALL_LOOKBACK)
        last_checks[workspace_str] = current
        gap = _find_gap(times, since)

        start = to_local(conversation_start_times[workspace_str])
        elapsed = current - start
        milestone = _get_next_milestone(int(elapsed.total_seconds() // 60))

        new_milestone = (
            milestone is not None and milestone not in shown_milestones[workspace_str]
        )
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
        yield Message(
            "system",
            f"<system_info>{' '.join(parts)}</system_info>",
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
