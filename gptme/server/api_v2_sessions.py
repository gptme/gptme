"""V2 API session route handlers.

Flask Blueprint with endpoints for real-time conversation interaction:
event streaming, step execution, tool confirmation, elicitation, and interrupt.

Data models live in session_models.py; execution logic in session_step.py.
"""

import dataclasses
import logging
import time
import uuid
from collections.abc import Generator
from datetime import datetime, timezone

import flask
from flask import request

from ..config import ChatConfig, Config
from ..dirs import get_logs_dir
from ..llm.models import get_default_model
from ..logmanager import LogManager
from ..message import Message
from .api_v2_common import _validate_branch, _validate_conversation_id
from .auth import require_auth
from .constants import DEFAULT_FALLBACK_MODEL
from .openapi_docs import (
    CONVERSATION_ID_PARAM,
    ElicitRespondRequest,
    ErrorResponse,
    InterruptRequest,
    StatusResponse,
    StepRequest,
    ToolConfirmRequest,
    api_doc,
)

# Re-export public symbols so existing imports (e.g. ``from .api_v2_sessions
# import SessionManager``) continue to work without changes.
from .session_models import (
    ConversationSession,
    SessionManager,
    ToolExecution,
    ToolStatus,
)
from .session_step import (  # noqa: F401
    _append_and_notify,
    _get_use_acp_default,
    _run_health_check,
    _start_acp_step_thread,
    _start_step_thread,
    close_acp_runtime_bg,
    resolve_hook_confirmation,
    resolve_hook_elicitation,
    start_acp_health_monitor,
    start_tool_execution,
    stop_acp_health_monitor,
)

logger = logging.getLogger(__name__)


def _get_request_json_object() -> dict | tuple[flask.Response, int]:
    """Return request JSON as an object or a 400 error response.

    Session endpoints expect JSON objects. Arrays/strings/numbers would make
    later `.get()` access crash with AttributeError and return 500s.
    """
    req_json = request.get_json(silent=True)
    if req_json is None:
        return {}
    if not isinstance(req_json, dict):
        return flask.jsonify({"error": "JSON body must be an object"}), 400
    return req_json


# Re-export step-level symbols that other modules may import from here.
# This preserves backward compatibility after the split.
__all__ = [
    # Models
    "ToolStatus",
    "ToolExecution",
    "ConversationSession",
    "SessionManager",
    # Step execution
    "start_acp_health_monitor",
    "stop_acp_health_monitor",
    "close_acp_runtime_bg",
    "start_tool_execution",
    # Blueprint
    "sessions_api",
]


# API Endpoints
# ------------

sessions_api = flask.Blueprint("sessions_api", __name__)


@sessions_api.route("/api/v2/conversations/<string:conversation_id>/events")
@require_auth
@api_doc(
    summary="Subscribe to conversation events (V2)",
    description="Subscribe to real-time conversation events via Server-Sent Events stream",
    responses={200: None, 404: ErrorResponse},
    parameters=[
        {
            "name": "conversation_id",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
            "description": "Conversation ID",
        },
        {
            "name": "session_id",
            "in": "query",
            "required": False,
            "schema": {"type": "string"},
            "description": "Session ID (creates new session if not provided)",
        },
    ],
    tags=["sessions"],
)
def api_conversation_events(conversation_id: str):
    """Subscribe to conversation events."""
    if error := _validate_conversation_id(conversation_id):
        return error
    session_id = request.args.get("session_id")
    if not session_id:
        # Create a new session if none provided
        session = SessionManager.create_session(conversation_id)
        session_id = session.id
    else:
        session_obj = SessionManager.get_session(session_id)
        if session_obj is None:
            return flask.jsonify({"error": f"Session not found: {session_id}"}), 404
        session = session_obj

    # Generate event stream
    def generate_events() -> Generator[str, None, None]:
        client_id = str(uuid.uuid4())
        try:
            # Add this client to the session
            session.clients.add(client_id)

            # Send initial connection event with pending tool state
            connected_event = {
                "type": "connected",
                "session_id": session_id,
                "generating": session.generating,
                "last_error": session.last_error,
                "pending_tools": [
                    {
                        "tool_id": tid,
                        "tooluse": {
                            "tool": te.tooluse.tool,
                            "args": te.tooluse.args,
                            "content": te.tooluse.content,
                        },
                        "auto_confirm": te.auto_confirm,
                    }
                    for tid, te in list(session.pending_tools.items())
                    if te.status == ToolStatus.PENDING
                ],
            }
            yield f"data: {flask.json.dumps(connected_event)}\n\n"

            # Send immediate ping to ensure connection is established right away
            yield f"data: {flask.json.dumps({'type': 'ping'})}\n\n"

            # Track position using absolute event indices (offset-aware)
            last_event_index = session.events_count

            while True:
                # Check if there are new events
                if last_event_index < (new_index := session.events_count):
                    # Send any new events
                    for event in session.get_events_since(last_event_index):
                        yield f"data: {flask.json.dumps(event)}\n\n"
                    last_event_index = new_index

                # Wait a bit before checking again
                yield f"data: {flask.json.dumps({'type': 'ping'})}\n\n"

                # Clear before waiting to avoid race: if an event arrives between
                # wait() returning and clear(), the signal would be lost, delaying
                # delivery by up to one full timeout interval.
                session.event_flag.clear()

                # Re-check after clearing: events may have arrived between the
                # check above and the clear(), which would otherwise delay
                # delivery by up to the full wait timeout.
                if last_event_index < session.events_count:
                    continue

                session.event_flag.wait(timeout=15)

        except GeneratorExit:
            # Client disconnected
            if session:
                session.clients.discard(client_id)
                if not session.clients:
                    # If no clients are connected, mark the session for cleanup
                    session.active = False
            raise

    return flask.Response(
        generate_events(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable buffering in nginx
        },
    )


@sessions_api.route(
    "/api/v2/conversations/<string:conversation_id>/step", methods=["POST"]
)
@require_auth
@api_doc(
    summary="Take conversation step (V2)",
    description="Take a step in the conversation - generate a response or continue after tool execution",
    request_body=StepRequest,
    responses={
        200: StatusResponse,
        400: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
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
    tags=["sessions"],
)
def api_conversation_step(conversation_id: str):
    """Take a step in the conversation - generate a response or continue after tool execution."""
    if error := _validate_conversation_id(conversation_id):
        return error
    req_json = _get_request_json_object()
    if not isinstance(req_json, dict):
        return req_json
    session_id = req_json.get("session_id")

    if not session_id:
        return flask.jsonify({"error": "session_id is required"}), 400

    session = SessionManager.get_session(session_id)
    if session is None:
        return flask.jsonify({"error": f"Session not found: {session_id}"}), 404

    logdir = get_logs_dir() / conversation_id
    chat_config = ChatConfig.load_or_create(logdir, ChatConfig())

    stream = req_json.get("stream", chat_config.stream)

    # ACP opt-in: sticky once enabled for a session.
    # Default can be set server-wide via GPTME_USE_ACP_DEFAULT=true env var.
    # Validate type explicitly to avoid truthy string surprises (e.g. "false").
    _acp_default = _get_use_acp_default()
    use_acp = req_json.get("use_acp", _acp_default)
    if not isinstance(use_acp, bool):
        return (
            flask.jsonify(
                {
                    "error": "Invalid 'use_acp' value",
                    "message": "'use_acp' must be a boolean",
                }
            ),
            400,
        )

    if use_acp and not session.use_acp:
        from .acp_session_runtime import AcpSessionRuntime

        session.use_acp = True
        session.acp_runtime = AcpSessionRuntime(workspace=chat_config.workspace)
        # Lazy-start the health monitor on first ACP session — avoids unconditional
        # background thread and global session-eviction side-effects at app startup.
        start_acp_health_monitor()

    # Validate auto_confirm type explicitly (bool OR int).
    # Reject strings/floats/etc. to avoid accidental truthy coercion.
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

    # If auto_confirm set, set auto_confirm_count.
    # Check bool first: bool is a subclass of int in Python, so isinstance(True, int) is True.
    # Use type() to distinguish them correctly.
    if type(auto_confirm) is bool:
        session.auto_confirm_count = 1 if auto_confirm else -1
    else:  # int
        session.auto_confirm_count = auto_confirm

    auto_confirm_enabled = bool(session.auto_confirm_count > 0)

    # Atomically check-and-set generating under a per-session lock.
    # Without the lock, two concurrent requests on a threaded WSGI server can
    # both read False before either writes True (classic TOCTOU).  The lock is
    # held only for the check+set — the expensive model/branch resolution that
    # follows runs outside it.
    with session.step_lock:
        if session.generating:
            return flask.jsonify({"error": "Generation already in progress"}), 409
        # Mark generating early to prevent concurrent /step requests from racing
        # through the setup code below.  _start_step_thread/_start_acp_step_thread
        # also set this, but ~60 lines of model/branch resolution sit between the
        # check above and those calls — enough for a second request to slip through
        # on threaded WSGI servers.
        session.generating = True
        session.generating_since = datetime.now(tz=timezone.utc)

    # Wrap setup in try/finally so any unexpected exception (get_default_model,
    # config I/O, etc.) resets the flag rather than leaving the session
    # permanently stuck in "generating" state.
    _step_dispatched = False
    try:
        # Get the branch and model
        branch = req_json.get("branch", "main")
        if error := _validate_branch(branch):
            return error
        default_model = get_default_model()

        # Get model from request, config, or default (in that order).
        # The frontend only sends model when the user explicitly selected one
        # (hasExplicitModelSelection), so any value here is a genuine choice.
        model = req_json.get("model")
        if model and model != chat_config.model:
            chat_config.model = model
            chat_config.save()
            # Notify frontend so the model badge updates
            SessionManager.add_event(
                conversation_id,
                {
                    "type": "config_changed",
                    "config": chat_config.to_dict(),
                    "changed_fields": ["model"],
                },
            )
        if not model:
            model = chat_config.model
        if not model and default_model:
            model = default_model.full
        if not model:
            # Try to get from environment/config as last resort
            config = Config.from_workspace(workspace=chat_config.workspace)
            model = config.get_env("MODEL")
        if not model and not session.use_acp:
            # In ACP mode the subprocess manages its own model; skip this check
            return flask.jsonify(
                {
                    "error": "No model specified and no default model set",
                    "message": (
                        "Please specify a model in one of the following ways:\n"
                        "1. Include 'model' in the request JSON\n"
                        "2. Set MODEL environment variable when starting the server\n"
                        "3. Use --model flag when starting the server (gptme-server serve --model <model>)\n"
                        "4. Configure model in workspace chat config"
                    ),
                    "example_models": [
                        DEFAULT_FALLBACK_MODEL,
                        "openai/gpt-4",
                        "openai/gpt-4o-mini",
                    ],
                }
            ), 400

        # Snapshot acp_runtime to avoid TOCTOU races: concurrent cleanup threads
        # (e.g. _cleanup_stale_acp_sessions) can set session.acp_runtime = None
        # between the check and the use.
        acp_runtime = session.acp_runtime if session.use_acp else None

        # If ACP mode is active, keep session runtime model aligned with the
        # resolved request/config/default model whenever available.
        if acp_runtime is not None and model:
            acp_runtime.model = model

        # Snapshot absolute event count before starting, so we can detect new events below
        initial_event_count = session.events_count

        # Route through ACP subprocess if the session has opted in
        if acp_runtime is not None:
            _start_acp_step_thread(
                conversation_id=conversation_id,
                session=session,
                workspace=chat_config.workspace,
            )
        else:
            # model should be non-None here: the `if not model and not session.use_acp`
            # check above returns 400 for non-ACP sessions with no model.
            # Use explicit check instead of assert (which python -O disables).
            if model is None:
                return flask.jsonify(
                    {"error": "Model is required for non-ACP sessions"}
                ), 400
            # Start step execution in a background thread
            # Model will be set in the worker thread by step()
            _start_step_thread(
                conversation_id=conversation_id,
                session=session,
                model=model,
                workspace=chat_config.workspace,
                branch=branch,
                auto_confirm=auto_confirm_enabled,
                stream=stream,
            )
        _step_dispatched = True
    finally:
        if not _step_dispatched:
            session.generating = False
            session.generating_since = None

    # Wait briefly for early errors (bad model, auth failure, empty messages, etc.)
    # so we can return them in the HTTP response instead of swallowing silently.
    # We poll the session events for up to 5 seconds, looking for either
    # a "generation_progress" event (success) or an "error" event (failure).
    _STARTUP_TIMEOUT = 5.0
    _POLL_INTERVAL = 0.1
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        # Check new events since we started
        new_events = session.get_events_since(initial_event_count)
        for event in new_events:
            event_type = event.get("type") if isinstance(event, dict) else None
            if event_type == "error":
                return flask.jsonify(
                    {
                        "status": "error",
                        "error": event.get("error", "Unknown error"),
                        "session_id": session_id,
                    }
                ), 500
            if event_type == "generation_progress":
                # First token received — LLM call succeeded
                return flask.jsonify(
                    {
                        "status": "ok",
                        "message": "Step started",
                        "session_id": session_id,
                    }
                )
        session.event_flag.clear()
        session.event_flag.wait(timeout=_POLL_INTERVAL)

    # Timeout without error or token — generation is slow but not failed
    return flask.jsonify(
        {"status": "ok", "message": "Step started", "session_id": session_id}
    )


@sessions_api.route(
    "/api/v2/conversations/<string:conversation_id>/tool/confirm", methods=["POST"]
)
@require_auth
@api_doc(
    summary="Confirm tool execution (V2)",
    description="Confirm, edit, skip, or auto-confirm a pending tool execution. "
    "session_id is optional - if not provided, the tool will be found across all sessions for the conversation.",
    request_body=ToolConfirmRequest,
    responses={200: StatusResponse, 400: ErrorResponse, 404: ErrorResponse},
    parameters=[CONVERSATION_ID_PARAM],
    tags=["sessions"],
)
def api_conversation_tool_confirm(conversation_id: str):
    """Confirm or modify a tool execution.

    session_id is optional. If not provided, the tool will be found across all
    sessions for this conversation. This handles the race condition where the
    client may not have received the session_id yet when confirming a tool.
    """
    if error := _validate_conversation_id(conversation_id):
        return error

    req_json = _get_request_json_object()
    if not isinstance(req_json, dict):
        return req_json
    session_id = req_json.get("session_id")
    tool_id = req_json.get("tool_id")
    action = req_json.get("action")

    if not tool_id or not action:
        return (
            flask.jsonify({"error": "tool_id and action are required"}),
            400,
        )

    session: ConversationSession | None = None

    if session_id:
        # If session_id provided, use it directly
        session = SessionManager.get_session(session_id)
        if session is None:
            return flask.jsonify({"error": f"Session not found: {session_id}"}), 404
        if tool_id not in session.pending_tools:
            return flask.jsonify({"error": f"Tool not found: {tool_id}"}), 404
    else:
        # If no session_id, search for the tool across all sessions for this conversation
        for sess in SessionManager.get_sessions_for_conversation(conversation_id):
            if tool_id in sess.pending_tools:
                session = sess
                break

        if session is None:
            return (
                flask.jsonify(
                    {
                        "error": f"Tool not found in any session for conversation: {tool_id}"
                    }
                ),
                404,
            )

    # Use .get() to avoid KeyError if tool was concurrently removed (e.g., by another
    # request or the session step thread) between the check above and this access.
    tool_exec = session.pending_tools.get(tool_id)
    if tool_exec is None:
        return (
            flask.jsonify(
                {
                    "error": f"Tool {tool_id} no longer pending (may have been executed or cancelled)"
                }
            ),
            404,
        )

    logdir = get_logs_dir() / conversation_id
    chat_config = ChatConfig.load_or_create(logdir, ChatConfig())

    # Get model from session config, default model, or fallback to "anthropic"
    default_model = get_default_model()
    model = chat_config.model or (default_model.full if default_model else "anthropic")

    # Try to resolve via hook system (for hook-based confirmation flow)
    # This enables future integration where tools use hooks for confirmation
    resolve_hook_confirmation(tool_id, action, req_json.get("content"))

    if action == "confirm":
        # Execute the tool
        tooluse = tool_exec.tooluse

        logger.info(f"Executing runnable tooluse: {tooluse}")
        start_tool_execution(
            conversation_id, session, tool_id, tooluse, model, chat_config
        )
        return flask.jsonify({"status": "ok", "message": "Tool confirmed"})

    if action == "edit":
        # Edit and then execute the tool
        edited_content = req_json.get("content")
        if not edited_content or not isinstance(edited_content, str):
            return flask.jsonify(
                {"error": "content must be a non-empty string for edit action"}
            ), 400

        # Execute with edited content
        start_tool_execution(
            conversation_id,
            session,
            tool_id,
            dataclasses.replace(tool_exec.tooluse, content=edited_content),
            model,
            chat_config,
        )

    elif action == "skip":
        # Skip the tool execution
        tool_exec.status = ToolStatus.SKIPPED
        session.pending_tools.pop(
            tool_id, None
        )  # use pop to avoid KeyError if concurrently removed

        # Provide meaningful message to prevent LLM from re-suggesting the same tool
        tool_name = tool_exec.tooluse.tool
        msg = Message(
            "system",
            f"User chose not to execute this {tool_name} tool. "
            "Do not re-suggest the same action unless explicitly requested.",
        )
        _append_and_notify(LogManager.load(conversation_id, lock=False), session, msg)

        # Resume generation
        _start_step_thread(conversation_id, session, model, chat_config.workspace)

    elif action == "auto":
        # Enable auto-confirmation for future tools
        count = req_json.get("count", 1)
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            return flask.jsonify({"error": "count must be a positive integer"}), 400

        session.auto_confirm_count = count

        # Also confirm this tool
        start_tool_execution(
            conversation_id, session, tool_id, tool_exec.tooluse, model, chat_config
        )
    else:
        return flask.jsonify({"error": f"Unknown action: {action}"}), 400

    return flask.jsonify({"status": "ok", "message": f"Tool {action}ed"})


@sessions_api.route(
    "/api/v2/conversations/<string:conversation_id>/rerun",
    methods=["POST"],
)
@require_auth
@api_doc(
    summary="Re-run tools from an assistant message (V2)",
    description="Parse tool uses from an assistant message and set them as pending for execution. "
    "This re-creates the tool confirmation flow without calling the LLM.",
    responses={200: StatusResponse, 400: ErrorResponse, 404: ErrorResponse},
    parameters=[CONVERSATION_ID_PARAM],
    tags=["sessions"],
)
def api_conversation_rerun(conversation_id: str):
    """Re-run tools from the last assistant message.

    Parses tool uses from the last assistant message content and sets them
    as pending for confirmation/execution, without calling the LLM.
    """
    if error := _validate_conversation_id(conversation_id):
        return error
    from ..tools import ToolUse

    req_json = _get_request_json_object()
    if not isinstance(req_json, dict):
        return req_json
    session_id = req_json.get("session_id")

    if not session_id:
        return flask.jsonify({"error": "session_id is required"}), 400

    session = SessionManager.get_session(session_id)
    if not session:
        return flask.jsonify({"error": f"Session not found: {session_id}"}), 404

    if session.generating:
        return flask.jsonify(
            {"error": "Cannot rerun while generation is in progress"}
        ), 409

    # Load conversation and find the last assistant message
    try:
        manager = LogManager.load(conversation_id, lock=False)
    except FileNotFoundError:
        return flask.jsonify(
            {"error": f"Conversation not found: {conversation_id}"}
        ), 404

    # Find the last assistant message
    last_assistant = None
    for msg in reversed(list(manager.log.messages)):
        if msg.role == "assistant":
            last_assistant = msg
            break

    if not last_assistant:
        return flask.jsonify({"error": "No assistant message found"}), 400

    # Parse tool uses from the message content
    tooluses = list(ToolUse.iter_from_content(last_assistant.content))
    if not tooluses:
        return flask.jsonify(
            {"error": "No tool uses found in the last assistant message"}
        ), 400

    # Set them as pending (same flow as step() tool detection)
    first_auto_id: str | None = None
    logdir = get_logs_dir() / conversation_id
    chat_config = ChatConfig.load_or_create(logdir, ChatConfig())
    default_model = get_default_model()
    model = chat_config.model or (default_model.full if default_model else "anthropic")

    for tooluse in tooluses:
        tool_id = str(uuid.uuid4())
        tool_exec = ToolExecution(
            tool_id=tool_id,
            tooluse=tooluse,
            auto_confirm=session.auto_confirm_count > 0,
        )
        session.pending_tools[tool_id] = tool_exec

        SessionManager.add_event(
            conversation_id,
            {
                "type": "tool_pending",
                "tool_id": tool_id,
                "tooluse": {
                    "tool": tooluse.tool,
                    "args": tooluse.args,
                    "content": tooluse.content,
                },
                "auto_confirm": tool_exec.auto_confirm,
            },
        )

        if tool_exec.auto_confirm:
            if session.auto_confirm_count > 0:
                session.auto_confirm_count -= 1
            if first_auto_id is None:
                first_auto_id = tool_id

    # Start execution for only the first auto-confirm tool.
    # execute_tool_thread will chain the remaining tools serially (same as step()).
    if first_auto_id is not None:
        start_tool_execution(
            conversation_id,
            session,
            first_auto_id,
            session.pending_tools[first_auto_id].tooluse,
            model,
            chat_config,
        )

    return flask.jsonify(
        {
            "status": "ok",
            "message": f"Re-running {len(tooluses)} tool(s)",
            "tool_ids": list(session.pending_tools),
        }
    )


@sessions_api.route(
    "/api/v2/conversations/<string:conversation_id>/elicit/respond",
    methods=["POST"],
)
@require_auth
@api_doc(
    summary="Respond to elicitation (V2)",
    description="Respond to an agent's elicitation request with user input. "
    "Accepts values for text, choice, secret, confirmation, multi_choice, and form types.",
    request_body=ElicitRespondRequest,
    responses={200: StatusResponse, 400: ErrorResponse},
    parameters=[CONVERSATION_ID_PARAM],
    tags=["sessions"],
)
def api_conversation_elicit_respond(conversation_id: str):
    """Respond to an elicitation request from the agent.

    The agent requested structured input via the elicit tool. The client
    displays an appropriate UI and sends the user's response here.

    Note: conversation_id is accepted for URL consistency but not validated
    against elicit_id. The elicitation registry uses globally unique UUIDs,
    so cross-conversation resolution is not a practical concern.
    """
    if error := _validate_conversation_id(conversation_id):
        return error
    req_json = _get_request_json_object()
    if not isinstance(req_json, dict):
        return req_json
    elicit_id = req_json.get("elicit_id")
    action = req_json.get("action")

    if not elicit_id or not action:
        return (
            flask.jsonify({"error": "elicit_id and action are required"}),
            400,
        )

    if action not in ("accept", "decline", "cancel"):
        return (
            flask.jsonify(
                {
                    "error": f"Unknown action: {action}. Must be accept, decline, or cancel"
                }
            ),
            400,
        )

    resolve_hook_elicitation(
        elicit_id, action, req_json.get("value"), req_json.get("values")
    )

    return flask.jsonify({"status": "ok", "message": f"Elicitation {action}ed"})


@sessions_api.route(
    "/api/v2/conversations/<string:conversation_id>/interrupt", methods=["POST"]
)
@require_auth
@api_doc(
    summary="Interrupt conversation (V2)",
    description="Interrupt the current generation or tool execution in a conversation",
    request_body=InterruptRequest,
    responses={200: StatusResponse, 400: ErrorResponse, 404: ErrorResponse},
    parameters=[CONVERSATION_ID_PARAM],
    tags=["sessions"],
)
def api_conversation_interrupt(conversation_id: str):
    """Interrupt the current generation or tool execution."""
    if error := _validate_conversation_id(conversation_id):
        return error
    req_json = _get_request_json_object()
    if not isinstance(req_json, dict):
        return req_json
    session_id = req_json.get("session_id")

    if not session_id:
        return flask.jsonify({"error": "session_id is required"}), 400

    session = SessionManager.get_session(session_id)
    if session is None:
        return flask.jsonify({"error": f"Session not found: {session_id}"}), 404

    if not session.generating and not session.pending_tools:
        # Idempotent: if nothing is generating, treat as already interrupted
        return flask.jsonify(
            {"status": "ok", "message": "Already interrupted or not generating"}
        )

    # Mark session as not generating
    session.generating = False
    session.generating_since = None

    # Clear pending tools
    session.pending_tools.clear()

    # Notify about interruption
    SessionManager.add_event(conversation_id, {"type": "interrupted"})

    return flask.jsonify({"status": "ok", "message": "Interrupted"})
