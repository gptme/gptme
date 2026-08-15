"""Agent-Devtools trace exporter built on gptme's existing hook surface.

This plugin is intentionally narrow:

- opt-in via ``[plugin.agent_devtools]`` or environment variables
- exports lifecycle events over best-effort HTTP POSTs
- defaults to privacy-safe summaries instead of raw prompts/tool payloads
- never blocks or changes normal gptme execution

The exporter observes existing hooks. Blocking policy remains owned by
``tool.confirm`` and related confirmation hooks.
"""

from __future__ import annotations

import logging
import os
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import requests

from ..hooks import (
    HookType,
    current_conversation_id,
    current_session_id,
    register_hook,
)
from ..message import Message
from ..plugins.plugin import GptmePlugin

if TYPE_CHECKING:
    from collections.abc import Generator

    from ..hooks import StopPropagation
    from ..hooks.types import ToolExecutePostData, ToolExecutePreData
    from ..logmanager import Log, LogManager
    from ..tools.base import ToolUse

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 1.0
_MAX_TOOL_AGE_S = 300.0
_tool_start_times_var: ContextVar[dict[int, float] | None] = ContextVar(
    "agent_devtools_tool_start_times",
    default=None,
)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _enabled() -> bool:
    return _truthy(os.environ.get("GPTME_AGENT_DEVTOOLS")) or bool(_endpoint())


def _endpoint() -> str | None:
    raw = os.environ.get("GPTME_AGENT_DEVTOOLS_ENDPOINT", "").strip()
    return raw or None


def _timeout_s() -> float:
    raw = os.environ.get("GPTME_AGENT_DEVTOOLS_TIMEOUT", "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT_S
    try:
        return max(float(raw), 0.05)
    except ValueError:
        return _DEFAULT_TIMEOUT_S


def _include_sensitive() -> bool:
    return _truthy(os.environ.get("GPTME_AGENT_DEVTOOLS_INCLUDE_SENSITIVE"))


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _workspace_str(workspace: Path | None) -> str | None:
    return str(workspace) if workspace is not None else None


def _conversation_and_session_ids(
    *,
    manager: LogManager | None = None,
    logdir: Path | None = None,
) -> tuple[str | None, str | None]:
    conversation_id = current_conversation_id.get()
    session_id = current_session_id.get()

    derived = None
    if manager is not None:
        derived = getattr(manager, "chat_id", None)
        if derived is None and getattr(manager, "logdir", None) is not None:
            derived = Path(manager.logdir).name
    elif logdir is not None:
        derived = logdir.name

    if conversation_id is None:
        conversation_id = derived
    if session_id is None:
        session_id = conversation_id or derived

    return conversation_id, session_id


def _messages_from_log(log: Log | None) -> list[Message]:
    if log is None:
        return []
    messages = getattr(log, "messages", None)
    if isinstance(messages, list):
        return cast(list[Message], messages)
    return []


def _turn_index(messages: list[Message]) -> int | None:
    user_count = sum(1 for msg in messages if msg.role == "user")
    if user_count == 0:
        return None
    return user_count - 1


def _safe_text_summary(text: str | None) -> dict[str, Any]:
    if not text:
        return {"chars": 0}
    return {"chars": len(text), "lines": text.count("\n") + 1}


def _message_text(messages: tuple[Message, ...] | None) -> str | None:
    if not messages:
        return None
    chunks = [msg.content for msg in messages if isinstance(msg.content, str)]
    if not chunks:
        return None
    return "\n".join(chunks)


def _tool_payload_preview(
    tool_use: ToolUse, *, include_sensitive: bool
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": tool_use.tool,
        "call_id": tool_use.call_id,
        "format": tool_use._format,
        "args_preview": {
            "arg_count": len(tool_use.args or []),
            "kwarg_keys": sorted((tool_use.kwargs or {}).keys()),
            "has_content": bool(tool_use.content),
            "content_summary": _safe_text_summary(tool_use.content),
        },
    }
    if include_sensitive:
        payload["args"] = list(tool_use.args or [])
        if tool_use.kwargs is not None:
            payload["kwargs"] = dict(tool_use.kwargs)
        if tool_use.content is not None:
            payload["content"] = tool_use.content
    return payload


def _usage_from_message(message: Message) -> dict[str, Any]:
    usage: dict[str, Any] = {"output_chars": len(message.content or "")}
    metadata = message.metadata or {}
    if model := metadata.get("model"):
        usage["model"] = model
    if resolved_model := metadata.get("resolved_model"):
        usage["resolved_model"] = resolved_model
    if usage_meta := metadata.get("usage"):
        usage.update(cast(dict[str, Any], usage_meta))
    if timings := metadata.get("timings"):
        usage.update(cast(dict[str, Any], timings))
    return usage


def _base_envelope(
    event: str,
    *,
    workspace: Path | None = None,
    manager: LogManager | None = None,
    logdir: Path | None = None,
    turn_index: int | None = None,
    step_index: int | None = None,
) -> dict[str, Any]:
    conversation_id, session_id = _conversation_and_session_ids(
        manager=manager, logdir=logdir
    )
    return {
        "schema_version": 1,
        "event": event,
        "timestamp": _iso_now(),
        "session_id": session_id,
        "conversation_id": conversation_id,
        "workspace": _workspace_str(workspace),
        "turn_index": turn_index,
        "step_index": step_index,
    }


def _send_event(envelope: dict[str, Any]) -> None:
    endpoint = _endpoint()
    if not _enabled() or endpoint is None:
        return
    try:
        response = requests.post(
            endpoint,
            json=envelope,
            timeout=_timeout_s(),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "gptme-agent-devtools/1",
            },
        )
        if response.status_code >= 400:
            logger.warning(
                "agent_devtools sink returned %s for %s",
                response.status_code,
                envelope.get("event"),
            )
    except requests.RequestException as exc:
        logger.debug("agent_devtools export failed open: %s", exc)


def _ensure_tool_start_times() -> dict[int, float]:
    tool_start_times = _tool_start_times_var.get()
    if tool_start_times is None:
        tool_start_times = {}
        _tool_start_times_var.set(tool_start_times)
    return tool_start_times


def _remember_tool_start(
    data: ToolExecutePreData,
) -> Generator[Message | StopPropagation, None, None]:
    if not _enabled() or data.tool_use is None:
        return
    tool_start_times = _ensure_tool_start_times()
    now = time.monotonic()
    stale = [k for k, v in tool_start_times.items() if now - v > _MAX_TOOL_AGE_S]
    for key in stale:
        del tool_start_times[key]
    tool_start_times[id(data.tool_use)] = now
    _tool_start_times_var.set(tool_start_times)
    return
    yield


def emit_session_start(
    logdir: Path,
    workspace: Path | None,
    initial_msgs: list[Message],
) -> Generator[Message | StopPropagation, None, None]:
    if not _enabled():
        return
    envelope = _base_envelope(
        HookType.SESSION_START.value,
        workspace=workspace,
        logdir=logdir,
        turn_index=_turn_index(initial_msgs),
    )
    envelope["context"] = {"initial_message_count": len(initial_msgs)}
    _send_event(envelope)
    return
    yield


def emit_session_end(
    manager: LogManager,
    **kwargs: Any,
) -> Generator[Message | StopPropagation, None, None]:
    del kwargs
    if not _enabled():
        return
    envelope = _base_envelope(
        HookType.SESSION_END.value,
        workspace=manager.workspace,
        manager=manager,
        turn_index=_turn_index(manager.log.messages),
    )
    envelope["context"] = {"message_count": len(manager.log.messages)}
    _send_event(envelope)
    return
    yield


def emit_turn_pre(
    manager: LogManager,
) -> Generator[Message | StopPropagation, None, None]:
    if not _enabled():
        return
    messages = manager.log.messages
    envelope = _base_envelope(
        HookType.TURN_PRE.value,
        workspace=manager.workspace,
        manager=manager,
        turn_index=_turn_index(messages),
    )
    envelope["context"] = {"message_count": len(messages)}
    _send_event(envelope)
    return
    yield


def emit_tool_pre(
    data: ToolExecutePreData,
) -> Generator[Message | StopPropagation, None, None]:
    if not _enabled() or data.tool_use is None:
        return
    messages = _messages_from_log(data.log)
    envelope = _base_envelope(
        HookType.TOOL_EXECUTE_PRE.value,
        workspace=data.workspace,
        turn_index=_turn_index(messages),
    )
    envelope["tool"] = _tool_payload_preview(
        data.tool_use, include_sensitive=_include_sensitive()
    )
    _send_event(envelope)
    return
    yield


def emit_tool_post(
    data: ToolExecutePostData,
) -> Generator[Message | StopPropagation, None, None]:
    if not _enabled() or data.tool_use is None:
        return
    messages = _messages_from_log(data.log)
    include_sensitive = _include_sensitive()
    envelope = _base_envelope(
        HookType.TOOL_EXECUTE_POST.value,
        workspace=data.workspace,
        turn_index=_turn_index(messages),
    )
    tool = _tool_payload_preview(data.tool_use, include_sensitive=include_sensitive)
    result_text = _message_text(data.result_msgs)
    if include_sensitive and result_text is not None:
        tool["result"] = result_text
    elif data.result_msgs:
        tool["result_preview"] = _safe_text_summary(result_text)
    envelope["tool"] = tool

    started = _ensure_tool_start_times().pop(id(data.tool_use), None)
    usage: dict[str, Any] = {"success": True}
    if started is not None:
        usage["duration_ms"] = max(int(round((time.monotonic() - started) * 1000)), 0)
    if data.result_msgs:
        usage["result_message_count"] = len(data.result_msgs)
    envelope["usage"] = usage
    _send_event(envelope)
    return
    yield


def emit_generation_pre(
    messages: list[Message],
    **kwargs: Any,
) -> Generator[Message | StopPropagation, None, None]:
    if not _enabled():
        return
    workspace = kwargs.get("workspace")
    model = kwargs.get("model")
    envelope = _base_envelope(
        HookType.GENERATION_PRE.value,
        workspace=workspace,
        turn_index=_turn_index(messages),
    )
    envelope["context"] = {"message_count": len(messages), "model": model}
    _send_event(envelope)
    return
    yield


def emit_generation_post(
    message: Message,
    **kwargs: Any,
) -> Generator[Message | StopPropagation, None, None]:
    if not _enabled():
        return
    workspace = kwargs.get("workspace")
    envelope = _base_envelope(
        HookType.GENERATION_POST.value,
        workspace=workspace,
    )
    envelope["usage"] = _usage_from_message(message)
    if _include_sensitive():
        envelope["assistant"] = {"content": message.content}
    _send_event(envelope)
    return
    yield


def register() -> None:
    register_hook(
        "agent_devtools.tool_pre_state",
        HookType.TOOL_EXECUTE_PRE,
        _remember_tool_start,
        priority=100,
    )
    register_hook(
        "agent_devtools.session_start",
        HookType.SESSION_START,
        emit_session_start,
        async_mode=True,
    )
    register_hook(
        "agent_devtools.session_end",
        HookType.SESSION_END,
        emit_session_end,
        async_mode=True,
    )
    register_hook(
        "agent_devtools.turn_pre",
        HookType.TURN_PRE,
        emit_turn_pre,
        async_mode=True,
    )
    register_hook(
        "agent_devtools.tool_pre",
        HookType.TOOL_EXECUTE_PRE,
        emit_tool_pre,
        async_mode=True,
    )
    register_hook(
        "agent_devtools.tool_post",
        HookType.TOOL_EXECUTE_POST,
        emit_tool_post,
        async_mode=True,
    )
    register_hook(
        "agent_devtools.generation_pre",
        HookType.GENERATION_PRE,
        emit_generation_pre,
        async_mode=True,
    )
    register_hook(
        "agent_devtools.generation_post",
        HookType.GENERATION_POST,
        emit_generation_post,
        async_mode=True,
    )
    logger.debug("Registered agent_devtools hooks")


def _init_from_config(config: object) -> None:
    user_cfg = getattr(getattr(config, "user", None), "plugin", {}) or {}
    project = getattr(config, "project", None)
    project_cfg = getattr(project, "plugin", {}) or {} if project else {}

    merged: dict[str, object] = {}
    if isinstance(user_cfg, dict):
        merged.update(user_cfg.get("agent_devtools", {}) or {})
    if isinstance(project_cfg, dict):
        merged.update(project_cfg.get("agent_devtools", {}) or {})

    if (
        merged
        or (isinstance(user_cfg, dict) and "agent_devtools" in user_cfg)
        or (isinstance(project_cfg, dict) and "agent_devtools" in project_cfg)
    ):
        os.environ.setdefault("GPTME_AGENT_DEVTOOLS", "1")

    config_to_env = {
        "endpoint": "GPTME_AGENT_DEVTOOLS_ENDPOINT",
        "timeout": "GPTME_AGENT_DEVTOOLS_TIMEOUT",
        "include_sensitive": "GPTME_AGENT_DEVTOOLS_INCLUDE_SENSITIVE",
    }
    for key, env_name in config_to_env.items():
        value = merged.get(key)
        if value not in (None, ""):
            os.environ.setdefault(env_name, str(value))


plugin = GptmePlugin(
    name="agent_devtools",
    register_hooks=register,
    init=_init_from_config,
)
