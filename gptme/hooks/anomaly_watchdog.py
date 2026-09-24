"""Opt-in behavioral anomaly watchdog for gptme sessions.

Monitors tool calls for three suspicious patterns:
- scope_escape: file write outside the session workspace / allowed directories
- write_storm: excessive file writes in a short sliding window
- novel_host: network call to a hostname not in the initial allowlist

Activation:
  GPTME_ANOMALY_WATCHDOG=warn    # log warnings, continue execution
  GPTME_ANOMALY_WATCHDOG=block   # inject a warning Message and halt tool (StopPropagation)
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
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ..hooks import HookType, register_hook
from ..plugins.plugin import GptmePlugin

if TYPE_CHECKING:
    from collections.abc import Generator

    from ..hooks import StopPropagation
    from ..hooks.types import ToolExecutePreData
    from ..message import Message

logger = logging.getLogger(__name__)

# Sliding window of write timestamps (monotonic), per async context
_write_times_var: ContextVar[list[float] | None] = ContextVar(
    "anomaly_watchdog_write_times", default=None
)

# Tools that perform file writes (for write_storm + scope_escape detection)
_WRITE_TOOLS = frozenset({"save", "append", "patch"})

# Browser-like tools that make network calls (for novel_host detection)
_NETWORK_TOOLS = frozenset({"browser", "read_web"})

# Hostnames always considered trusted (loopback, localhost)
_BUILTIN_TRUSTED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


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


def _emit(anomaly_type: str, detail: str) -> tuple[bool, str]:
    """Log + return (should_block, message)."""
    msg = f"[anomaly_watchdog] {anomaly_type}: {detail}"
    logger.warning(msg)
    return _mode() == "block", msg


def _extract_path(data: ToolExecutePreData) -> Path | None:
    """Extract the target file path from a save/append/patch tool call."""
    tool_use = data.tool_use
    if tool_use is None:
        return None

    # XML/tool-format: kwargs carry named params
    if tool_use.kwargs:
        raw = tool_use.kwargs.get("path") or tool_use.kwargs.get("filename")
        if raw:
            return Path(raw)

    # Markdown-format: first arg is the path
    if tool_use.args:
        return Path(tool_use.args[0])

    # patch tool: parse target from diff header (--- a/path or +++ b/path)
    if tool_use.tool == "patch" and tool_use.content:
        for line in tool_use.content.splitlines():
            m = re.match(r"^\+\+\+\s+(?:b/)?(.+)", line)
            if m:
                candidate = m.group(1).strip()
                if candidate and candidate != "/dev/null":
                    return Path(candidate)

    return None


def _check_scope_escape(
    data: ToolExecutePreData,
) -> tuple[bool, str] | None:
    """Detect a write to a path outside the session workspace."""
    workspace = data.workspace
    if workspace is None:
        return None

    raw_path = _extract_path(data)
    if raw_path is None:
        return None

    # Resolve against workspace so relative paths are anchored correctly
    if not raw_path.is_absolute():
        resolved = (workspace / raw_path).resolve()
    else:
        resolved = raw_path.resolve()

    workspace_resolved = workspace.resolve()

    # Allow writes inside workspace
    try:
        resolved.relative_to(workspace_resolved)
        return None
    except ValueError:
        pass

    # Allow writes inside explicitly whitelisted dirs
    for allowed in _allowed_dirs():
        try:
            resolved.relative_to(allowed)
            return None
        except ValueError:
            continue

    return _emit(
        "scope_escape",
        f"write to {resolved} is outside workspace {workspace_resolved}",
    )


def _check_write_storm() -> tuple[bool, str] | None:
    """Detect an excessive write rate in the sliding window."""
    limit = _write_limit()
    window = _write_window()
    now = time.monotonic()

    existing = _write_times_var.get()
    times = existing[:] if existing is not None else []
    # Prune old entries
    times = [t for t in times if now - t < window]
    times.append(now)
    _write_times_var.set(times)

    if len(times) > limit:
        return _emit(
            "write_storm",
            f"{len(times)} writes in the last {window:.0f}s (limit: {limit})",
        )
    return None


def _extract_url(data: ToolExecutePreData) -> str | None:
    """Extract the URL from a browser/read_web tool call."""
    tool_use = data.tool_use
    if tool_use is None:
        return None
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


def _check_novel_host(
    data: ToolExecutePreData,
) -> tuple[bool, str] | None:
    """Detect a network call to a hostname not in the allowlist."""
    url = _extract_url(data)
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
        f"network call to {hostname!r} (not in GPTME_ANOMALY_ALLOWED_HOSTS). "
        "Add to allowlist to silence.",
    )


def check_tool_pre(
    data: ToolExecutePreData,
) -> Generator[Message | StopPropagation, None, None]:
    """Main TOOL_EXECUTE_PRE hook: run all enabled anomaly checks."""
    if not _enabled():
        return

    tool_use = data.tool_use
    if tool_use is None:
        return

    findings: list[tuple[bool, str]] = []

    if tool_use.tool in _WRITE_TOOLS:
        result = _check_scope_escape(data)
        if result is not None:
            findings.append(result)
        result = _check_write_storm()
        if result is not None:
            findings.append(result)

    if tool_use.tool in _NETWORK_TOOLS:
        result = _check_novel_host(data)
        if result is not None:
            findings.append(result)

    if not findings:
        return

    from ..hooks.types import (
        StopPropagation,  # local import avoids circular at module load
    )
    from ..message import Message

    any_block = any(should_block for should_block, _ in findings)
    messages = "\n".join(msg for _, msg in findings)

    if any_block:
        prefix = "⛔ [anomaly_watchdog] Tool call blocked"
    else:
        prefix = "⚠️ [anomaly_watchdog] Anomaly detected"

    yield Message("system", f"{prefix}:\n{messages}")

    if any_block:
        yield StopPropagation()


def register() -> None:
    """Register anomaly watchdog hooks."""
    register_hook(
        "anomaly_watchdog.tool_pre",
        HookType.TOOL_EXECUTE_PRE,
        check_tool_pre,
        priority=150,  # after guardrails (200), before confirm hooks
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
        os.environ.setdefault("GPTME_ANOMALY_WATCHDOG", "warn")

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
