"""Opt-in ActivityWatch session emission via aw-watcher-agent.

This hook plugin is intentionally small and fail-open:

- it shells out to the external ``aw-watcher-agent`` CLI if configured
- it emits ``session.start`` / ``session.end`` lifecycle events and per-tool
  activity heartbeats
- failures are logged and ignored so telemetry never breaks a session

Activation:

- set ``GPTME_AW_WATCHER_AGENT=1``, or
- add ``[plugin.aw_watcher_agent]`` to config to enable it automatically
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import time
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

from ..hooks import HookType, register_hook
from ..llm.models import get_default_model
from ..logmanager import LogManager
from ..plugins.plugin import GptmePlugin
from .server_confirm import current_session_id

if TYPE_CHECKING:
    from collections.abc import Generator

    from ..hooks import StopPropagation
    from ..hooks.types import ToolExecutePostData, ToolExecutePreData
    from ..message import Message

logger = logging.getLogger(__name__)

_tool_start_times_var: ContextVar[dict[int, float] | None] = ContextVar(
    "aw_watcher_agent_tool_start_times",
    default=None,
)
# Prune entries older than this to prevent accumulation when POST is skipped (e.g. tool exception)
_MAX_TOOL_AGE_S = 300


def _enabled() -> bool:
    return os.environ.get("GPTME_AW_WATCHER_AGENT", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _command_prefix() -> list[str]:
    raw = os.environ.get("GPTME_AW_WATCHER_AGENT_COMMAND", "aw-watcher-agent")
    parts = shlex.split(raw)
    return parts or ["aw-watcher-agent"]


def _workspace_name(workspace: Path | None) -> str | None:
    if workspace is None:
        return None
    try:
        return workspace.name or str(workspace)
    except Exception:  # pragma: no cover - defensive
        return str(workspace)


def _model_name() -> str | None:
    model = get_default_model()
    if model is None:
        return None
    return model.full


def _base_args(session_id: str, workspace: Path | None) -> list[str]:
    args = [
        "--harness",
        "gptme",
        "--session-id",
        session_id,
    ]
    if model := _model_name():
        args.extend(["--model", model])
    if workspace_name := _workspace_name(workspace):
        args.extend(["--workspace", workspace_name])
    if trigger := os.environ.get("GPTME_AW_WATCHER_TRIGGER"):
        args.extend(["--trigger", trigger])
    if category := os.environ.get("GPTME_AW_WATCHER_CATEGORY"):
        args.extend(["--category", category])
    if server := os.environ.get("GPTME_AW_WATCHER_SERVER"):
        args.extend(["--server", server])
    if hostname := os.environ.get("GPTME_AW_WATCHER_HOSTNAME"):
        args.extend(["--hostname", hostname])
    return args


def _run_aw(argv: list[str]) -> None:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError as exc:
        logger.debug("aw-watcher-agent unavailable: %s", exc)
        return
    except subprocess.TimeoutExpired as exc:
        logger.warning("aw-watcher-agent timed out: %s", exc)
        return

    if result.returncode != 0:
        logger.warning(
            "aw-watcher-agent exited %s: %s",
            result.returncode,
            (result.stderr or result.stdout).strip(),
        )
    elif result.stderr.strip():
        logger.debug("aw-watcher-agent stderr: %s", result.stderr.strip())


def _ensure_tool_start_times() -> dict[int, float]:
    tool_start_times = _tool_start_times_var.get()
    if tool_start_times is None:
        tool_start_times = {}
        _tool_start_times_var.set(tool_start_times)
    return tool_start_times


def _current_tool_session_id() -> str | None:
    manager = LogManager.get_current_log()
    logdir = getattr(manager, "logdir", None) if manager is not None else None
    if logdir is not None:
        return Path(logdir).name
    return current_session_id.get()


def emit_start(
    logdir: Path,
    workspace: Path | None,
    initial_msgs: list[Message],
) -> Generator[Message | StopPropagation, None, None]:
    """Emit a session-start event keyed by the conversation/logdir id."""
    del initial_msgs
    if not _enabled():
        return
    session_id = logdir.name
    argv = _command_prefix() + ["emit-start", *_base_args(session_id, workspace)]
    _run_aw(argv)
    return
    yield


def emit_end(
    manager: LogManager, **kwargs
) -> Generator[Message | StopPropagation, None, None]:
    """Emit a session-end event matching the earlier session-start emission."""
    if not _enabled():
        return
    logdir = getattr(manager, "logdir", None) or kwargs.get("logdir")
    workspace = getattr(manager, "workspace", None)
    if logdir is None:
        return
    session_id = Path(logdir).name
    argv = _command_prefix() + ["emit-end", *_base_args(session_id, workspace)]
    _run_aw(argv)
    return
    yield


def record_tool_start(
    data: ToolExecutePreData,
) -> Generator[Message | StopPropagation, None, None]:
    """Record the start time for a tool so the post hook can emit duration."""
    if not _enabled():
        return
    tool_start_times = _ensure_tool_start_times()
    # Prune stale entries from tools whose POST hook was never called (e.g. exception).
    # Do this at PRE time so the POST path stays side-effect-free.
    now = time.monotonic()
    stale = [k for k, v in tool_start_times.items() if now - v > _MAX_TOOL_AGE_S]
    for k in stale:
        del tool_start_times[k]
    if data.tool_use is not None:
        tool_start_times[id(data.tool_use)] = now
    _tool_start_times_var.set(tool_start_times)
    return
    yield


def emit_tool_activity(
    data: ToolExecutePostData,
) -> Generator[Message | StopPropagation, None, None]:
    """Emit one per-tool activity heartbeat after a tool finishes."""
    if not _enabled():
        return

    tool_use = data.tool_use
    workspace = data.workspace
    if tool_use is None:
        return

    session_id = _current_tool_session_id()
    if not session_id:
        return

    tool_start_times = _ensure_tool_start_times()
    started_at = tool_start_times.pop(id(tool_use), None)
    _tool_start_times_var.set(tool_start_times)
    duration_ms = 0
    if started_at is not None:
        duration_ms = max(int(round((time.monotonic() - started_at) * 1000)), 0)

    argv = _command_prefix() + [
        "emit-activity",
        *_base_args(session_id, workspace),
        "--tool",
        tool_use.tool,
        "--status",
        # TOOL_EXECUTE_POST only fires on the success path in tools/base.py;
        # exceptions skip the post hook entirely, so "success" is accurate here.
        "success",
        "--duration-ms",
        str(duration_ms),
    ]
    _run_aw(argv)
    return
    yield


def register() -> None:
    """Register aw-watcher-agent lifecycle hooks."""
    register_hook("aw_watcher_agent.start", HookType.SESSION_START, emit_start)
    register_hook("aw_watcher_agent.end", HookType.SESSION_END, emit_end)
    register_hook(
        "aw_watcher_agent.tool_pre", HookType.TOOL_EXECUTE_PRE, record_tool_start
    )
    register_hook(
        "aw_watcher_agent.tool_post", HookType.TOOL_EXECUTE_POST, emit_tool_activity
    )
    logger.debug("Registered aw-watcher-agent hooks")


def _init_from_config(config: object) -> None:
    """Enable the plugin when ``[plugin.aw_watcher_agent]`` exists."""
    user_cfg = getattr(getattr(config, "user", None), "plugin", {}) or {}
    project = getattr(config, "project", None)
    project_cfg = getattr(project, "plugin", {}) or {} if project else {}

    merged: dict[str, object] = {}
    if isinstance(user_cfg, dict):
        merged.update(user_cfg.get("aw_watcher_agent", {}) or {})
    if isinstance(project_cfg, dict):
        merged.update(project_cfg.get("aw_watcher_agent", {}) or {})

    if (
        merged
        or (isinstance(user_cfg, dict) and "aw_watcher_agent" in user_cfg)
        or (isinstance(project_cfg, dict) and "aw_watcher_agent" in project_cfg)
    ):
        os.environ.setdefault("GPTME_AW_WATCHER_AGENT", "1")

    config_to_env = {
        "command": "GPTME_AW_WATCHER_AGENT_COMMAND",
        "server": "GPTME_AW_WATCHER_SERVER",
        "hostname": "GPTME_AW_WATCHER_HOSTNAME",
        "trigger": "GPTME_AW_WATCHER_TRIGGER",
        "category": "GPTME_AW_WATCHER_CATEGORY",
    }
    for key, env_name in config_to_env.items():
        value = merged.get(key)
        if value not in (None, ""):
            os.environ.setdefault(env_name, str(value))


plugin = GptmePlugin(
    name="aw_watcher_agent",
    register_hooks=register,
    init=_init_from_config,
)
