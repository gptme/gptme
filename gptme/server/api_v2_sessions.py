"""
V2 API sessions and real-time conversation management.

Handles session management, step execution, tool confirmation, and event streaming
for real-time conversation interactions.
"""

import dataclasses
import logging
import os
import threading
import uuid
from collections import defaultdict
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

import flask
from dotenv import load_dotenv
from flask import request

from gptme.config import ChatConfig, Config, set_config

from ..dirs import get_logs_dir
from ..hooks import HookType, init_hooks, trigger_hook
from ..llm import _chat_complete, _stream
from ..llm.models import get_default_model
from ..logmanager import LogManager, prepare_messages
from ..message import Message
from ..telemetry import trace_function
from ..tools import ToolUse, get_tools, init_tools
from .api_v2_common import (
    ConfigChangedEvent,
    ErrorEvent,
    EventType,
    msg2dict,
)
from .auth import require_auth
from .openapi_docs import (
    CONVERSATION_ID_PARAM,
    ErrorResponse,
    InterruptRequest,
    StatusResponse,
    StepRequest,
    ToolConfirmRequest,
    api_doc,
)

logger = logging.getLogger(__name__)


# Session Management
# ------------------


class ToolStatus(Enum):
    """Status of a tool execution."""

    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class ToolExecution:
    """Tracks a tool execution."""

    tool_id: str
    tooluse: ToolUse
    status: ToolStatus = ToolStatus.PENDING
    auto_confirm: bool = False


@dataclass
class ConversationSession:
    """Session for a conversation."""

    id: str
    conversation_id: str
    active: bool = True
    generating: bool = False
    last_activity: datetime = field(default_factory=datetime.now)
    events: list[EventType] = field(default_factory=list)
    pending_tools: dict[str, ToolExecution] = field(default_factory=dict)
    auto_confirm_count: int = 0
    clients: set[str] = field(default_factory=set)
    event_flag: threading.Event = field(default_factory=threading.Event)


class SessionManager:
    """Manages conversation sessions."""

    _sessions: dict[str, ConversationSession] = {}
    _conversation_sessions: dict[str, set[str]] = defaultdict(set)

    @classmethod
    def create_session(cls, conversation_id: str) -> ConversationSession:
        """Create a new session for a conversation."""
        session_id = str(uuid.uuid4())
        session = ConversationSession(id=session_id, conversation_id=conversation_id)
        cls._sessions[session_id] = session
        cls._conversation_sessions[conversation_id].add(session_id)
        return session

    @classmethod
    def get_session(cls, session_id: str) -> ConversationSession | None:
        """Get a session by ID."""
        return cls._sessions.get(session_id)

    @classmethod
    def get_sessions_for_conversation(
        cls, conversation_id: str
    ) -> list[ConversationSession]:
        """Get all sessions for a conversation."""
        return [
            cls._sessions[sid]
            for sid in cls._conversation_sessions.get(conversation_id, set())
            if sid in cls._sessions
        ]

    @classmethod
    def add_event(cls, conversation_id: str, event: EventType) -> None:
        """Add an event to all sessions for a conversation."""
        for session in cls.get_sessions_for_conversation(conversation_id):
            session.events.append(event)
            session.last_activity = datetime.now()
            session.event_flag.set()  # Signal that new events are available

    @classmethod
    def clean_inactive_sessions(cls, max_age_minutes: int = 60) -> None:
        """Clean up inactive sessions."""
        cutoff = datetime.now() - timedelta(minutes=max_age_minutes)
        to_remove = []

        for session_id, session in cls._sessions.items():
            if session.last_activity < cutoff and not session.generating:
                to_remove.append(session_id)

        for session_id in to_remove:
            cls.remove_session(session_id)

    @classmethod
    def remove_session(cls, session_id: str) -> None:
        """Remove a session."""
        if session_id in cls._sessions:
            conversation_id = cls._sessions[session_id].conversation_id

            # Trigger SESSION_END hook when removing the last session for a conversation
            is_last_session = (
                conversation_id in cls._conversation_sessions
                and len(cls._conversation_sessions[conversation_id]) == 1
                and session_id in cls._conversation_sessions[conversation_id]
            )

            if is_last_session:
                try:
                    # Load the conversation to trigger the hook
                    from ..logmanager import LogManager

                    manager = LogManager.load(conversation_id, lock=True)

                    logger.debug(
                        f"Last session for conversation {conversation_id}, triggering SESSION_END hook"
                    )
                    if session_end_msgs := trigger_hook(
                        HookType.SESSION_END,
                        manager=manager,
                    ):
                        for msg in session_end_msgs:
                            manager.append(
                                msg
                            )  # Just append, no notify needed during cleanup
                except Exception as e:
                    logger.warning(f"Failed to trigger SESSION_END hook: {e}")

            if conversation_id in cls._conversation_sessions:
                cls._conversation_sessions[conversation_id].discard(session_id)
                if not cls._conversation_sessions[conversation_id]:
                    del cls._conversation_sessions[conversation_id]
            del cls._sessions[session_id]

    @classmethod
    def remove_all_sessions_for_conversation(cls, conversation_id: str) -> None:
        """Remove all sessions for a conversation."""
        for session in cls.get_sessions_for_conversation(conversation_id):
            cls.remove_session(session.id)


# Helper Functions for Generation
# ------------------------------


def _append_and_notify(manager: LogManager, session: ConversationSession, msg: Message):
    """Append a message and notify clients."""
    manager.append(msg)
    SessionManager.add_event(
        session.conversation_id,
        {
            "type": "message_added",
            "message": msg2dict(msg, manager.workspace),
        },
    )


def auto_generate_display_name(messages: list[Message], model: str) -> str | None:
    """Generate a display name for the conversation based on the messages."""
    from ..util.auto_naming import (
        auto_generate_display_name as _auto_generate_display_name,
    )

    return _auto_generate_display_name(messages, model)


@trace_function("api_v2.step", attributes={"component": "api_v2"})
def step(
    conversation_id: str,
    session: ConversationSession,
    model: str,
    workspace: Path,
    branch: str = "main",
    auto_confirm: bool = False,
    stream: bool = True,
) -> None:
    """
    Generate a response and detect tools.

    This function handles generating a response from the LLM and detecting tools
    in the response. When tools are detected, it creates a pending tool record
    and either waits for confirmation or auto-confirms based on settings.

    It's designed to be used both for initial generation and for continuing
    after tool execution is complete.

    Args:
        conversation_id: The conversation ID
        session: The current session
        model: Model to use
        workspace: Workspace to use
        branch: Branch to use (default: "main")
        auto_confirm: Whether to auto-confirm tools (default: False)
        stream: Whether to stream the response (default: True)
    """

    # Create and set config
    config = Config.from_workspace(workspace=workspace)
    logdir = get_logs_dir() / conversation_id
    chat_config = ChatConfig.load_or_create(logdir, ChatConfig())
    config.chat = chat_config
    set_config(config)

    # Load .env file if present
    load_dotenv(dotenv_path=workspace / ".env")

    # Initialize tools and hooks in this thread
    init_tools(chat_config.tools)
    init_hooks()

    # Load conversation
    manager = LogManager.load(
        conversation_id,
        branch=branch,
        lock=False,
    )

    # Set the model as default before triggering hooks
    # This ensures hooks like token_awareness can access the model
    from ..llm.models import set_default_model

    set_default_model(model)

    # Trigger SESSION_START hook for new conversations
    assistant_messages = [m for m in manager.log.messages if m.role == "assistant"]
    if len(assistant_messages) == 0:
        logger.debug("New conversation detected, triggering SESSION_START hook")
        if session_start_msgs := trigger_hook(
            HookType.SESSION_START,
            logdir=logdir,
            workspace=workspace,
            initial_msgs=manager.log.messages,
        ):
            for msg in session_start_msgs:
                _append_and_notify(manager, session, msg)
            # Write messages to disk to ensure they're persisted
            manager.write()
            logger.debug("Wrote SESSION_START hook messages to disk")

    # TODO: This is not the best way to manage the chdir state, since it's
    # essentially a shared global across chats (bad), but the fix at least
    # addresses the issue where chats don't start in the directory they should.
    # If we are attempting to make a step in a conversation with only one or fewer user
    # messages, make sure we first chdir to the workspace directory (so that
    # the conversation starts in the right folder).
    user_messages = [msg for msg in manager.log.messages if msg.role == "user"]
    if len(user_messages) <= 1:
        logger.debug(
            f"One or fewer user messages found, changing directory to workspace: {workspace}"
        )
        os.chdir(workspace)

    # Trigger MESSAGE_PRE_PROCESS hook BEFORE preparing messages
    # This ensures hook messages are included in the LLM input
    if pre_msgs := trigger_hook(
        HookType.MESSAGE_PRE_PROCESS,
        manager=manager,
    ):
        for msg in pre_msgs:
            _append_and_notify(manager, session, msg)
        # Write messages to disk to ensure they're persisted
        manager.write()
        logger.debug("Wrote MESSAGE_PRE_PROCESS hook messages to disk")

    # Prepare messages for the model
    msgs = prepare_messages(manager.log.messages)
    if not msgs:
        error_event: ErrorEvent = {
            "type": "error",
            "error": "No messages to process",
        }
        SessionManager.add_event(conversation_id, error_event)
        session.generating = False
        return

    # Notify clients about generation status
    SessionManager.add_event(conversation_id, {"type": "generation_started"})

    tool_format = chat_config.tool_format
    tools = None
    if tool_format == "tool":
        tools = [t for t in get_tools() if t.is_runnable]

    try:
        # Stream tokens from the model
        output = ""
        tooluses = []
        for token in (
            char
            for chunk in (_stream if stream else _chat_complete)(msgs, model, tools)
            for char in chunk
        ):
            # check if interrupted
            if not session.generating:
                output += " [INTERRUPTED]"
                break

            output += token

            # Send token to clients
            SessionManager.add_event(
                conversation_id, {"type": "generation_progress", "token": token}
            )

            # Check for complete tool uses on \n
            if "\n" in token:
                if tooluses := list(ToolUse.iter_from_content(output)):
                    break
        else:
            tooluses = list(ToolUse.iter_from_content(output))

        # Persist the assistant message
        msg = Message("assistant", output)
        _append_and_notify(manager, session, msg)
        # Write immediately after assistant message to ensure it's persisted
        manager.write()
        logger.debug("Persisted assistant message and wrote to disk")

        # Trigger MESSAGE_POST_PROCESS hook
        if post_msgs := trigger_hook(
            HookType.MESSAGE_POST_PROCESS,
            manager=manager,
        ):
            for msg in post_msgs:
                _append_and_notify(manager, session, msg)

        # Write messages to disk to ensure they're persisted
        # This fixes race condition where messages might not be available when log is retrieved
        manager.write()
        logger.debug("Wrote messages to disk")

        # Auto-generate display name for first assistant response if not already set
        # TODO: Consider implementing via hook system to streamline with CLI implementation
        # See: gptme/chat.py for CLI's implementation
        assistant_messages = [m for m in manager.log.messages if m.role == "assistant"]
        if len(assistant_messages) == 1 and not chat_config.name:
            try:
                display_name = auto_generate_display_name(manager.log.messages, model)
                if display_name:
                    chat_config.name = display_name
                    chat_config.save()
                    logger.info(f"Auto-generated display name: {display_name}")

                    # Notify clients about config change
                    config_event: ConfigChangedEvent = {
                        "type": "config_changed",
                        "config": chat_config.to_dict(),
                        "changed_fields": ["name"],
                    }
                    SessionManager.add_event(conversation_id, config_event)
                else:
                    logger.info(
                        "Auto-naming failed, leaving conversation name unset for future retry"
                    )
            except Exception as e:
                logger.warning(f"Failed to auto-generate display name: {e}")

        # Signal message generation complete
        logger.debug("Generation complete")
        SessionManager.add_event(
            conversation_id,
            {
                "type": "generation_complete",
                "message": msg2dict(msg, manager.workspace),
            },
        )

        if len(tooluses) > 1:
            logger.warning(
                "Multiple tools per message not yet supported, expect issues"
            )

        # Handle tool use
        for tooluse in tooluses:
            # Create a tool execution record
            tool_id = str(uuid.uuid4())

            tool_exec = ToolExecution(
                tool_id=tool_id,
                tooluse=tooluse,
                auto_confirm=session.auto_confirm_count > 0 or auto_confirm,
            )
            session.pending_tools[tool_id] = tool_exec

            # Notify about pending tool
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

            # If auto-confirm is enabled, execute the tool
            if tool_exec.auto_confirm:
                if session.auto_confirm_count > 0:
                    session.auto_confirm_count -= 1
                start_tool_execution(
                    conversation_id, session, tool_id, tooluse, model, chat_config
                )

        # Mark session as not generating
        session.generating = False

    except Exception as e:
        logger.exception(f"Error during step execution: {e}")
        SessionManager.add_event(conversation_id, {"type": "error", "error": str(e)})
        session.generating = False


def start_tool_execution(
    conversation_id: str,
    session: ConversationSession,
    tool_id: str,
    edited_tooluse: ToolUse | None,
    model: str,
    chat_config: ChatConfig,
) -> threading.Thread:
    """Execute a tool and handle its output."""

    # This function would ideally run asynchronously to not block the request
    # For simplicity, we'll run it in a thread
    @trace_function("api_v2.execute_tool", attributes={"component": "api_v2"})
    def execute_tool_thread() -> None:
        config = Config.from_workspace(workspace=chat_config.workspace)
        config.chat = chat_config
        set_config(config)

        # Initialize tools and hooks in this thread
        init_tools(None)
        init_hooks()

        # Load .env file if present
        load_dotenv(dotenv_path=chat_config.workspace / ".env")

        # Load the conversation
        manager = LogManager.load(conversation_id, lock=False)

        tool_exec = session.pending_tools[tool_id]
        tool_exec.status = ToolStatus.EXECUTING

        # use explicit tooluse if set (may be modified), else use the one from the pending tool
        tooluse: ToolUse = edited_tooluse or tool_exec.tooluse

        # Remove the tool from pending
        if tool_id in session.pending_tools:
            del session.pending_tools[tool_id]

        # Notify about tool execution
        SessionManager.add_event(
            conversation_id, {"type": "tool_executing", "tool_id": tool_id}
        )
        logger.info(f"Tool {tool_id} executing")

        # Execute the tool
        try:
            logger.info(f"Executing tool: {tooluse.tool}")
            tool_outputs = list(
                tooluse.execute(lambda _: True, manager.log, manager.workspace)
            )
            logger.info(f"Tool execution complete, outputs: {len(tool_outputs)}")

            # Store the tool outputs
            for tool_output in tool_outputs:
                _append_and_notify(manager, session, tool_output)
        except Exception as e:
            logger.exception(f"Error executing tool {tooluse.__class__.__name__}: {e}")
            tool_exec.status = ToolStatus.FAILED

            msg = Message("system", f"Error: {str(e)}")
            _append_and_notify(manager, session, msg)

        # This implements auto-stepping similar to the CLI behavior
        _start_step_thread(conversation_id, session, model, chat_config.workspace)

    # Start execution in a thread
    thread = threading.Thread(target=execute_tool_thread)
    thread.daemon = True
    thread.start()
    return thread


def _start_step_thread(
    conversation_id: str,
    session: ConversationSession,
    model: str,
    workspace: Path,
    branch: str = "main",
    auto_confirm: bool = False,
    stream: bool = True,
):
    """Start a step execution in a background thread."""

    def step_thread() -> None:
        try:
            # Mark session as generating
            session.generating = True

            step(
                conversation_id=conversation_id,
                session=session,
                model=model,
                workspace=workspace,
                branch=branch,
                auto_confirm=auto_confirm,
                stream=stream,
            )

        except Exception as e:
            logger.exception(f"Error during step execution: {e}")
            SessionManager.add_event(
                conversation_id, {"type": "error", "error": str(e)}
            )
            session.generating = False

    # Start step execution in a thread
    thread = threading.Thread(target=step_thread)
    thread.daemon = True
    thread.start()


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

            # Send initial connection event
            yield f"data: {flask.json.dumps({'type': 'connected', 'session_id': session_id})}\n\n"

            # Send immediate ping to ensure connection is established right away
            yield f"data: {flask.json.dumps({'type': 'ping'})}\n\n"

            # Create an event queue
            last_event_index = 0

            while True:
                # Check if there are new events
                if last_event_index < (new_index := len(session.events)):
                    # Send any new events
                    for event in session.events[last_event_index:new_index]:
                        yield f"data: {flask.json.dumps(event)}\n\n"
                    last_event_index = new_index

                # Wait a bit before checking again
                yield f"data: {flask.json.dumps({'type': 'ping'})}\n\n"

                # Use event.wait() with timeout to avoid busy waiting while allowing ping intervals
                # 15s timeout for connection keep-alive
                session.event_flag.wait(timeout=15)
                session.event_flag.clear()

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
    req_json = flask.request.json or {}
    session_id = req_json.get("session_id")
    auto_confirm_int_or_bool: int | bool = req_json.get("auto_confirm", False)

    if not session_id:
        return flask.jsonify({"error": "session_id is required"}), 400

    session = SessionManager.get_session(session_id)
    if session is None:
        return flask.jsonify({"error": f"Session not found: {session_id}"}), 404

    logdir = get_logs_dir() / conversation_id
    chat_config = ChatConfig.load_or_create(logdir, ChatConfig())

    stream = req_json.get("stream", chat_config.stream)

    # if auto_confirm set, set auto_confirm_count
    if isinstance(auto_confirm_int_or_bool, int):
        session.auto_confirm_count = auto_confirm_int_or_bool
    elif isinstance(auto_confirm_int_or_bool, bool):
        session.auto_confirm_count = 1 if auto_confirm_int_or_bool else -1
    auto_confirm = bool(session.auto_confirm_count > 0)

    if session.generating:
        return flask.jsonify({"error": "Generation already in progress"}), 409

    # Get the branch and model
    branch = req_json.get("branch", "main")
    default_model = get_default_model()

    # Get model from request, config, or default (in that order)
    model = req_json.get("model")
    if not model:
        model = chat_config.model
    if not model and default_model:
        model = default_model.full
    if not model:
        # Try to get from environment/config as last resort
        config = Config.from_workspace(workspace=chat_config.workspace)
        model = config.get_env("MODEL")
    if not model:
        return flask.jsonify(
            {"error": "No model specified and no default model set"}
        ), 400

    # Start step execution in a background thread
    # Model will be set in the worker thread by step()
    _start_step_thread(
        conversation_id=conversation_id,
        session=session,
        model=model,
        workspace=chat_config.workspace,
        branch=branch,
        auto_confirm=auto_confirm,
        stream=stream,
    )

    return flask.jsonify(
        {"status": "ok", "message": "Step started", "session_id": session_id}
    )


@sessions_api.route(
    "/api/v2/conversations/<string:conversation_id>/tool/confirm", methods=["POST"]
)
@require_auth
@api_doc(
    summary="Confirm tool execution (V2)",
    description="Confirm, edit, skip, or auto-confirm a pending tool execution",
    request_body=ToolConfirmRequest,
    responses={200: StatusResponse, 400: ErrorResponse, 404: ErrorResponse},
    parameters=[CONVERSATION_ID_PARAM],
    tags=["sessions"],
)
def api_conversation_tool_confirm(conversation_id: str):
    """Confirm or modify a tool execution."""

    req_json = flask.request.json or {}
    session_id = req_json.get("session_id")
    tool_id = req_json.get("tool_id")
    action = req_json.get("action")

    if not session_id or not tool_id or not action:
        return (
            flask.jsonify({"error": "session_id, tool_id, and action are required"}),
            400,
        )

    session = SessionManager.get_session(session_id)
    if session is None:
        return flask.jsonify({"error": f"Session not found: {session_id}"}), 404

    if tool_id not in session.pending_tools:
        return flask.jsonify({"error": f"Tool not found: {tool_id}"}), 404

    tool_exec = session.pending_tools[tool_id]

    logdir = get_logs_dir() / conversation_id
    chat_config = ChatConfig.load_or_create(logdir, ChatConfig())

    # Get model from session config, default model, or fallback to "anthropic"
    default_model = get_default_model()
    model = chat_config.model or (default_model.full if default_model else "anthropic")

    if action == "confirm":
        # Execute the tool
        tooluse = tool_exec.tooluse

        logger.info(f"Executing runnable tooluse: {tooluse}")
        start_tool_execution(
            conversation_id, session, tool_id, tooluse, model, chat_config
        )
        return flask.jsonify({"status": "ok", "message": "Tool confirmed"})

    elif action == "edit":
        # Edit and then execute the tool
        edited_content = req_json.get("content")
        if not edited_content:
            return flask.jsonify({"error": "content is required for edit action"}), 400

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
        del session.pending_tools[tool_id]

        msg = Message("system", f"Skipped tool {tool_id}")
        _append_and_notify(LogManager.load(conversation_id, lock=False), session, msg)

        # Resume generation
        _start_step_thread(conversation_id, session, model, chat_config.workspace)

    elif action == "auto":
        # Enable auto-confirmation for future tools
        count = req_json.get("count", 1)
        if count <= 0:
            return flask.jsonify({"error": "count must be positive"}), 400

        session.auto_confirm_count = count

        # Also confirm this tool
        start_tool_execution(
            conversation_id, session, tool_id, tool_exec.tooluse, model, chat_config
        )
    else:
        return flask.jsonify({"error": f"Unknown action: {action}"}), 400

    return flask.jsonify({"status": "ok", "message": f"Tool {action}ed"})


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
    req_json = flask.request.json or {}
    session_id = req_json.get("session_id")

    if not session_id:
        return flask.jsonify({"error": "session_id is required"}), 400

    session = SessionManager.get_session(session_id)
    if session is None:
        return flask.jsonify({"error": f"Session not found: {session_id}"}), 404

    if not session.generating and not session.pending_tools:
        return (
            flask.jsonify(
                {"error": "No active generation or tool execution to interrupt"}
            ),
            400,
        )

    # Mark session as not generating
    session.generating = False

    # Clear pending tools
    session.pending_tools.clear()

    # Notify about interruption
    SessionManager.add_event(conversation_id, {"type": "interrupted"})

    return flask.jsonify({"status": "ok", "message": "Interrupted"})
