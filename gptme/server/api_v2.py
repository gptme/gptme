"""
V2 API for gptme server with improved control flow and conversation management.

This module contains the main conversation CRUD endpoints for the V2 API.
Session management, tool execution, and agent creation are handled by separate modules.
"""

import logging
import shutil
from datetime import datetime
from itertools import islice
from typing import cast

import flask
from dateutil.parser import isoparse
from flask import request

from gptme.config import ChatConfig, Config, load_user_config, set_config
from gptme.llm.models import (
    PROVIDERS,
    Provider,
    _apply_model_filters,
    _get_models_for_provider,
    get_default_model,
)
from gptme.prompts import get_prompt

from ..commands import handle_cmd
from ..dirs import get_logs_dir
from ..logmanager import Log, LogManager, get_user_conversations
from ..message import Message
from ..tools import get_toolchain, get_tools, init_tools
from ..util.content import is_message_command


def _validate_conversation_id(
    conversation_id: str,
) -> tuple[flask.Response, int] | None:
    """Validate conversation_id to prevent path traversal attacks.

    Returns None if valid, or (error_response, status_code) if invalid.
    """
    if "/" in conversation_id or ".." in conversation_id or "\\" in conversation_id:
        return flask.jsonify({"error": "Invalid conversation_id"}), 400
    return None


from .api import _abs_to_rel_workspace
from .api_v2_agents import agents_api
from .api_v2_common import msg2dict
from .api_v2_sessions import SessionManager, sessions_api
from .auth import require_auth
from .openapi_docs import (
    CONVERSATION_ID_PARAM,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationResponse,
    ErrorResponse,
    MessageCreateRequest,
    SessionResponse,
    StatusResponse,
    api_doc,
    api_doc_simple,
)

logger = logging.getLogger(__name__)

v2_api = flask.Blueprint("v2_api", __name__)

# Register sub-blueprints
v2_api.register_blueprint(sessions_api)
v2_api.register_blueprint(agents_api)


@v2_api.route("/api/v2")
@api_doc_simple()
def api_root():
    """V2 API root.

    Get information about the v2 API, including available endpoints and capabilities.
    """
    return flask.jsonify(
        {
            "message": "gptme v2 API",
            "documentation": "https://gptme.org/docs/server.html",
        }
    )


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
        }
    ],
)
def api_conversations():
    """List conversations (V2).

    Get a list of user conversations with metadata using the V2 API.
    """
    limit = int(request.args.get("limit", 100))
    conversations = list(islice(get_user_conversations(), limit))
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

    # Create and set config
    logdir = get_logs_dir() / conversation_id
    chat_config = ChatConfig.load_or_create(logdir, ChatConfig()).save()

    manager = LogManager.load(conversation_id, lock=False)
    log_dict = manager.to_dict(branches=True)

    # make all paths absolute or relative to workspace (no "../")
    for msg in log_dict["log"]:
        if files := msg.get("files"):
            msg["files"] = [
                _abs_to_rel_workspace(f, chat_config.workspace) for f in files
            ]

    # Include agent info if available
    agent_config = chat_config.agent_config
    if agent_config:
        log_dict["agent"] = {
            "name": agent_config.name,
            "avatar": agent_config.avatar,
        }
        if chat_config.agent:
            log_dict["agent"]["path"] = str(chat_config.agent)

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
    if logdir.exists():
        return (
            flask.jsonify({"error": f"Conversation already exists: {conversation_id}"}),
            409,
        )

    req_json = flask.request.json or {}

    # Create the log directory
    logdir.mkdir(parents=True)

    # Load or create the chat config, overriding values from request config if provided
    config_dict = req_json.get("config", {})
    config_dict["_logdir"] = logdir  # Pass logdir for "@log" workspace resolution
    request_config = ChatConfig.from_dict(config_dict)
    chat_config = ChatConfig.load_or_create(logdir, request_config)
    prompt = req_json.get("prompt", "full")

    msgs = get_prompt(
        tools=list(get_toolchain(chat_config.tools)),
        interactive=chat_config.interactive,
        tool_format=chat_config.tool_format or "markdown",
        model=chat_config.model,
        prompt=prompt,
        workspace=chat_config.workspace,
        agent_path=chat_config.agent,
    )

    valid_roles = ("system", "user", "assistant")
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
        timestamp: datetime = (
            isoparse(msg["timestamp"]) if "timestamp" in msg else datetime.now()
        )
        msgs.append(Message(msg["role"], msg["content"], timestamp=timestamp))

    logdir.mkdir(parents=True, exist_ok=True)
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

    # Check for auto_confirm parameter and set auto_confirm_count
    if req_json and req_json.get("auto_confirm"):
        session.auto_confirm_count = 999  # High number to essentially make it unlimited

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
    req_json = flask.request.json
    if not req_json:
        return flask.jsonify({"error": "No JSON data provided"}), 400

    if "role" not in req_json or "content" not in req_json:
        return flask.jsonify({"error": "Missing required fields (role, content)"}), 400

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
    tool_allowlist = req_json.get("tools", None)

    init_tools(tool_allowlist)

    try:
        log = LogManager.load(conversation_id, branch=branch)
    except FileNotFoundError:
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )

    msg = Message(
        req_json["role"], req_json["content"], files=req_json.get("files", [])
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
            {"type": "message_added", "message": msg2dict(msg, log.workspace)},
        )

        # Execute the command and collect response messages
        responses: list[Message] = []
        try:
            for resp in handle_cmd(msg.content, log):
                log.append(resp)
                responses.append(resp)
                SessionManager.add_event(
                    conversation_id,
                    {"type": "message_added", "message": msg2dict(resp, log.workspace)},
                )
        except (Exception, SystemExit) as e:
            logger.exception("Error executing command: %s", msg.content)
            error_msg = Message("system", f"Command error: {e}")
            log.append(error_msg)
            responses.append(error_msg)
            SessionManager.add_event(
                conversation_id,
                {
                    "type": "message_added",
                    "message": msg2dict(error_msg, log.workspace),
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
            "message": msg2dict(msg, log.workspace),
        },
    )

    return flask.jsonify({"status": "ok"})


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

    return flask.jsonify(
        {
            "models": models_data,
            "default": default_model.full if default_model else None,
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

    req_json = flask.request.json
    if not req_json:
        return flask.jsonify({"error": "No JSON data provided"}), 400

    logdir = get_logs_dir() / conversation_id

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

    # Update system prompt with new tools
    manager = LogManager.load(conversation_id, lock=False)
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

    if not full_path.exists():
        return flask.jsonify({"error": "Avatar file not found"}), 404

    return flask.send_file(full_path)


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

    if not full_path.exists():
        return flask.jsonify({"error": "Avatar file not found"}), 404

    return flask.send_file(full_path)
