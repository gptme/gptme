"""ActivityWatch tool - query and analyze ActivityWatch data."""

import logging
from datetime import datetime, timedelta
from typing import Any

import requests

from ..message import Message
from .base import ConfirmFunc, ToolSpec

logger = logging.getLogger(__name__)

# ActivityWatch API base URL (localhost)
BASE_URL = "http://localhost:5600/api/0"


class ActivityWatchError(Exception):
    """Exception raised when ActivityWatch operations fail."""

    pass


def get_buckets() -> dict[str, Any]:
    """
    Get all available ActivityWatch buckets.

    Returns:
        Dictionary of bucket_id -> bucket_info

    Raises:
        ActivityWatchError: If request fails
    """
    try:
        response = requests.get(f"{BASE_URL}/buckets", timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError as e:
        raise ActivityWatchError(
            "Could not connect to ActivityWatch server. "
            "Is ActivityWatch running on localhost:5600?"
        ) from e
    except requests.exceptions.RequestException as e:
        raise ActivityWatchError(f"Failed to get buckets: {e}") from e


def get_events(
    bucket_id: str, start: datetime, end: datetime, limit: int = 100
) -> list[dict[str, Any]]:
    """
    Get events from a specific bucket within a time range.

    Args:
        bucket_id: Bucket to query
        start: Start time
        end: End time
        limit: Maximum number of events to return

    Returns:
        List of events

    Raises:
        ActivityWatchError: If request fails
    """
    try:
        params = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": limit,
        }
        response = requests.get(
            f"{BASE_URL}/buckets/{bucket_id}/events", params=params, timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise ActivityWatchError(f"Failed to get events: {e}") from e


def query_activity(days: int = 1) -> dict[str, Any]:
    """
    Query ActivityWatch data for the last N days.

    Args:
        days: Number of days to query (default: 1)

    Returns:
        Dictionary with activity summary including:
        - total_time: Total active time
        - apps: Time spent in different applications
        - buckets: Available buckets

    Raises:
        ActivityWatchError: If query fails
    """
    try:
        # Get buckets
        buckets = get_buckets()

        if not buckets:
            return {
                "error": "No ActivityWatch buckets found. Is ActivityWatch collecting data?",
                "buckets": [],
            }

        # Calculate time range
        end_time = datetime.now()
        start_time = end_time - timedelta(days=days)

        # Find window watcher bucket (contains app/window data)
        window_bucket = None
        for bucket_id in buckets:
            if "aw-watcher-window" in bucket_id:
                window_bucket = bucket_id
                break

        if not window_bucket:
            return {
                "error": "No window watcher bucket found",
                "available_buckets": list(buckets.keys()),
            }

        # Get events from window bucket
        events = get_events(window_bucket, start_time, end_time, limit=1000)

        # Aggregate app time
        app_time: dict[str, float] = {}
        total_time = 0.0

        for event in events:
            duration = event.get("duration", 0)
            app = event.get("data", {}).get("app", "Unknown")

            app_time[app] = app_time.get(app, 0) + duration
            total_time += duration

        # Sort apps by time
        sorted_apps = sorted(app_time.items(), key=lambda x: x[1], reverse=True)

        return {
            "time_range": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat(),
                "days": days,
            },
            "total_time_seconds": total_time,
            "total_time_hours": round(total_time / 3600, 2),
            "apps": [
                {
                    "name": app,
                    "time_seconds": time,
                    "time_hours": round(time / 3600, 2),
                    "percentage": round(
                        (time / total_time * 100) if total_time > 0 else 0, 1
                    ),
                }
                for app, time in sorted_apps[:20]  # Top 20 apps
            ],
            "buckets_used": [window_bucket],
        }

    except ActivityWatchError:
        raise
    except Exception as e:
        raise ActivityWatchError(f"Failed to query activity: {e}") from e


def format_summary(data: dict[str, Any]) -> str:
    """
    Format activity data into a human-readable summary.

    Args:
        data: Activity data from query_activity()

    Returns:
        Formatted string summary
    """
    if "error" in data:
        return f"Error: {data['error']}"

    lines = []
    lines.append(f"📊 Activity Summary ({data['time_range']['days']} day(s))")
    lines.append(f"⏱️  Total Active Time: {data['total_time_hours']:.1f} hours")
    lines.append("")
    lines.append("🖥️  Top Applications:")

    for app in data["apps"][:10]:  # Top 10
        hours = app["time_hours"]
        pct = app["percentage"]
        name = app["name"]
        bar = "█" * int(pct / 5)  # 5% per bar
        lines.append(f"  {name:30s} {hours:5.1f}h ({pct:4.1f}%) {bar}")

    return "\n".join(lines)


def execute_activitywatch(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Message:
    """
    Execute ActivityWatch queries.

    Args:
        code: Optional query parameters (JSON)
        args: List of arguments (first arg can be command)
        kwargs: Keyword arguments
        confirm: Confirmation function

    Returns:
        Message with query results
    """
    try:
        # Parse command from args or code
        command = "summary"  # default
        days = 1  # default

        if args and len(args) > 0:
            command = args[0].lower()
            if len(args) > 1:
                try:
                    days = int(args[1])
                except ValueError:
                    return Message(
                        "system",
                        f"Error: Invalid days parameter '{args[1]}'. Must be an integer.",
                    )

        # Handle different commands
        if command == "buckets":
            # List available buckets
            buckets = get_buckets()
            bucket_list = "\n".join(f"  - {bucket_id}" for bucket_id in buckets.keys())
            return Message(
                "system",
                f"Available ActivityWatch buckets:\n{bucket_list}",
            )

        elif command == "summary" or command == "query":
            # Get activity summary
            data = query_activity(days=days)
            summary = format_summary(data)
            return Message("system", summary)

        else:
            return Message(
                "system",
                f"Unknown command '{command}'. Available: buckets, summary, query",
            )

    except ActivityWatchError as e:
        logger.error(f"ActivityWatch error: {e}")
        return Message("system", f"ActivityWatch error: {e}")
    except Exception as e:
        logger.exception("Unexpected error in activitywatch tool")
        return Message("system", f"Unexpected error: {e}")


tool = ToolSpec(
    name="activitywatch",
    desc="Query and analyze ActivityWatch data",
    instructions="""
Query ActivityWatch data to analyze productivity and activity patterns.

ActivityWatch must be running on localhost:5600 for this tool to work.

Commands:
- buckets: List available ActivityWatch buckets
- summary [days]: Get activity summary for last N days (default: 1)
- query [days]: Alias for summary

The tool returns:
- Total active time
- Time breakdown by application
- Percentage distribution

Example queries:
- "Show me yesterday's activity"
- "What did I work on last week?"
- "How much time did I spend in VSCode today?"
""",
    examples="""
> User: What did I work on yesterday?
> Assistant: Let me query your ActivityWatch data.
```activitywatch
summary 1
```
> System: 📊 Activity Summary (1 day(s))
⏱️  Total Active Time: 7.2 hours

🖥️  Top Applications:
  Code                           3.2h (44.4%) ████████
  Firefox                        2.1h (29.2%) █████
  Terminal                       1.1h (15.3%) ███
  Slack                          0.8h (11.1%) ██

> User: Show me my activity for the past week
> Assistant: I'll get your activity summary for the last 7 days.
```activitywatch
summary 7
```

> User: What buckets are available?
> Assistant: Let me check the available ActivityWatch buckets.
```activitywatch
buckets
```
""",
    execute=execute_activitywatch,
    block_types=["activitywatch"],
    available=True,
)
