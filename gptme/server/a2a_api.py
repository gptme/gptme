"""A2A JSON-RPC surface for gptme-server."""

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import flask
from flask import request

from gptme.__version__ import __version__

from ..config import ChatConfig, Config
from ..dirs import get_logs_dir
from ..llm.models import get_default_model
from ..logmanager import LogManager
from ..message import Message
from ..prompts import get_prompt
from ..tools import get_toolchain
from .api_v2_common import _validate_conversation_id, msg2dict
from .auth import require_auth
from .session_models import ConversationSession, SessionManager
from .session_step import _start_step_thread

logger = logging.getLogger(__name__)

A2A_PROTOCOL_VERSION = "1.0"
A2A_RPC_PATH = "/api/a2a"
A2A_CONTENT_TYPE = "application/json"

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

A2A_TASK_NOT_FOUND = -32001
A2A_CONTENT_TYPE_NOT_SUPPORTED = -32005


class A2AError(Exception):
    """JSON-RPC/A2A error with a protocol code."""

    def __init__(
        self,
        code: int,
        message: str,
        *,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason
        self.metadata = metadata or {}


a2a_api = flask.Blueprint("a2a_api", __name__)


def _json_response(payload: dict[str, Any], status: int = 200) -> flask.Response:
    response = flask.jsonify(payload)
    response.status_code = status
    response.content_type = A2A_CONTENT_TYPE
    return response


def _rpc_result(request_id: Any, result: dict[str, Any]) -> flask.Response:
    return _json_response({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error_data(error: A2AError) -> list[dict[str, Any]] | None:
    if not error.reason:
        return None
    return [
        {
            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
            "reason": error.reason,
            "domain": "a2a-protocol.org",
            "metadata": {
                **error.metadata,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            },
        }
    ]


def _rpc_error(request_id: Any, error: A2AError, status: int = 200) -> flask.Response:
    error_payload: dict[str, Any] = {
        "code": error.code,
        "message": error.message,
    }
    if data := _error_data(error):
        error_payload["data"] = data
    return _json_response(
        {"jsonrpc": "2.0", "id": request_id, "error": error_payload}, status=status
    )


def _a2a_endpoint_url() -> str:
    return request.host_url.rstrip("/") + A2A_RPC_PATH


def _agent_card() -> dict[str, Any]:
    return {
        "name": "gptme",
        "description": (
            "Local-first terminal AI agent exposed through the Agent2Agent protocol."
        ),
        "supportedInterfaces": [
            {
                "url": _a2a_endpoint_url(),
                "protocolBinding": "JSONRPC",
                "protocolVersion": A2A_PROTOCOL_VERSION,
            }
        ],
        "provider": {
            "organization": "gptme",
            "url": "https://gptme.org",
        },
        "version": __version__,
        "documentationUrl": "https://gptme.org/docs/server.html",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "securitySchemes": {
            "bearerAuth": {
                "httpAuthSecurityScheme": {
                    "scheme": "Bearer",
                    "bearerFormat": "gptme-server-token",
                    "description": "Use the gptme-server bearer token.",
                }
            }
        },
        "securityRequirements": [{"bearerAuth": []}],
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "gptme-terminal-agent",
                "name": "gptme Terminal Agent",
                "description": (
                    "Answer questions, inspect workspaces, and perform "
                    "software-development tasks through gptme."
                ),
                "tags": ["coding", "terminal", "software-development", "agent"],
                "examples": [
                    "Inspect this repository and summarize the failing tests.",
                    "Implement the smallest fix for this bug and explain the change.",
                ],
                "inputModes": ["text/plain"],
                "outputModes": ["text/plain"],
            }
        ],
    }


@a2a_api.route("/.well-known/agent-card.json")
@a2a_api.route("/.well-known/agent.json")
def agent_card() -> flask.Response:
    """Return the public A2A Agent Card.

    `agent-card.json` is the A2A 1.0 well-known URI. `agent.json` is kept as a
    cheap legacy alias because early A2A docs and local planning notes used it.
    """
    return _json_response(_agent_card())


def _request_jsonrpc() -> dict[str, Any] | A2AError:
    req_json = request.get_json(silent=True)
    if req_json is None:
        if request.get_data(cache=True):
            return A2AError(JSONRPC_PARSE_ERROR, "Invalid JSON payload")
        return A2AError(JSONRPC_INVALID_REQUEST, "Request payload validation error")
    if not isinstance(req_json, dict):
        return A2AError(JSONRPC_INVALID_REQUEST, "Request payload validation error")
    if req_json.get("jsonrpc") != "2.0":
        return A2AError(JSONRPC_INVALID_REQUEST, "Request payload validation error")
    if not isinstance(req_json.get("method"), str):
        return A2AError(JSONRPC_INVALID_REQUEST, "Request payload validation error")
    return req_json


def _params_object(req_json: dict[str, Any]) -> dict[str, Any]:
    params = req_json.get("params", {})
    if not isinstance(params, dict):
        raise A2AError(JSONRPC_INVALID_PARAMS, "Invalid parameters")
    return params


def _extract_message_text(message: dict[str, Any]) -> str:
    role = message.get("role")
    if role not in ("ROLE_USER", "user"):
        raise A2AError(JSONRPC_INVALID_PARAMS, "Invalid parameters")
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts:
        raise A2AError(JSONRPC_INVALID_PARAMS, "Invalid parameters")

    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            raise A2AError(JSONRPC_INVALID_PARAMS, "Invalid parameters")
        keys = [key for key in ("text", "raw", "url", "data") if key in part]
        if len(keys) != 1:
            raise A2AError(JSONRPC_INVALID_PARAMS, "Invalid parameters")
        if keys[0] != "text":
            raise A2AError(
                A2A_CONTENT_TYPE_NOT_SUPPORTED,
                "Content type not supported",
                reason="CONTENT_TYPE_NOT_SUPPORTED",
            )
        text = part["text"]
        if not isinstance(text, str):
            raise A2AError(JSONRPC_INVALID_PARAMS, "Invalid parameters")
        texts.append(text)

    content = "\n\n".join(texts).strip()
    if not content:
        raise A2AError(JSONRPC_INVALID_PARAMS, "Invalid parameters")
    return content


def _validate_task_id(task_id: str) -> None:
    if _validate_conversation_id(task_id):
        raise A2AError(JSONRPC_INVALID_PARAMS, "Invalid parameters")


def _new_task_id() -> str:
    return f"a2a-{uuid.uuid4().hex}"


def _request_chat_config(logdir: Path) -> ChatConfig:
    config_dict: dict[str, Any] = {
        "_logdir": logdir,
        "chat": {
            "workspace": "@log",
        },
    }
    return ChatConfig.from_dict(config_dict, create_workspace=False)


def _create_task_conversation(task_id: str, user_text: str) -> ConversationSession:
    logdir = get_logs_dir() / task_id
    request_config = _request_chat_config(logdir)
    logdir.mkdir(parents=True, exist_ok=False)
    chat_config = ChatConfig.load_or_create(logdir, request_config)

    msgs = get_prompt(
        tools=list(get_toolchain(chat_config.tools, strict=False)),
        interactive=chat_config.interactive,
        tool_format=chat_config.tool_format or "markdown",
        model=chat_config.model,
        prompt="full",
        workspace=chat_config.workspace,
        agent_path=chat_config.agent,
    )
    msgs.append(Message("user", user_text))

    manager = LogManager.load(logdir=logdir, initial_msgs=msgs, create=True)
    manager.write()

    if not chat_config.tools:
        chat_config.tools = [
            tool.name for tool in get_toolchain(None) if not tool.is_mcp
        ]
    if not chat_config.mcp:
        config = Config.from_workspace(chat_config.workspace)
        chat_config.mcp = config.mcp
    chat_config.save()

    session = SessionManager.create_session(task_id)
    SessionManager.add_event(
        task_id,
        {
            "type": "message_added",
            "message": msg2dict(msgs[-1], manager.workspace, manager.logdir),
        },
    )
    return session


def _append_task_message(task_id: str, user_text: str) -> ConversationSession:
    manager = LogManager.load(task_id, lock=False)
    msg = Message("user", user_text)
    manager.append(msg)

    sessions = SessionManager.get_sessions_for_conversation(task_id)
    session = max(sessions, key=lambda item: item.last_activity, default=None)
    if session is None:
        session = SessionManager.create_session(task_id)
    SessionManager.add_event(
        task_id,
        {
            "type": "message_added",
            "message": msg2dict(msg, manager.workspace, manager.logdir),
        },
    )
    return session


def _resolve_model(chat_config: ChatConfig) -> str:
    if chat_config.model:
        return chat_config.model
    if default_model := get_default_model():
        return default_model.full
    if model := Config.from_workspace(chat_config.workspace).get_env("MODEL"):
        return model
    raise A2AError(JSONRPC_INVALID_PARAMS, "No model specified")


def _run_task_blocking(task_id: str, session: ConversationSession) -> None:
    logdir = get_logs_dir() / task_id
    chat_config = ChatConfig.load_or_create(logdir, ChatConfig())
    model = _resolve_model(chat_config)
    initial_event_count = session.events_count

    _start_step_thread(
        conversation_id=task_id,
        session=session,
        model=model,
        workspace=chat_config.workspace,
        branch="main",
        auto_confirm=False,
        stream=False,
    )

    timeout = float(os.environ.get("GPTME_A2A_BLOCKING_TIMEOUT", "120"))
    deadline = time.monotonic() + timeout
    event_index = initial_event_count

    while time.monotonic() < deadline:
        events = session.get_events_since(event_index)
        event_index += len(events)
        for event in events:
            event_type = event.get("type") if isinstance(event, dict) else None
            if event_type == "generation_complete":
                return
            if event_type == "error":
                raise A2AError(
                    JSONRPC_INTERNAL_ERROR,
                    str(event.get("error") or "Internal error"),
                )
        if not session.generating:
            return
        session.event_flag.clear()
        session.event_flag.wait(timeout=0.1)

    logger.info("A2A SendMessage timed out waiting for task %s", task_id)
    # Return task in WORKING state so the client knows to poll with GetTask.


def _a2a_message_from_log(
    msg: Message,
    task_id: str,
    context_id: str,
    index: int,
) -> dict[str, Any]:
    role = "ROLE_AGENT" if msg.role == "assistant" else "ROLE_USER"
    return {
        "messageId": f"{task_id}-{index}",
        "taskId": task_id,
        "contextId": context_id,
        "role": role,
        "parts": [{"text": msg.content, "mediaType": "text/plain"}],
    }


def _task_from_conversation(
    task_id: str,
    *,
    history_length: int | None = None,
) -> dict[str, Any]:
    try:
        manager = LogManager.load(task_id, lock=False)
    except FileNotFoundError as exc:
        raise A2AError(
            A2A_TASK_NOT_FOUND,
            "Task not found",
            reason="TASK_NOT_FOUND",
            metadata={"taskId": task_id},
        ) from exc

    context_id = task_id
    messages = [
        message
        for message in manager.log.messages
        if message.role in ("user", "assistant")
    ]
    sessions = SessionManager.get_sessions_for_conversation(task_id)
    latest_session = max(sessions, key=lambda item: item.last_activity, default=None)
    latest_assistant_index, latest_assistant = next(
        (
            (len(messages) - 1 - i, m)
            for i, m in enumerate(reversed(messages))
            if m.role == "assistant"
        ),
        (-1, None),
    )

    if latest_session and latest_session.generating:
        state = "TASK_STATE_WORKING"
    elif latest_session and latest_session.last_error:
        state = "TASK_STATE_FAILED"
    elif latest_assistant:
        state = "TASK_STATE_COMPLETED"
    else:
        state = "TASK_STATE_SUBMITTED"

    timestamp_source = messages[-1] if messages else None
    timestamp = (
        timestamp_source.timestamp
        if timestamp_source is not None
        else datetime.now(tz=timezone.utc)
    )

    task: dict[str, Any] = {
        "id": task_id,
        "contextId": context_id,
        "status": {
            "state": state,
            "timestamp": timestamp.isoformat(),
        },
        "metadata": {
            "conversationId": task_id,
            "provider": "gptme",
        },
    }

    if latest_assistant is not None:
        response_message = _a2a_message_from_log(
            latest_assistant, task_id, context_id, latest_assistant_index
        )
        task["status"]["message"] = response_message
        task["artifacts"] = [
            {
                "artifactId": "assistant-response",
                "name": "Assistant response",
                "parts": [
                    {"text": latest_assistant.content, "mediaType": "text/plain"}
                ],
            }
        ]

    if history_length is None:
        history_messages = messages
    elif history_length <= 0:
        history_messages = []
    else:
        history_messages = messages[-history_length:]
    if history_messages:
        start_index = len(messages) - len(history_messages)
        task["history"] = [
            _a2a_message_from_log(message, task_id, context_id, start_index + offset)
            for offset, message in enumerate(history_messages)
        ]

    return task


def _handle_send_message(params: dict[str, Any]) -> dict[str, Any]:
    message = params.get("message")
    if not isinstance(message, dict):
        raise A2AError(JSONRPC_INVALID_PARAMS, "Invalid parameters")

    user_text = _extract_message_text(message)
    task_id = message.get("taskId")
    context_id = message.get("contextId")
    if task_id is not None and not isinstance(task_id, str):
        raise A2AError(JSONRPC_INVALID_PARAMS, "Invalid parameters")
    if context_id is not None and not isinstance(context_id, str):
        raise A2AError(JSONRPC_INVALID_PARAMS, "Invalid parameters")
    if task_id and context_id and task_id != context_id:
        raise A2AError(JSONRPC_INVALID_PARAMS, "Invalid parameters")

    if task_id:
        _validate_task_id(task_id)
        if not (get_logs_dir() / task_id).exists():
            raise A2AError(
                A2A_TASK_NOT_FOUND,
                "Task not found",
                reason="TASK_NOT_FOUND",
                metadata={"taskId": task_id},
            )
        session = _append_task_message(task_id, user_text)
    else:
        task_id = _new_task_id()
        session = _create_task_conversation(task_id, user_text)

    _run_task_blocking(task_id, session)
    return {"task": _task_from_conversation(task_id)}


def _handle_get_task(params: dict[str, Any]) -> dict[str, Any]:
    task_id = params.get("id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise A2AError(JSONRPC_INVALID_PARAMS, "Invalid parameters")
    _validate_task_id(task_id)

    history_length = params.get("historyLength")
    if history_length is not None and type(history_length) is not int:
        raise A2AError(JSONRPC_INVALID_PARAMS, "Invalid parameters")
    return {"task": _task_from_conversation(task_id, history_length=history_length)}


@a2a_api.route(A2A_RPC_PATH, methods=["POST"])
@require_auth
def a2a_rpc() -> flask.Response:
    req_json = _request_jsonrpc()
    if isinstance(req_json, A2AError):
        return _rpc_error(None, req_json)

    request_id = req_json.get("id")
    method = req_json["method"]

    try:
        params = _params_object(req_json)
        if method == "SendMessage":
            result = _handle_send_message(params)
        elif method == "GetTask":
            result = _handle_get_task(params)
        else:
            raise A2AError(JSONRPC_METHOD_NOT_FOUND, "Method not found")
    except A2AError as exc:
        return _rpc_error(request_id, exc)
    except Exception:
        logger.exception("Unhandled A2A RPC error")
        return _rpc_error(
            request_id,
            A2AError(JSONRPC_INTERNAL_ERROR, "Internal error"),
        )

    return _rpc_result(request_id, result)
