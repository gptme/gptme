"""Opt-in behavioral anomaly watchdog for gptme sessions.

Monitors tool calls for three suspicious patterns:
- scope_escape: file write outside the session workspace / allowed directories
- write_storm: excessive file writes in a short sliding window
- novel_host: network call to a hostname not in the initial allowlist

Activation:
  GPTME_ANOMALY_WATCHDOG=warn    # log warnings, continue execution
  GPTME_ANOMALY_WATCHDOG=block   # block the tool call via TOOL_CONFIRM (skip)
  GPTME_ANOMALY_WATCHDOG=off     # disabled (default)

Optional tuning:
  GPTME_ANOMALY_ALLOWED_DIRS=dir1:dir2     # colon-separated extra allowed write dirs
  GPTME_ANOMALY_ALLOWED_HOSTS=host1,host2  # comma-separated trusted hostnames
  GPTME_ANOMALY_WRITE_LIMIT=20             # max writes in the window (default: 20)
  GPTME_ANOMALY_WRITE_WINDOW=60            # sliding window in seconds (default: 60)
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from ..hooks import HookType, register_hook
from ..plugins.plugin import GptmePlugin

if TYPE_CHECKING:
    from collections.abc import Generator

    from ..hooks.confirm import ConfirmationResult
    from ..hooks.types import ToolExecutePostData, ToolExecutePreData
    from ..message import Message
    from ..tools.base import ToolUse

logger = logging.getLogger(__name__)

# Sliding windows of write timestamps (monotonic), keyed by session.
# A single process can host multiple sessions (e.g. ACP/server multi-workspace),
# so windows are keyed by the active conversation's logdir; sessions that
# cannot be resolved share a fallback key. Module-level state keeps detection
# immune to async-context resets (a fresh ContextVar copy would hide prior
# writes and defeat the burst window entirely).
_write_times_by_session: dict[str, list[float]] = {}
_SESSION_KEY_DEFAULT = "__default__"


def _session_key() -> str:
    """Return a stable per-conversation key for the write-storm window."""
    try:
        from ..logmanager import LogManager
    except ImportError:
        return _SESSION_KEY_DEFAULT
    log = LogManager.get_current_log()
    return str(log.logdir) if log else _SESSION_KEY_DEFAULT


# Tools that perform file writes (for write_storm + scope_escape detection)
_WRITE_TOOLS = frozenset({"save", "append", "patch"})

# Browser-like tools that make network calls (for novel_host detection).
# Subtools (e.g. "browser.read_url", "browser.open_page") are matched by
# their namespace prefix.
_NETWORK_TOOLS = frozenset({"browser", "read_web"})

# Hostnames always considered trusted (loopback, localhost)
_BUILTIN_TRUSTED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

_MAX_DETAIL_LEN = 200


def _tool_namespace(tool: str) -> str:
    """Strip subtool suffix: 'browser.read_url' -> 'browser'."""
    return tool.split(".", 1)[0]


def _mode() -> str:
    return os.environ.get("GPTME_ANOMALY_WATCHDOG", "off").lower()


def _enabled() -> bool:
    return _mode() in ("warn", "block")


def _write_limit() -> int:
    try:
        return int(os.environ.get("GPTME_ANOMALY_WRITE_LIMIT", "20"))
    except ValueError:
        return 20


def _write_window() -> float:
    try:
        return float(os.environ.get("GPTME_ANOMALY_WRITE_WINDOW", "60"))
    except ValueError:
        return 60.0


def _allowed_dirs() -> list[Path]:
    raw = os.environ.get("GPTME_ANOMALY_ALLOWED_DIRS", "")
    return [Path(p).resolve() for p in raw.split(":") if p.strip()]


def _allowed_hosts() -> frozenset[str]:
    raw = os.environ.get("GPTME_ANOMALY_ALLOWED_HOSTS", "")
    extra = frozenset(h.strip().lower() for h in raw.split(",") if h.strip())
    return _BUILTIN_TRUSTED_HOSTS | extra


def _quote(value: object) -> str:
    """Render an untrusted string safely for a system message.

    Tool-controlled text (paths, hostnames) must not be embedded raw into
    system messages — it could carry prompt-injection payloads. repr()
    escapes newlines and control characters; truncation bounds size.
    """
    text = repr(str(value))
    if len(text) > _MAX_DETAIL_LEN:
        text = text[: _MAX_DETAIL_LEN - 3] + "..."
    return text


def _extract_paths(tool_use: Any) -> list[Path]:
    """Extract candidate target paths from a save/append/patch tool call."""
    if tool_use.kwargs:
        raw = tool_use.kwargs.get("path") or tool_use.kwargs.get("filename")
        if raw:
            return [Path(raw)]

    if tool_use.args:
        return [Path(tool_use.args[0])]

    # patch tool: parse ALL targets from diff headers (--- a/path or +++ b/path).
    # Both header sides are read: a deletion-only hunk has ``+++ /dev/null`` as
    # its target, so matching only ``+++`` would miss e.g. ``--- /etc/passwd``.
    if tool_use.tool == "patch" and tool_use.content:
        paths: list[Path] = []
        for line in tool_use.content.splitlines():
            # Strip the optional tab-separated timestamp that GNU/git diffs
            # append to headers (``--- a/x\t2024-01-01 00:00:00 +0000``).
            m = re.match(r"^(?:---|\+\+\+)\s+(?:[ab]/)?([^\t]+)", line)
            if m:
                candidate = m.group(1).strip()
                if (
                    candidate
                    and candidate != "/dev/null"
                    and Path(candidate) not in paths
                ):
                    paths.append(Path(candidate))
        if paths:
            return paths

    return []


def _check_scope_escape(
    tool_use: Any,
    workspace: Path | None,
) -> tuple[bool, str] | None:
    """Detect a write to a path outside the session workspace.

    Checks every target of the tool call — a multi-file patch with even one
    out-of-workspace section is flagged.
    """
    if workspace is None:
        return None

    raw_paths = _extract_paths(tool_use)
    if not raw_paths:
        return None

    workspace_resolved = workspace.resolve()
    allowed_dirs = _allowed_dirs()

    for raw_path in raw_paths:
        if not raw_path.is_absolute():
            resolved = (workspace / raw_path).resolve()
        else:
            resolved = raw_path.resolve()

        # Allow writes inside workspace
        try:
            resolved.relative_to(workspace_resolved)
            continue
        except ValueError:
            pass

        # Allow writes inside explicitly whitelisted dirs
        if any(_is_relative_to(resolved, allowed) for allowed in allowed_dirs):
            continue

        return _emit(
            "scope_escape",
            f"write to {_quote(resolved)} is outside workspace "
            f"{_quote(workspace_resolved)}",
        )

    return None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _prune_stale_windows(now: float, window: float) -> None:
    """Drop aged-out timestamps and evict windows that have gone empty.

    Keys are removed once a session's window is empty: a long-lived server
    hosts many conversations over its lifetime, so retaining a dictionary key
    per conversation would grow without bound.
    """
    for session_key in list(_write_times_by_session):
        times = _write_times_by_session[session_key]
        times[:] = [t for t in times if now - t < window]
        if not times:
            del _write_times_by_session[session_key]


def record_write() -> None:
    """Record that a write actually executed (called from the post hook).

    Recording happens *after* execution, so a call that was blocked by this
    watchdog or declined by a later confirm hook never enters the window —
    otherwise a burst of non-executing attempts would reach the limit and
    lock out later legitimate writes.
    """
    window = _write_window()
    now = time.monotonic()
    _prune_stale_windows(now, window)
    _write_times_by_session.setdefault(_session_key(), []).append(now)


def _check_write_storm() -> tuple[bool, str] | None:
    """Detect an excessive write rate in the sliding window.

    Read-only: the window holds writes that actually executed (see
    ``record_write``), so this is a pure check. A call that trips the limit is
    skipped in block mode (see ``anomaly_watchdog_confirm``) and so is never
    recorded, which is what keeps a rejected burst from refreshing its own
    window and locking out later legitimate writes.
    """
    limit = _write_limit()
    window = _write_window()
    now = time.monotonic()

    _prune_stale_windows(now, window)
    times = _write_times_by_session.get(_session_key(), [])

    if len(times) >= limit:
        return _emit(
            "write_storm",
            f"{limit} writes in the last {window:.0f}s (limit: {limit})",
        )

    return None


def _extract_url(tool_use: Any) -> str | None:
    """Extract the URL from a browser/read_web tool call."""
    if tool_use.kwargs:
        url = tool_use.kwargs.get("url")
        if url:
            return url
    if tool_use.args:
        return tool_use.args[0]
    if tool_use.content:
        # First non-empty line may be the URL
        first = tool_use.content.strip().splitlines()[0].strip()
        if first.startswith(("http://", "https://")):
            return first
    return None


def _check_novel_host(tool_use: Any) -> tuple[bool, str] | None:
    """Detect a network call to a hostname not in the allowlist."""
    url = _extract_url(tool_use)
    if not url:
        return None

    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        return None

    hostname = hostname.lower()
    if not hostname:
        return None

    trusted = _allowed_hosts()
    if hostname in trusted:
        return None

    # Warn but don't block by default — the allowlist is additive, not exhaustive.
    # This is a *novel* host signal, not a block-by-default firewall.
    return _emit(
        "novel_host",
        f"network call to {_quote(hostname)} (not in GPTME_ANOMALY_ALLOWED_HOSTS). "
        "Add to allowlist to silence.",
    )


def _detect_findings(tool_use: Any, workspace: Path | None) -> list[str]:
    """Run all enabled anomaly checks; return finding messages."""
    findings: list[str] = []
    namespace = _tool_namespace(tool_use.tool)

    if namespace in _WRITE_TOOLS:
        result = _check_scope_escape(tool_use, workspace)
        blocked = False
        if result is not None:
            findings.append(result[1])
            blocked = result[0]
        # Don't pile a storm warning onto a call already being blocked for a
        # different reason. The storm window itself is only advanced by writes
        # that actually executed (see ``record_write``).
        if not blocked:
            result = _check_write_storm()
            if result is not None:
                findings.append(result[1])

    if namespace in _NETWORK_TOOLS:
        result = _check_novel_host(tool_use)
        if result is not None:
            findings.append(result[1])

    return findings


def _emit(anomaly_type: str, detail: str) -> tuple[bool, str]:
    """Log + return (should_block, message)."""
    msg = f"[anomaly_watchdog] {anomaly_type}: {detail}"
    logger.warning(msg)
    return _mode() == "block", msg


def check_tool_pre(
    data: ToolExecutePreData,
) -> Generator[Message, None, None]:
    """TOOL_EXECUTE_PRE hook: surface warn-mode findings as system messages.

    In block mode, blocking happens in the TOOL_CONFIRM hook (see
    ``anomaly_watchdog_confirm``) — TOOL_EXECUTE_PRE cannot prevent tool
    execution, it only stops lower-priority hooks.
    """
    if _mode() != "warn":
        return

    tool_use = data.tool_use
    if tool_use is None:
        return

    findings = _detect_findings(tool_use, data.workspace)
    if not findings:
        return

    from ..message import Message

    yield Message(
        "system",
        "⚠️ [anomaly_watchdog] Anomaly detected:\n" + "\n".join(findings),
    )


def check_tool_post(
    data: ToolExecutePostData,
) -> Generator[Message, None, None]:
    """TOOL_EXECUTE_POST hook: record writes that actually executed.

    The storm window is advanced here rather than at pre-execution time, so a
    call that was blocked by this watchdog or declined by a later confirm hook
    never counts toward it.
    """
    if _enabled() and data.tool_use is not None:
        if _tool_namespace(data.tool_use.tool) in _WRITE_TOOLS:
            record_write()
    yield from ()


def anomaly_watchdog_confirm(
    tool_use: ToolUse,
    preview: str | None = None,
    workspace: Path | None = None,
) -> ConfirmationResult | None:
    """TOOL_CONFIRM hook: block flagged tool calls in block mode.

    Returns ``ConfirmationResult.skip(...)`` so the executor actually skips
    the tool (mirrors guardrails' enforce mode). Returns None otherwise to
    fall through to the next confirm hook.
    """
    if _mode() != "block":
        return None

    findings = _detect_findings(tool_use, workspace)
    if not findings:
        return None

    from ..hooks.confirm import ConfirmationResult

    reason = "Blocked by anomaly_watchdog:\n" + "\n".join(findings)
    logger.info("anomaly_watchdog (block): %s", reason)
    return ConfirmationResult.skip(reason)


def register() -> None:
    """Register anomaly watchdog hooks."""
    # Apply config-based activation before deciding hook behavior, so that
    # [plugin.anomaly_watchdog] in gptme config enables the watchdog even
    # when this hook is registered through init_hooks (not the plugin
    # loader, which never runs for built-in hooks).
    try:
        from ..config import get_config

        _init_from_config(get_config())
    except Exception:  # fail-open: config problems must not break startup
        logger.debug("anomaly_watchdog config init failed", exc_info=True)

    register_hook(
        "anomaly_watchdog.tool_pre",
        HookType.TOOL_EXECUTE_PRE,
        check_tool_pre,
        priority=150,  # after guardrails (200), before confirm hooks
    )
    register_hook(
        "anomaly_watchdog.tool_post",
        HookType.TOOL_EXECUTE_POST,
        check_tool_post,
        priority=150,
    )
    register_hook(
        "anomaly_watchdog.confirm",
        HookType.TOOL_CONFIRM,
        anomaly_watchdog_confirm,
        priority=150,  # after guardrails (200), before cli/server confirm
    )
    logger.debug("Registered anomaly_watchdog hooks (mode=%s)", _mode())


def _init_from_config(config: object) -> None:
    """Enable via ``[plugin.anomaly_watchdog]`` in gptme config."""
    user_cfg = getattr(getattr(config, "user", None), "plugin", {}) or {}
    project = getattr(config, "project", None)
    project_cfg = getattr(project, "plugin", {}) or {} if project else {}

    merged: dict[str, object] = {}
    if isinstance(user_cfg, dict):
        merged.update(user_cfg.get("anomaly_watchdog", {}) or {})
    if isinstance(project_cfg, dict):
        merged.update(project_cfg.get("anomaly_watchdog", {}) or {})

    if (
        merged
        or (isinstance(user_cfg, dict) and "anomaly_watchdog" in user_cfg)
        or (isinstance(project_cfg, dict) and "anomaly_watchdog" in project_cfg)
    ):
        # Only default to warn when the config does not itself set a mode;
        # the setdefault in the loop below cannot override the "warn" seeded
        # here, so seeding unconditionally would turn configured block/off
        # into warn.
        if merged.get("mode") in (None, ""):
            os.environ.setdefault("GPTME_ANOMALY_WATCHDOG", "warn")

    # Precedence: an explicit environment variable always wins over config
    # (``setdefault``), so an operator can force the watchdog off or on for a
    # single run without editing config files.
    config_to_env: dict[str, str] = {
        "mode": "GPTME_ANOMALY_WATCHDOG",
        "allowed_dirs": "GPTME_ANOMALY_ALLOWED_DIRS",
        "allowed_hosts": "GPTME_ANOMALY_ALLOWED_HOSTS",
        "write_limit": "GPTME_ANOMALY_WRITE_LIMIT",
        "write_window": "GPTME_ANOMALY_WRITE_WINDOW",
    }
    for key, env_name in config_to_env.items():
        value = merged.get(key)
        if value not in (None, ""):
            os.environ.setdefault(env_name, str(value))


plugin = GptmePlugin(
    name="anomaly_watchdog",
    register_hooks=register,
    init=_init_from_config,
)
