"""Context-aware tool selection driven by ActivityWatch.

Queries a local `ActivityWatch <https://activitywatch.net/>`_ server for the
currently focused application and maps it to a tool allowlist, so a session
started while a browser is focused can load browser tools while one started in
an editor loads code tools.

This is a read-only client: gptme never writes to ActivityWatch. Every failure
mode (no server, no window bucket, no events, malformed payload) degrades to
``None`` so the caller falls back to the full default toolset.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from fnmatch import fnmatchcase

logger = logging.getLogger(__name__)

DEFAULT_SERVER = "http://localhost:5600"
DEFAULT_TIMEOUT = 1.0

# ActivityWatch's window watcher registers its bucket with this type.
_WINDOW_BUCKET_TYPE = "currentwindow"


def _get_json(url: str, timeout: float):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_current_app(
    server: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str | None:
    """Return the name of the currently focused application, or None.

    Returns None (never raises) if ActivityWatch is unreachable, has no window
    watcher bucket, or has not recorded any events yet.
    """
    server = (server or os.environ.get("AW_SERVER_URL") or DEFAULT_SERVER).rstrip("/")
    try:
        buckets = _get_json(f"{server}/api/0/buckets/", timeout)
        if not isinstance(buckets, dict):
            return None
        bucket_id = next(
            (
                bid
                for bid, meta in buckets.items()
                if isinstance(meta, dict) and meta.get("type") == _WINDOW_BUCKET_TYPE
            ),
            None,
        )
        if bucket_id is None:
            logger.debug("ActivityWatch has no %s bucket", _WINDOW_BUCKET_TYPE)
            return None
        events = _get_json(
            f"{server}/api/0/buckets/{bucket_id}/events?limit=1", timeout
        )
        if not isinstance(events, list) or not events:
            return None
        data = events[0].get("data") if isinstance(events[0], dict) else None
        app = data.get("app") if isinstance(data, dict) else None
        return app if isinstance(app, str) and app else None
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        logger.debug("ActivityWatch query failed (%s), using default tools", exc)
        return None


def match_app_rules(app: str, rules: dict[str, list[str]]) -> list[str] | None:
    """Return the tool allowlist for `app`, or None if no rule matches.

    Rule keys are case-insensitive shell globs matched against the application
    name (e.g. ``"*firefox*"``). Rules are evaluated in declaration order and
    the first match wins, so put specific patterns before broad ones.
    """
    app_lower = app.lower()
    for pattern, tools in rules.items():
        if fnmatchcase(app_lower, pattern.lower()):
            return list(tools)
    return None


def resolve_allowlist_for_current_app(
    rules: dict[str, list[str]],
    server: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[str] | None:
    """Resolve a tool allowlist from the currently focused app, or None."""
    if not rules:
        return None
    app = get_current_app(server=server, timeout=timeout)
    if app is None:
        return None
    allowlist = match_app_rules(app, rules)
    if allowlist is None:
        logger.debug("No tool_select_by_app rule matched app %r", app)
        return None
    logger.info("ActivityWatch: app %r -> tools %s", app, ",".join(allowlist))
    return allowlist
