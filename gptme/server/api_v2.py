"""
V2 API for gptme server with improved control flow and conversation management.

This module contains the main conversation CRUD endpoints for the V2 API.
Session management, tool execution, and agent creation are handled by separate modules.
"""

import logging
import os
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Literal, cast

import flask
from dateutil.parser import isoparse
from flask import request

from gptme.__version__ import __version__
from gptme.config import (
    ChatConfig,
    Config,
    get_config,
    load_user_config,
    set_config,
    set_config_value,
)
from gptme.llm import PROVIDER_API_KEYS, list_available_providers
from gptme.llm.models import (
    PROVIDERS,
    Provider,
    _apply_model_filters,
    _get_models_for_provider,
    get_default_model,
    get_model,
    get_recommended_model,
    set_default_model,
)
from gptme.prompts import get_prompt

from ..commands import handle_cmd
from ..config import get_project_config
from ..config.user import get_user_config_env_source, get_user_config_runtime_info
from ..dirs import get_logs_dir
from ..logmanager import Log, LogManager, get_user_conversations
from ..message import Message
from ..tools import get_toolchain, get_tools, init_tools
from ..util.content import is_message_command
from ..util.uri import parse_file_reference
from .api_v2_agents import agents_api
from .api_v2_common import (
    _abs_to_rel_workspace,
    _validate_branch,
    _validate_conversation_id,
    msg2dict,
)
from .api_v2_sessions import SessionManager, sessions_api
from .auth import require_auth
from .external_sessions import get_external_session_provider
from .openapi_docs import (
    CONVERSATION_ID_PARAM,
    ApiRootResponse,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationResponse,
    ErrorResponse,
    ExternalSessionListResponse,
    ExternalSessionResponse,
    MessageCreateRequest,
    SessionResponse,
    StatusResponse,
    UserApiKeySaveRequest,
    UserApiKeySaveResponse,
    UserDefaultModelSaveRequest,
    UserDefaultModelSaveResponse,
    UserSettingsResponse,
    api_doc,
    api_doc_simple,
)

logger = logging.getLogger(__name__)

# Raster image extensions allowed for user avatars.
# SVG excluded: can embed <script> tags (XSS via crafted SVG).
_ALLOWED_AVATAR_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".ico"}


def _is_valid_image_content(path: "Path") -> bool:
    """Validate file content is a recognised image format using Pillow.

    Extension checks can be bypassed by renaming a file; this validates the
    actual content via magic-byte / header parsing.  Pillow is already a hard
    runtime dependency (needed for vision support), so no extra install cost.
    """
    try:
        from PIL import Image, UnidentifiedImageError

        with Image.open(path) as img:
            _ = img.format  # triggers format detection from file content
        return True
    except (UnidentifiedImageError, FileNotFoundError, IsADirectoryError, OSError):
        return False
    except Exception:
        logger.warning("Unexpected error validating image %s", path, exc_info=True)
        return False


def _validate_model_input(model: str, expected_provider: str | None = None) -> str:
    """Validate a fully qualified provider/model identifier."""
    trimmed_model = model.strip()
    if not trimmed_model:
        raise ValueError("model must not be empty")
    if "/" not in trimmed_model:
        raise ValueError("model must be fully qualified as provider/model")

    provider, model_name = trimmed_model.split("/", 1)
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")
    if not model_name.strip():
        raise ValueError("model must include a model name after the provider prefix")
    if expected_provider and provider != expected_provider:
        raise ValueError(
            f"Model {trimmed_model} does not match provider {expected_provider}"
        )
    return trimmed_model


def _persist_default_model(model: str) -> bool:
    """Persist env.MODEL and try to apply it in-process.

    Returns True if a restart is still required to guarantee the change takes effect.
    """
    set_config_value("env.MODEL", model, reload=False)

    model_meta = get_model(model)
    try:
        from gptme.llm import init_llm

        init_llm(cast(Provider, model_meta.provider))
        set_default_model(model_meta)
        flask.current_app.config["SERVER_DEFAULT_MODEL"] = model_meta
        return False
    except Exception:
        logger.warning(
            "Persisted default model %s but could not apply it in-process; restart required",
            model,
            exc_info=True,
        )
        return True


def _validate_api_key_input(api_key: str) -> str:
    """Apply lightweight sanity checks before persisting a user-supplied API key."""
    trimmed = api_key.strip()
    if not trimmed:
        raise ValueError("API key is empty")
    if len(trimmed) > 4096:
        raise ValueError("API key is too long")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in trimmed):
        raise ValueError("API key contains control characters")
    return trimmed


v2_api = flask.Blueprint("v2_api", __name__)

# Register sub-blueprints
v2_api.register_blueprint(sessions_api)
v2_api.register_blueprint(agents_api)


@v2_api.route("/api/v2")
@api_doc_simple(responses={200: ApiRootResponse}, tags=["meta"])
def api_root():
    """V2 API root.

    Get information about the v2 API, including available endpoints and capabilities.
    """
    provider = get_external_session_provider()
    capabilities = {
        "external_session_catalog": False,
        "external_session_transcript": False,
    }
    if provider is not None:
        capabilities.update(provider.capabilities)

    provider_configured = get_default_model() is not None

    return flask.jsonify(
        {
            "message": "gptme v2 API",
            "documentation": "https://gptme.org/docs/server.html",
            "version": __version__,
            "capabilities": capabilities,
            "provider_configured": provider_configured,
        }
    )


@v2_api.route("/api/v2/config")
@api_doc()
def api_config():
    """Agent configuration endpoint.

    Returns workspace agent configuration including named URLs (dashboard, repo, etc.)
    configured in ``gptme.toml`` under ``[agent.urls]``.

    Clients can use the ``dashboard`` URL to link to or embed the agent's dashboard.

    Response schema::

        {
          "agent": {
            "name": "bob",               // agent name from gptme.toml [agent] name
            "urls": {                     // from [agent.urls] section (may be empty)
              "dashboard": "https://...", // static dashboard URL (gh-pages)
              "dashboard-api": "https://..." // live dashboard API URL (optional)
            }
          }
        }
    """
    config = get_config()
    agent = config.project.agent if config.project else None

    agent_info: dict = {}
    if agent:
        agent_info["name"] = agent.name
        if agent.urls:
            agent_info["urls"] = agent.urls

    return flask.jsonify({"agent": agent_info})


@v2_api.route("/api/v2/external-sessions")
@require_auth
@api_doc_simple(
    responses={200: ExternalSessionListResponse, 503: ErrorResponse},
    tags=["external-sessions"],
    parameters=[
        {
            "name": "limit",
            "in": "query",
            "schema": {"type": "integer", "default": 100},
            "description": "Maximum number of external sessions to return",
        },
        {
            "name": "days",
            "in": "query",
            "schema": {"type": "integer", "default": 30},
            "description": "How many recent days of session history to scan",
        },
    ],
)
def api_external_sessions():
    """List read-only external sessions discovered by the server."""
    provider = get_external_session_provider()
    if provider is None:
        return flask.jsonify({"error": "external session provider unavailable"}), 503

    try:
        limit = int(request.args.get("limit", 100))
        days = int(request.args.get("days", 30))
    except (ValueError, TypeError):
        return flask.jsonify({"error": "limit and days must be integers"}), 400

    limit = max(1, min(limit, 1000))
    days = max(1, min(days, 3650))

    sessions = [
        item.to_dict() for item in provider.list_sessions(limit=limit, days=days)
    ]
    return flask.jsonify({"sessions": sessions})


@v2_api.route("/api/v2/external-sessions/<string:external_session_id>")
@require_auth
@api_doc_simple(
    responses={200: ExternalSessionResponse, 404: ErrorResponse, 503: ErrorResponse},
    tags=["external-sessions"],
    parameters=[
        {
            "name": "external_session_id",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
            "description": "Opaque external session identifier",
        },
        {
            "name": "days",
            "in": "query",
            "schema": {"type": "integer", "default": 30},
            "description": "How many recent days of session history to scan",
        },
    ],
)
def api_external_session(external_session_id: str):
    """Get a normalized read-only external session transcript."""
    provider = get_external_session_provider()
    if provider is None:
        return flask.jsonify({"error": "external session provider unavailable"}), 503

    try:
        days = int(request.args.get("days", 30))
    except (ValueError, TypeError):
        return flask.jsonify({"error": "days must be an integer"}), 400
    days = max(1, min(days, 3650))

    session = provider.get_session(external_session_id, days=days)
    if session is None:
        return flask.jsonify(
            {"error": f"External session not found: {external_session_id}"}
        ), 404
    return flask.jsonify(session)


@v2_api.route("/api/v2/conversations")
@require_auth
@api_doc_simple(
    responses={200: ConversationListResponse, 500: ErrorResponse},
    tags=["conversations-v2"],
    parameters=[
        {
            "name": "limit",
            "in": "query",
            "schema": {"type": "integer", "default": 100},
            "description": "Maximum number of conversations to return",
        },
        {
            "name": "search",
            "in": "query",
            "schema": {"type": "string"},
            "description": "Filter conversations by name, id, or last message preview (case-insensitive substring match)",
        },
    ],
)
def api_conversations():
    """List conversations (V2).

    Get a list of user conversations with metadata using the V2 API.
    Supports optional search filtering by conversation name, id, or last message preview.
    """
    try:
        limit = int(request.args.get("limit", 100))
    except (ValueError, TypeError):
        return flask.jsonify({"error": "limit must be an integer"}), 400
    limit = max(1, min(limit, 1000))
    search = request.args.get("search", "").strip().lower()

    # Use fast tail-only scan for list/search — reads last 8KB for
    # preview/model, skips json.loads() on every metadata line.
    # Full file is still read for line count, but without JSON parsing.
    if search:
        conversations = []
        for conv in get_user_conversations(detail=False):
            if (
                search in conv.name.lower()
                or search in conv.id.lower()
                or search in (conv.last_message_preview or "").lower()
            ):
                conversations.append(conv)
                if len(conversations) >= limit:
                    break
    else:
        conversations = list(islice(get_user_conversations(detail=False), limit))
    return flask.jsonify(conversations)


@v2_api.route("/api/v2/conversations/<string:conversation_id>")
@require_auth
@api_doc_simple(
    responses={200: ConversationResponse, 404: ErrorResponse}, tags=["conversations-v2"]
)
def api_conversation(conversation_id: str):
    """Get conversation (V2).

    Retrieve a conversation with all its messages and metadata using the V2 API.
    """
    # Validate conversation_id to prevent path traversal
    if error := _validate_conversation_id(conversation_id):
        return error

    try:
        manager = LogManager.load(conversation_id, lock=False)
    except FileNotFoundError:
        return flask.jsonify(
            {"error": f"Conversation not found: {conversation_id}"}
        ), 404

    # Create and set config
    logdir = get_logs_dir() / conversation_id
    chat_config = ChatConfig.load_or_create(logdir, ChatConfig()).save()
    log_dict = manager.to_dict(branches=True)

    # make all paths relative to workspace or logdir (no "../" or absolute paths)
    for msg in log_dict["log"]:
        if files := msg.get("files"):
            msg["files"] = [
                _abs_to_rel_workspace(f, chat_config.workspace, manager.logdir)
                for f in files
            ]

    # Include agent info if available
    agent_config = chat_config.agent_config
    if agent_config:
        log_dict["agent"] = {
            "name": agent_config.name,
            "avatar": agent_config.avatar,
            "urls": agent_config.urls or None,
        }
        if chat_config.agent:
            log_dict["agent"]["path"] = str(chat_config.agent)

    # Surface session-level state so REST polling clients can see generation
    # status and the last step error without subscribing to SSE.
    # Errors during /step propagate via SSE error events, but clients that
    # only poll the conversation would otherwise see {"status": "ok"} from
    # /step and never learn the LLM call failed.
    sessions = SessionManager.get_sessions_for_conversation(conversation_id)
    if sessions:
        # Pick the most recently active session — it's the one a polling
        # client would care about.
        latest = max(sessions, key=lambda s: s.last_activity)
        log_dict["session"] = {
            "id": latest.id,
            "generating": latest.generating,
            "last_error": latest.last_error,
        }

    return flask.jsonify(log_dict)


@v2_api.route("/api/v2/conversations/<string:conversation_id>", methods=["PUT"])
@require_auth
@api_doc(
    summary="Create conversation (V2)",
    description="Create a new conversation with initial configuration and messages using the V2 API",
    request_body=ConversationCreateRequest,
    responses={200: SessionResponse, 409: ErrorResponse, 400: ErrorResponse},
    parameters=[
        {
            "name": "conversation_id",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
            "description": "Conversation ID",
        }
    ],
    tags=["conversations-v2"],
)
def api_conversation_put(conversation_id: str):
    """Create a new conversation."""
    # Validate conversation_id to prevent path traversal
    if error := _validate_conversation_id(conversation_id):
        return error

    logdir = get_logs_dir() / conversation_id

    req_json = flask.request.json or {}

    # Validate auto_confirm type before any side effects (CWE-20: truthy coercion).
    # "false" (string) is truthy in Python — must reject non-bool/int values.
    # Same pattern as api_v2_sessions.py.
    auto_confirm = req_json.get("auto_confirm", False)
    if type(auto_confirm) not in (bool, int):
        return (
            flask.jsonify(
                {
                    "error": "Invalid 'auto_confirm' value",
                    "message": "'auto_confirm' must be a boolean or integer",
                }
            ),
            400,
        )

    # Validate all messages before creating any side effects (directories).
    # This prevents orphaned directories when validation fails: if logdir.mkdir()
    # runs before a 400 is returned, the same conversation_id gets a 409 on retry.
    _RoleType = Literal["system", "user", "assistant"]
    valid_roles = ("system", "user", "assistant")
    validated_msgs: list[tuple[_RoleType, str, datetime]] = []
    for msg in req_json.get("messages", []):
        if msg.get("role") not in valid_roles:
            return (
                flask.jsonify(
                    {
                        "error": f"Invalid role: {msg.get('role')}. Must be one of: {valid_roles}"
                    }
                ),
                400,
            )
        if "content" not in msg:
            return flask.jsonify(
                {"error": "Message missing required 'content' field"}
            ), 400
        if "timestamp" in msg:
            try:
                ts: datetime = isoparse(msg["timestamp"])
            except (ValueError, OverflowError, TypeError):
                return flask.jsonify(
                    {"error": f"Invalid timestamp format: {msg['timestamp']}"}
                ), 400
        else:
            ts = datetime.now(tz=timezone.utc)
        validated_msgs.append((cast(_RoleType, msg["role"]), msg["content"], ts))

    # Create the log directory atomically to avoid TOCTOU race
    try:
        logdir.mkdir(parents=True)
    except FileExistsError:
        return (
            flask.jsonify({"error": f"Conversation already exists: {conversation_id}"}),
            409,
        )

    # Load or create the chat config, overriding values from request config if provided
    config_dict = req_json.get("config", {})
    config_dict["_logdir"] = logdir  # Pass logdir for "@log" workspace resolution
    request_config = ChatConfig.from_dict(config_dict)
    chat_config = ChatConfig.load_or_create(logdir, request_config)
    prompt = req_json.get("prompt", "full")

    msgs = get_prompt(
        tools=list(get_toolchain(chat_config.tools, strict=False)),
        interactive=chat_config.interactive,
        tool_format=chat_config.tool_format or "markdown",
        model=chat_config.model,
        prompt=prompt,
        workspace=chat_config.workspace,
        agent_path=chat_config.agent,
    )

    # Inject output-format capability hint for webui mode
    # gptme-webui renders ```html code blocks in a sandboxed iframe with
    # Code/Preview tabs — inform the model so it can use HTML when it provides
    # a richer experience for the reader.
    # Set GPTME_SERVE_HTML_HINT=false to disable for non-webui API clients.
    if os.environ.get("GPTME_SERVE_HTML_HINT", "true").lower() != "false":
        msgs.append(
            Message(
                "system",
                "Output format: you are in a web interface. "
                "For interactive content (dashboards, tables, diagrams), "
                "use ```html blocks — they render live in a sandboxed preview panel.",
            )
        )

    for role, content, timestamp in validated_msgs:
        msgs.append(Message(role, content, timestamp=timestamp))

    log = LogManager.load(logdir=logdir, initial_msgs=msgs, create=True)
    log.write()

    # Set tool allowlist to available tools if not provided
    if not chat_config.tools:
        chat_config.tools = [t.name for t in get_toolchain(None) if not t.is_mcp]

    if not chat_config.mcp:
        # load from user or project config
        config = Config.from_workspace(chat_config.workspace)
        chat_config.mcp = config.mcp

    # Save the chat config
    chat_config.save()

    # Create a session for this conversation
    session = SessionManager.create_session(conversation_id)

    # Set auto_confirm_count from the already-validated auto_confirm value
    if type(auto_confirm) is bool:
        if auto_confirm:
            session.auto_confirm_count = 999
    elif auto_confirm > 0:
        session.auto_confirm_count = auto_confirm

    return flask.jsonify(
        {"status": "ok", "conversation_id": conversation_id, "session_id": session.id}
    )


@v2_api.route("/api/v2/conversations/<string:conversation_id>", methods=["POST"])
@require_auth
@api_doc(
    summary="Add message to conversation (V2)",
    description="Add a new message to an existing conversation using the V2 API",
    request_body=MessageCreateRequest,
    responses={200: StatusResponse, 400: ErrorResponse, 404: ErrorResponse},
    parameters=[CONVERSATION_ID_PARAM],
    tags=["conversations-v2"],
)
def api_conversation_post(conversation_id: str):
    """Append a message to a conversation."""
    if error := _validate_conversation_id(conversation_id):
        return error

    req_json = flask.request.json
    if not req_json:
        return flask.jsonify({"error": "No JSON data provided"}), 400

    if "role" not in req_json or "content" not in req_json:
        return flask.jsonify({"error": "Missing required fields (role, content)"}), 400

    # Validate field types
    if not isinstance(req_json["content"], str):
        return flask.jsonify({"error": "content must be a string"}), 400

    # Validate role against allowed values
    valid_roles = ("system", "user", "assistant")
    if req_json["role"] not in valid_roles:
        return (
            flask.jsonify(
                {
                    "error": f"Invalid role: {req_json['role']}. Must be one of: {valid_roles}"
                }
            ),
            400,
        )

    branch = req_json.get("branch", "main")
    if error := _validate_branch(branch):
        return error
    tool_allowlist = req_json.get("tools", None)

    init_tools(tool_allowlist)

    try:
        log = LogManager.load(conversation_id, branch=branch)
    except FileNotFoundError:
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )

    # Validate and convert file paths from JSON strings to Path objects
    files_raw = req_json.get("files", [])
    if not isinstance(files_raw, list) or not all(
        isinstance(f, str) for f in files_raw
    ):
        return flask.jsonify({"error": "files must be a list of strings"}), 400
    file_paths = [Path(f) for f in files_raw]
    msg = Message(
        req_json["role"],
        req_json["content"],
        files=file_paths,  # type: ignore[arg-type]  # list[Path] is valid for list[FilePath]
    )

    # Check if the message is a slash command (e.g. /help, /model, /tools)
    if msg.role == "user" and is_message_command(msg.content):
        # Block commands that are unsafe in server context (would crash or block server)
        parts = msg.content.lstrip("/").split()
        cmd_name = parts[0] if parts else ""
        server_blocked_commands = {"exit", "restart"}
        if cmd_name in server_blocked_commands:
            return flask.jsonify(
                {"error": f"Command /{cmd_name} is not available in server mode"}
            ), 400

        # Append command message first (handle_cmd may undo it via auto_undo)
        log.append(msg)
        SessionManager.add_event(
            conversation_id,
            {
                "type": "message_added",
                "message": msg2dict(msg, log.workspace, log.logdir),
            },
        )

        # Execute the command and collect response messages
        responses: list[Message] = []
        try:
            for resp in handle_cmd(msg.content, log):
                log.append(resp)
                responses.append(resp)
                SessionManager.add_event(
                    conversation_id,
                    {
                        "type": "message_added",
                        "message": msg2dict(resp, log.workspace, log.logdir),
                    },
                )
        except Exception as e:
            logger.exception("Error executing command: %s", msg.content)
            error_msg = Message("system", f"Command error: {e}")
            log.append(error_msg)
            responses.append(error_msg)
            SessionManager.add_event(
                conversation_id,
                {
                    "type": "message_added",
                    "message": msg2dict(error_msg, log.workspace, log.logdir),
                },
            )

        return flask.jsonify(
            {"status": "ok", "command": True, "responses": len(responses)}
        )

    log.append(msg)

    # Notify all sessions that a new message was added
    SessionManager.add_event(
        conversation_id,
        {
            "type": "message_added",
            "message": msg2dict(msg, log.workspace, log.logdir),
        },
    )

    return flask.jsonify({"status": "ok"})


@v2_api.route(
    "/api/v2/conversations/<string:conversation_id>/messages/<int:index>",
    methods=["PATCH"],
)
@require_auth
@api_doc(
    summary="Edit message in conversation (V2)",
    description="Edit a user message in a conversation. With ?truncate=1, removes all messages after the edited one.",
    responses={
        200: ConversationResponse,
        400: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
    },
    parameters=[
        CONVERSATION_ID_PARAM,
        {
            "name": "index",
            "in": "path",
            "required": True,
            "schema": {"type": "integer"},
            "description": "Message index",
        },
        {
            "name": "truncate",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "enum": ["0", "1"]},
            "description": "If '1', remove all messages after the edited one",
        },
    ],
    tags=["conversations-v2"],
)
def api_conversation_edit_message(conversation_id: str, index: int):
    """Edit a message in a conversation.

    Only user messages can be edited. Creates a backup branch before editing.
    With ?truncate=1, removes all messages after the edited one.
    """
    # Validate conversation_id to prevent path traversal
    if error := _validate_conversation_id(conversation_id):
        return error

    req_json = request.get_json(silent=True)
    if req_json is None:
        req_json = {}
    elif not isinstance(req_json, dict):
        return flask.jsonify({"error": "JSON body must be an object"}), 400

    content = req_json.get("content")
    files = req_json.get("files")  # Optional list of file paths
    truncate = request.args.get("truncate") == "1"

    if content is not None and not isinstance(content, str):
        return flask.jsonify({"error": "content must be a string"}), 400
    if files is not None and (
        not isinstance(files, list) or not all(isinstance(f, str) for f in files)
    ):
        return flask.jsonify({"error": "files must be a list of strings"}), 400

    # Content or files required for edits, but optional for pure truncation
    has_changes = (content and content.strip()) or files is not None
    if not truncate and not has_changes:
        return flask.jsonify({"error": "content or files is required"}), 400

    # Check if generation is in progress
    sessions = SessionManager.get_sessions_for_conversation(conversation_id)
    for sess in sessions:
        if sess.generating:
            return (
                flask.jsonify({"error": "Cannot edit while generation is in progress"}),
                409,
            )

    try:
        manager = LogManager.load(conversation_id, lock=False)
    except FileNotFoundError:
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )

    msgs = list(manager.log.messages)
    if index < 0 or index >= len(msgs):
        return (
            flask.jsonify(
                {"error": f"Message index {index} out of range (0-{len(msgs) - 1})"}
            ),
            404,
        )

    # Content/file changes are only allowed on user messages.
    # Truncation (without content change) is allowed on any message.
    content_changed = content is not None and content != msgs[index].content
    files_changed = files is not None and [str(f) for f in msgs[index].files] != files
    if (content_changed or files_changed) and msgs[index].role != "user":
        return flask.jsonify({"error": "Can only edit user messages"}), 400

    if content_changed or files_changed:
        replacements: dict = {}
        if content_changed:
            replacements["content"] = content
        if files_changed and files is not None:
            replacements["files"] = [parse_file_reference(f) for f in files]
        edited_msg = replace(msgs[index], **replacements)
        new_msgs = msgs[:index] + [edited_msg] + ([] if truncate else msgs[index + 1 :])
    elif truncate:
        new_msgs = msgs[: index + 1]
    else:
        return flask.jsonify({"error": "No changes requested"}), 400

    manager.edit(Log(new_msgs))

    # Build response with updated conversation
    log_dict = manager.to_dict(branches=True)

    # Emit SSE event
    SessionManager.add_event(
        conversation_id,
        {
            "type": "conversation_edited",
            "index": index,
            "truncated": truncate,
            "log": log_dict.get("log", []),
            "branches": log_dict.get("branches", {}),
        },
    )

    return flask.jsonify(log_dict)


@v2_api.route(
    "/api/v2/conversations/<string:conversation_id>/messages/<int:index>",
    methods=["DELETE"],
)
@require_auth
@api_doc(
    summary="Delete message from conversation (V2)",
    description="Delete a message from a conversation. Creates a backup branch before deleting.",
    responses={
        200: ConversationResponse,
        400: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
    },
    parameters=[
        CONVERSATION_ID_PARAM,
        {
            "name": "index",
            "in": "path",
            "required": True,
            "schema": {"type": "integer"},
            "description": "Message index",
        },
    ],
    tags=["conversations-v2"],
)
def api_conversation_delete_message(conversation_id: str, index: int):
    """Delete a message from a conversation.

    Removes the message at the given index and keeps all other messages intact.
    Creates a backup branch before deleting.
    """
    # Validate conversation_id to prevent path traversal
    if error := _validate_conversation_id(conversation_id):
        return error

    # Check if generation is in progress
    sessions = SessionManager.get_sessions_for_conversation(conversation_id)
    for sess in sessions:
        if sess.generating:
            return (
                flask.jsonify(
                    {"error": "Cannot delete while generation is in progress"}
                ),
                409,
            )

    try:
        manager = LogManager.load(conversation_id, lock=False)
    except FileNotFoundError:
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )

    msgs = list(manager.log.messages)
    if index < 0 or index >= len(msgs):
        return (
            flask.jsonify(
                {"error": f"Message index {index} out of range (0-{len(msgs) - 1})"}
            ),
            404,
        )

    # Cannot delete system messages
    if msgs[index].role == "system":
        return flask.jsonify({"error": "Cannot delete system messages"}), 400

    new_msgs = msgs[:index] + msgs[index + 1 :]

    # Validate role sequence: consecutive same-role messages break LLM APIs
    non_system = [m for m in new_msgs if m.role != "system"]
    for i in range(1, len(non_system)):
        if non_system[i].role == non_system[i - 1].role:
            return (
                flask.jsonify(
                    {
                        "error": f"Deleting this message would create consecutive {non_system[i].role!r} messages, which is not supported by LLM APIs"
                    }
                ),
                400,
            )

    manager.edit(Log(new_msgs))

    # Build response with updated conversation
    log_dict = manager.to_dict(branches=True)

    # Emit SSE event (reuse conversation_edited — client handles log replacement)
    SessionManager.add_event(
        conversation_id,
        {
            "type": "conversation_edited",
            "index": index,
            "truncated": False,
            "log": log_dict.get("log", []),
            "branches": log_dict.get("branches", {}),
        },
    )

    return flask.jsonify(log_dict)


@v2_api.route("/api/v2/conversations/<string:conversation_id>", methods=["DELETE"])
@require_auth
@api_doc(
    summary="Delete conversation (V2)",
    description="Delete a conversation and all its data using the V2 API",
    responses={
        200: StatusResponse,
        400: ErrorResponse,
        404: ErrorResponse,
        500: ErrorResponse,
    },
    parameters=[
        {
            "name": "conversation_id",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
            "description": "Conversation ID",
        }
    ],
    tags=["conversations-v2"],
)
def api_conversation_delete(conversation_id: str):
    """Delete a conversation."""

    # Validate conversation_id to prevent path traversal
    if error := _validate_conversation_id(conversation_id):
        return error

    logdir = get_logs_dir() / conversation_id
    # Defense-in-depth: verify path is within logs directory
    # (cannot use assert as it can be disabled with python -O)
    if logdir.parent != get_logs_dir():
        logger.warning(f"Path traversal attempt blocked: {conversation_id}")
        return flask.jsonify({"error": "Invalid conversation_id"}), 400
    if not logdir.exists():
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )

    try:
        shutil.rmtree(logdir)
    except OSError as e:
        logger.error(f"Error deleting conversation {conversation_id}: {e}")
        return flask.jsonify({"error": f"Could not delete conversation: {e}"}), 500

    SessionManager.remove_all_sessions_for_conversation(conversation_id)

    return flask.jsonify({"status": "ok"})


@v2_api.route("/api/v2/models")
@require_auth
@api_doc_simple(
    responses={200: StatusResponse, 500: ErrorResponse},
    tags=["models"],
)
def api_models():
    """Get available models.

    Returns available models based on current configuration.
    If proxy is configured, only returns proxy-supported models.
    """

    config = Config()

    # Check if proxy is configured
    proxy_url = config.get_env("LLM_PROXY_URL")
    proxy_key = config.get_env("LLM_PROXY_API_KEY")
    is_proxy = bool(proxy_url and proxy_key)

    # Get default model
    default_model = get_default_model()

    # Get available models
    models_data: list[dict] = []

    # If proxy is configured (like gptme.ai), show supported providers
    # The proxy now supports both OpenAI and Anthropic models
    providers_to_check: list[Provider]
    if is_proxy:
        providers_to_check = cast(list[Provider], ["openai", "anthropic", "openrouter"])
    else:
        providers_to_check = cast(list[Provider], PROVIDERS)
    for provider in providers_to_check:
        provider_models = _get_models_for_provider(provider, dynamic_fetch=True)
        models = _apply_model_filters(provider_models, include_deprecated=False)
        models_data.extend(
            {
                "id": model.full,
                "provider": model.provider,
                "model": model.model,
                "context": model.context,
                "max_output": model.max_output,
                "supports_streaming": model.supports_streaming,
                "supports_vision": model.supports_vision,
                "supports_reasoning": model.supports_reasoning,
                "price_input": model.price_input,
                "price_output": model.price_output,
                "deprecated": model.deprecated,
            }
            for model in models
        )

    # Build recommended models list from core definitions
    recommended: list[str] = []
    for provider in providers_to_check:
        try:
            rec = get_recommended_model(provider)
            full_id = f"{provider}/{rec}"
            if any(m["id"] == full_id for m in models_data):
                recommended.append(full_id)
        except ValueError:
            pass

    return flask.jsonify(
        {
            "models": models_data,
            "default": default_model.full if default_model else None,
            "recommended": recommended,
        }
    )


@v2_api.route("/api/v2/commands")
@require_auth
@api_doc_simple(
    responses={200: StatusResponse},
    tags=["commands"],
)
def api_commands():
    """Get available slash commands.

    Returns the list of registered commands that can be used via /command syntax.
    """
    from ..commands import get_user_commands

    return flask.jsonify({"commands": get_user_commands()})


@v2_api.route("/api/v2/conversations/<string:conversation_id>/config", methods=["GET"])
@require_auth
def api_conversation_config(conversation_id: str):
    """Get the chat config for a conversation."""
    # Validate conversation_id to prevent path traversal
    if error := _validate_conversation_id(conversation_id):
        return error

    logdir = get_logs_dir() / conversation_id
    chat_config_path = logdir / "config.toml"
    if chat_config_path.exists():
        chat_config = ChatConfig.from_logdir(logdir)
        return flask.jsonify(chat_config.to_dict())
    return (
        flask.jsonify({"error": f"Chat config not found: {conversation_id}"}),
        404,
    )


@v2_api.route(
    "/api/v2/conversations/<string:conversation_id>/config", methods=["PATCH"]
)
@require_auth
def api_conversation_config_patch(conversation_id: str):
    """Update the chat config for a conversation."""
    # Validate conversation_id to prevent path traversal
    if error := _validate_conversation_id(conversation_id):
        return error

    req_json = request.get_json(silent=True)
    if req_json is None:
        return flask.jsonify({"error": "No JSON data provided"}), 400
    if not isinstance(req_json, dict):
        return flask.jsonify({"error": "JSON body must be an object"}), 400

    logdir = get_logs_dir() / conversation_id

    # Guard: check conversation exists before any side-effecting operations.
    # ChatConfig.save() creates the logdir on disk and set_config/init_tools mutate
    # process-wide state, so the 404 check must come first.
    if not logdir.exists():
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )

    # Load conversation log BEFORE any side effects (config write, global state).
    # A directory can exist without a valid log file (partial deletion, corruption),
    # so we must verify the log is loadable before committing changes.
    try:
        manager = LogManager.load(conversation_id, lock=False)
    except FileNotFoundError:
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )

    # Reject config changes while generation is in progress — config PATCH rewrites
    # system messages and mutates global state, which races with streaming.
    sessions = SessionManager.get_sessions_for_conversation(conversation_id)
    for sess in sessions:
        if sess.generating:
            return (
                flask.jsonify(
                    {"error": "Cannot update config while generation is in progress"}
                ),
                409,
            )

    # Create and set config
    req_json["_logdir"] = logdir  # Pass logdir for "@log" workspace resolution
    request_config = ChatConfig.from_dict(req_json)
    chat_config = ChatConfig.load_or_create(logdir, request_config).save()
    config = Config.from_workspace(workspace=chat_config.workspace)
    config.chat = chat_config
    set_config(config)

    # Initialize tools in this thread
    init_tools(chat_config.tools)

    tools = get_tools()

    if len(manager.log.messages) >= 1 and manager.log.messages[0].role == "system":
        # Remove leading system messages and replace with new ones
        # Use immutable Log interface instead of mutating the frozen dataclass's list
        first_non_system = 0
        for m in manager.log.messages:
            if m.role != "system":
                break
            first_non_system += 1
        remaining_msgs = list(manager.log.messages[first_non_system:])

        new_system_msgs = list(
            get_prompt(
                tools=tools,
                tool_format=chat_config.tool_format or "markdown",
                interactive=chat_config.interactive,
                model=chat_config.model,
                workspace=chat_config.workspace,
                agent_path=chat_config.agent,
            )
        )
        manager.log = Log(new_system_msgs + remaining_msgs)
    manager.write()

    return flask.jsonify(
        {
            "status": "ok",
            "message": "Chat config updated",
            "config": chat_config.to_dict(),
            "tools": [t.name for t in tools],
        }
    )


@v2_api.route(
    "/api/v2/conversations/<string:conversation_id>/agent/avatar", methods=["GET"]
)
@require_auth
@api_doc(
    summary="Get agent avatar",
    description="Serve the agent's avatar image for a conversation",
    responses={200: None, 404: ErrorResponse},
    parameters=[
        {
            "name": "conversation_id",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
            "description": "Conversation ID",
        }
    ],
    tags=["conversations-v2"],
)
def api_conversation_agent_avatar(conversation_id: str):
    """Serve the agent's avatar image."""
    # Validate conversation_id to prevent path traversal
    if error := _validate_conversation_id(conversation_id):
        return error

    logdir = get_logs_dir() / conversation_id
    if not logdir.exists():
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )

    try:
        LogManager.load(logdir, lock=False)
    except FileNotFoundError:
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )

    chat_config = ChatConfig.load_or_create(logdir, ChatConfig())

    agent_config = chat_config.agent_config
    if not agent_config or not agent_config.avatar:
        return flask.jsonify({"error": "No avatar configured"}), 404

    avatar_path = agent_config.avatar

    # If it's a URL, redirect to it
    if avatar_path.startswith(("http://", "https://")):
        return flask.redirect(avatar_path)

    # Otherwise, serve the file from agent workspace
    if not chat_config.agent:
        return flask.jsonify({"error": "No agent path configured"}), 404

    full_path = chat_config.agent / avatar_path

    # Validate the path is within agent workspace (security) - do this BEFORE existence check
    try:
        full_path.resolve().relative_to(chat_config.agent.resolve())
    except ValueError:
        return flask.jsonify({"error": "Invalid avatar path"}), 400

    if full_path.suffix.lower() not in _ALLOWED_AVATAR_EXTS:
        return flask.jsonify({"error": "Avatar must be an image file"}), 400

    if not full_path.exists():
        return flask.jsonify({"error": "Avatar file not found"}), 404

    if not _is_valid_image_content(full_path):
        return flask.jsonify({"error": "Avatar must be a valid image file"}), 400

    return flask.send_file(full_path)


def _serve_agent_avatar(agent_path_str: str):
    """Shared helper to serve an agent's avatar by its workspace path."""
    from pathlib import Path

    agent_path = Path(agent_path_str)
    project_config = get_project_config(agent_path, quiet=True)
    if not project_config or not project_config.agent:
        return flask.jsonify({"error": "Agent not found"}), 404

    avatar = project_config.agent.avatar
    if not avatar:
        return flask.jsonify({"error": "No avatar configured"}), 404

    # If it's a URL, redirect to it
    if avatar.startswith(("http://", "https://")):
        return flask.redirect(avatar)

    # Otherwise, serve the file from agent workspace
    full_path = agent_path / avatar
    try:
        full_path.resolve().relative_to(agent_path.resolve())
    except ValueError:
        return flask.jsonify({"error": "Invalid avatar path"}), 400

    if full_path.suffix.lower() not in _ALLOWED_AVATAR_EXTS:
        return flask.jsonify({"error": "Avatar must be an image file"}), 400

    if not full_path.exists():
        return flask.jsonify({"error": "Avatar file not found"}), 404

    if not _is_valid_image_content(full_path):
        return flask.jsonify({"error": "Avatar must be a valid image file"}), 400

    return flask.send_file(full_path)


@v2_api.route("/api/v2/agents", methods=["GET"])
@require_auth
@api_doc(
    summary="List agents",
    description="List agents discovered from conversation history",
    responses={200: None},
    tags=["agents"],
)
def api_agents():
    """List agents extracted from conversations."""
    agent_map: dict[str, dict] = {}
    for conv in get_user_conversations():
        if not conv.agent_path:
            continue
        path = conv.agent_path
        if path not in agent_map:
            agent_map[path] = {
                "name": conv.agent_name or path.split("/")[-1],
                "path": path,
                "has_avatar": conv.agent_avatar is not None,
                "urls": conv.agent_urls,
                "conversation_count": 0,
                "last_used": conv.modified,
            }
        entry = agent_map[path]
        entry["conversation_count"] += 1
        if conv.modified > entry["last_used"]:
            entry["last_used"] = conv.modified
            if conv.agent_urls:
                entry["urls"] = conv.agent_urls
            if conv.agent_avatar is not None:
                entry["has_avatar"] = True
    return flask.jsonify(list(agent_map.values()))


@v2_api.route("/api/v2/agents/avatar", methods=["GET"])
@require_auth
@api_doc(
    summary="Get agent avatar by path",
    description="Serve an agent's avatar image by its workspace path",
    responses={200: None, 404: ErrorResponse},
    parameters=[
        {
            "name": "path",
            "in": "query",
            "required": True,
            "schema": {"type": "string"},
            "description": "Agent workspace path",
        }
    ],
    tags=["agents"],
)
def api_agent_avatar():
    """Serve an agent's avatar by workspace path."""
    agent_path = request.args.get("path")
    if not agent_path:
        return flask.jsonify({"error": "path parameter required"}), 400
    return _serve_agent_avatar(agent_path)


@v2_api.route("/api/v2/user", methods=["GET"])
@require_auth
@api_doc(
    summary="Get user identity",
    description="Get user identity info (name, avatar) from global config",
    responses={200: None},
    tags=["user"],
)
def api_user():
    """Get user identity info."""
    user_config = load_user_config()
    return flask.jsonify(
        {
            "name": user_config.user.name,
            "avatar": user_config.user.avatar,
        }
    )


@v2_api.route("/api/v2/user/api-key", methods=["POST"])
@require_auth
@api_doc(
    summary="Save provider API key",
    description=(
        "Persist a provider API key into the user's global gptme config. "
        "Intended for first-run onboarding flows; callers should restart the "
        "server after a successful write if they need the running process to "
        "pick the key up immediately."
    ),
    request_body=UserApiKeySaveRequest,
    responses={200: UserApiKeySaveResponse, 400: ErrorResponse},
    tags=["user"],
)
def api_user_api_key():
    """Persist a provider API key into user config."""
    req_json = flask.request.json
    if not req_json:
        return flask.jsonify({"error": "No JSON data provided"}), 400

    provider = req_json.get("provider")
    api_key = req_json.get("api_key")
    model = req_json.get("model")
    if not isinstance(provider, str):
        return flask.jsonify({"error": "provider must be a string"}), 400
    if not isinstance(api_key, str):
        return flask.jsonify({"error": "api_key must be a string"}), 400
    if model is not None and not isinstance(model, str):
        return flask.jsonify({"error": "model must be a string"}), 400
    if provider not in PROVIDER_API_KEYS:
        return flask.jsonify({"error": f"Unknown provider: {provider}"}), 400

    try:
        trimmed_api_key = _validate_api_key_input(api_key)
        trimmed_model = (
            _validate_model_input(model, expected_provider=provider)
            if model is not None
            else None
        )
    except ValueError as exc:
        return flask.jsonify({"error": str(exc)}), 400

    env_var = PROVIDER_API_KEYS[provider]
    set_config_value(f"env.{env_var}", trimmed_api_key, reload=False)
    if trimmed_model is not None:
        set_config_value("env.MODEL", trimmed_model, reload=False)

    # Apply the new key immediately so the running server picks it up without restart.
    # os.environ takes priority over the config file in Config.get_env(), so the next
    # LLM call will use the new key.
    os.environ[env_var] = trimmed_api_key
    if trimmed_model is not None:
        os.environ["MODEL"] = trimmed_model

    logger.info(
        "Saved %s to user config via /api/v2/user/api-key (applied live)", env_var
    )
    return flask.jsonify(
        {
            "status": "ok",
            "provider": provider,
            "env_var": env_var,
            "restart_required": False,
        }
    )


@v2_api.route("/api/v2/user/default-model", methods=["POST"])
@require_auth
@api_doc(
    summary="Save default model",
    description=(
        "Persist the default model into the user's global gptme config and apply "
        "it to the running server when possible."
    ),
    request_body=UserDefaultModelSaveRequest,
    responses={200: UserDefaultModelSaveResponse, 400: ErrorResponse},
    tags=["user"],
)
def api_user_default_model():
    """Persist the default model into user config."""
    req_json = flask.request.json
    if not req_json:
        return flask.jsonify({"error": "No JSON data provided"}), 400

    model = req_json.get("model")
    if not isinstance(model, str):
        return flask.jsonify({"error": "model must be a string"}), 400

    try:
        trimmed_model = _validate_model_input(model)
    except ValueError as exc:
        return flask.jsonify({"error": str(exc)}), 400

    restart_required = _persist_default_model(trimmed_model)
    logger.info("Saved MODEL=%s via /api/v2/user/default-model", trimmed_model)
    return flask.jsonify(
        {
            "status": "ok",
            "model": trimmed_model,
            "restart_required": restart_required,
        }
    )


@v2_api.route("/api/v2/user/avatar", methods=["GET"])
@require_auth
@api_doc(
    summary="Get user avatar",
    description="Serve the user's avatar image from global config",
    responses={200: None, 404: ErrorResponse},
    tags=["user"],
)
def api_user_avatar():
    """Serve the user's avatar image."""
    user_config = load_user_config()
    avatar_path = user_config.user.avatar
    if not avatar_path:
        return flask.jsonify({"error": "No avatar configured"}), 404

    # If it's a URL, redirect to it
    if avatar_path.startswith(("http://", "https://")):
        return flask.redirect(avatar_path)

    # Resolve the path (supports ~ expansion)
    from pathlib import Path

    full_path = Path(avatar_path).expanduser().resolve()

    # Security: validate path points to a raster image file to prevent serving
    # sensitive files (e.g. ~/.ssh/id_rsa) via a malicious config value.
    # SVG is excluded: it can embed <script> tags and execute JS in the
    # server's origin when navigated to directly (XSS via crafted SVG).
    if full_path.suffix.lower() not in _ALLOWED_AVATAR_EXTS:
        return flask.jsonify({"error": "Avatar must be an image file"}), 400

    if not full_path.exists():
        return flask.jsonify({"error": "Avatar file not found"}), 404

    if not _is_valid_image_content(full_path):
        return flask.jsonify({"error": "Avatar must be a valid image file"}), 400

    return flask.send_file(full_path)


@v2_api.route("/api/v2/user/settings", methods=["GET"])
@require_auth
@api_doc(
    summary="Get current user settings",
    description=(
        "Return a read-only snapshot of the current user settings: which providers "
        "have API keys or OAuth tokens configured, and the active default model. "
        "Useful for settings UI and onboarding flows that need to reflect server-side "
        "state without keeping a local copy."
    ),
    responses={200: UserSettingsResponse},
    tags=["user"],
)
def api_user_settings():
    """Return the current user settings state."""
    available = list_available_providers()
    providers = [str(provider) for provider, _ in available]
    provider_sources = {
        str(provider): {
            "auth_source": auth_source,
            "effective_source": (
                "oauth"
                if auth_source == "oauth"
                else get_user_config_env_source(auth_source)
            ),
        }
        for provider, auth_source in available
    }
    default_model = get_default_model()
    return flask.jsonify(
        {
            "providers_configured": providers,
            "provider_sources": provider_sources,
            "default_model": default_model.full if default_model else None,
            "default_model_source": get_user_config_env_source("MODEL"),
            "config_files": get_user_config_runtime_info(),
        }
    )
