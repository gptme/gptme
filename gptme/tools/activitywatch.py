"""
Query and analyze ActivityWatch data for productivity insights.

Provides access to ActivityWatch time tracking data through the aw-client library.
Requires a running ActivityWatch instance (aw-server) on localhost:5600.
"""

import logging
from datetime import datetime, timedelta

from .base import ToolSpec, ToolUse

logger = logging.getLogger(__name__)


def _check_aw_available():
    """Check if aw-client is available."""
    try:
        import aw_client  # noqa: F401

        return True
    except ImportError:
        return False


# Lazy import to avoid hard dependency
def _get_aw_client():
    """Get ActivityWatch client, importing only when needed."""
    try:
        from aw_client import ActivityWatchClient

        return ActivityWatchClient
    except ImportError as e:
        raise ImportError(
            "aw-client not installed. Install with: pip install aw-client"
        ) from e


def get_today_summary(
    hostname: str | None = None,
) -> str:
    """
    Get a summary of today's activity from ActivityWatch.

    Args:
        hostname: The hostname to query (defaults to current hostname).
                 Used to identify the correct ActivityWatch buckets.

    Returns:
        A formatted summary of today's activity including:
        - Total active time
        - Top applications by time spent
        - Top window titles by time spent

    Raises:
        ConnectionError: If ActivityWatch server is not running or not accessible.
        ImportError: If aw-client is not installed.
    """
    import socket

    ActivityWatchClient = _get_aw_client()

    if hostname is None:
        hostname = socket.gethostname()

    # Connect to ActivityWatch server
    try:
        client = ActivityWatchClient("gptme-activitywatch-tool", testing=False)
    except Exception as e:
        raise ConnectionError(
            f"Failed to connect to ActivityWatch server: {e}\n"
            "Make sure ActivityWatch is running on localhost:5600"
        ) from e

    # Get today's date range
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = datetime.now()

    # Get bucket IDs
    buckets = client.get_buckets()
    window_bucket_id = f"aw-watcher-window_{hostname}"

    if window_bucket_id not in buckets:
        raise ValueError(
            f"Window watcher bucket not found: {window_bucket_id}\n"
            f"Available buckets: {', '.join(buckets.keys())}"
        )

    # Get window events
    window_events = client.get_events(
        window_bucket_id, start=today_start, end=today_end
    )

    # Calculate total active time and aggregate by app
    app_time: dict[str, timedelta] = {}
    title_time: dict[str, timedelta] = {}
    total_time = timedelta()

    for event in window_events:
        duration = timedelta(seconds=event["duration"])
        app = event["data"].get("app", "Unknown")
        title = event["data"].get("title", "Unknown")

        # Aggregate
        app_time[app] = app_time.get(app, timedelta()) + duration
        title_time[title] = title_time.get(title, timedelta()) + duration
        total_time += duration

    # Format output
    output = []
    output.append(f"📊 ActivityWatch Summary for {today_start.strftime('%Y-%m-%d')}")
    output.append(f"\n⏱️  Total Active Time: {_format_duration(total_time)}")

    # Top applications
    output.append("\n\n📱 Top Applications:")
    sorted_apps = sorted(app_time.items(), key=lambda x: x[1], reverse=True)
    for i, (app, duration) in enumerate(sorted_apps[:10], 1):
        percentage = (
            (duration / total_time * 100) if total_time.total_seconds() > 0 else 0
        )
        output.append(
            f"  {i:2}. {app:30} {_format_duration(duration):>10} ({percentage:5.1f}%)"
        )

    # Top window titles
    output.append("\n\n🪟 Top Window Titles:")
    sorted_titles = sorted(title_time.items(), key=lambda x: x[1], reverse=True)
    for i, (title, duration) in enumerate(sorted_titles[:10], 1):
        percentage = (
            (duration / total_time * 100) if total_time.total_seconds() > 0 else 0
        )
        # Truncate long titles
        title_display = title[:60] + "..." if len(title) > 60 else title
        output.append(
            f"  {i:2}. {title_display:60} {_format_duration(duration):>10} ({percentage:5.1f}%)"
        )

    return "\n".join(output)


def _format_duration(td: timedelta) -> str:
    """Format a timedelta as HH:MM:SS."""
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    if hours > 0:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    elif minutes > 0:
        return f"{minutes}m {seconds:02d}s"
    else:
        return f"{seconds}s"


def examples(tool_format):
    return f"""
### Get today's activity summary from ActivityWatch
User: What have I been working on today?
Assistant: I'll check your ActivityWatch data for today's activity summary.
{ToolUse("ipython", [], "get_today_summary()").to_output(tool_format)}
System: 📊 ActivityWatch Summary for 2025-11-19
⏱️  Total Active Time: 6h 32m 15s

📱 Top Applications:
   1. Code                          3h 45m 22s ( 57.4%)
   2. Firefox                       1h 52m 18s ( 28.6%)
   3. Terminal                      0h 54m 35s ( 14.0%)
...
Assistant: Based on your ActivityWatch data, you've been primarily working in Code (57.4% of time) for 3h 45m, with significant time in Firefox (1h 52m) and Terminal (54m).
"""


# Tool specification
tool = ToolSpec(
    name="activitywatch",
    desc="Query and analyze ActivityWatch data for productivity insights",
    instructions="""
The following Python functions are available using the `ipython` tool:

```txt
get_today_summary(hostname: Optional[str]) -> str:
    Get a summary of today's activity from ActivityWatch.
    Returns formatted summary with total time, top apps, and top window titles.

    Args:
        hostname: Optional hostname to query (defaults to current machine).
                 Use when querying remote ActivityWatch instances.

    Raises:
        ConnectionError: If ActivityWatch server not running (check localhost:5600).
        ImportError: If aw-client not installed (pip install aw-client).
```

**Requirements**:
- ActivityWatch must be running on localhost:5600
- aw-client library must be installed
- Valid window watcher bucket for the hostname

**Usage Notes**:
- Data is pulled from local ActivityWatch instance only
- Respects user's privacy (no data leaves their machine)
- Requires ActivityWatch to have been running to collect data
    """.strip(),
    examples=examples,
    functions=[get_today_summary],
    available=lambda: _check_aw_available(),
)

__doc__ = tool.get_doc(__doc__)
