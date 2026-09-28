"""A single, timezone-consistent clock for time shown to the assistant.

Everything the assistant is told about "now" (the system prompt's date, the
per-step time notices) should come from :func:`now` and be formatted with the
helpers here, so the sources never disagree on timezone or date.
"""

from datetime import datetime, timedelta, timezone


def now() -> datetime:
    """Current wall-clock time as an aware datetime in the local timezone."""
    return datetime.now(timezone.utc).astimezone()


def to_local(dt: datetime) -> datetime:
    """Normalize a datetime to an aware datetime in the local timezone.

    Naive datetimes are interpreted as local time, which is what ``Message``'s
    default ``datetime.now`` factory produces. Aware datetimes (e.g. UTC
    timestamps from the server or from file mtimes) are converted.
    """
    return dt.astimezone()


def format_tz(dt: datetime) -> str:
    """Format the timezone of ``dt`` as e.g. ``CEST (UTC+02:00)`` or ``UTC+00:00``."""
    dt = to_local(dt)
    offset = dt.strftime("%z")  # e.g. +0200
    offset_str = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"
    tzname = dt.tzname() or ""
    # Some platforms report the name as the offset itself (e.g. "+02"), and
    # "UTC (UTC+00:00)" is redundant; show only the offset in those cases.
    if tzname and not tzname.startswith(("+", "-")) and tzname != "UTC":
        return f"{tzname} ({offset_str})"
    return offset_str


def format_timestamp(dt: datetime) -> str:
    """Format as e.g. ``2026-09-28 11:35 CEST (UTC+02:00)``."""
    dt = to_local(dt)
    return f"{dt.strftime('%Y-%m-%d %H:%M')} {format_tz(dt)}"


def format_duration(delta: timedelta) -> str:
    """Format a duration as e.g. ``45min``, ``2h 5min``, ``1d 3h``."""
    total_minutes = max(0, int(delta.total_seconds() // 60))
    days, rem = divmod(total_minutes, 60 * 24)
    hours, minutes = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h" if hours > 0 else f"{days}d"
    if hours > 0:
        return f"{hours}h {minutes}min" if minutes > 0 else f"{hours}h"
    return f"{minutes}min"
