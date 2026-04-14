"""Session step execution — generation, tool execution, and ACP runtime management.

Extracted from api_v2_sessions.py to separate execution logic (how steps are
generated and tools are executed) from the data models and Flask route handlers.

Functions here are internal implementation details called by the API route
handlers in api_v2_sessions.py.
"""

import asyncio
import atexit
import logging
import os
import threading
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import ChatConfig
from ..dirs import get_logs_dir
from ..executor import prepare_execution_environment
from ..hooks import HookType, trigger_hook
from ..hooks.confirm import ConfirmationResult
from ..llm import _chat_complete, _stream
from ..logmanager import LogManager, prepare_messages
from ..message import Message
from ..telemetry import trace_function
from ..tools import ToolUse, get_tools
from ..tools.shell import set_workspace_cwd
from .api_v2_common import ConfigChangedEvent, ErrorEvent, msg2dict
from .session_models import (
    ConversationSession,
    SessionManager,
    ToolExecution,
    ToolStatus,
)

if TYPE_CHECKING:
    from .acp_session_runtime import AcpSessionRuntime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ACP Health Monitor
# ---------------------------------------------------------------------------

_health_monitor_thread: threading.Thread | None = None
_health_monitor_stop = threading.Event()
_health_monitor_atexit_registered = False
_health_monitor_lock = threading.Lock()

# How often the health monitor runs (seconds)
_HEALTH_CHECK_INTERVAL = 30
# Max idle time before a session is cleaned up (minutes)
_SESSION_MAX_AGE_MINUTES = 60


def start_acp_health_monitor(interval: int = _HEALTH_CHECK_INTERVAL) -> None:
    """Start a background thread that periodically checks ACP subprocess health.

    The monitor:
    - Cleans up sessions idle longer than ``_SESSION_MAX_AGE_MINUTES``
    - Detects dead ACP subprocesses and removes their sessions
    - Logs subprocess lifecycle events for observability
    """
    global _health_monitor_thread, _health_monitor_atexit_registered

    def _monitor() -> None:
        while not _health_monitor_stop.wait(interval):
            try:
                _run_health_check()
            except Exception:
                logger.exception("Error in ACP health monitor")

    with _health_monitor_lock:
        if _health_monitor_thread is not None:
            logger.debug(
                "ACP health monitor already running (interval arg %ds ignored)",
                interval,
            )
            return  # Already running

        _health_monitor_stop.clear()
        _health_monitor_thread = threading.Thread(
            target=_monitor, daemon=True, name="acp-health-monitor"
        )
        _health_monitor_thread.start()
        # Register atexit handler only once — stop/start cycles re-enter this function
        # but must not accumulate duplicate registrations.
        if not _health_monitor_atexit_registered:
            atexit.register(stop_acp_health_monitor)
            _health_monitor_atexit_registered = True
    logger.info("ACP health monitor started (interval=%ds)", interval)


def stop_acp_health_monitor() -> None:
    """Stop the health monitor and clean up all remaining ACP sessions."""
    global _health_monitor_thread
    with _health_monitor_lock:
        _health_monitor_stop.set()
        if _health_monitor_thread is not None:
            _health_monitor_thread.join(timeout=5)
            if _health_monitor_thread.is_alive():
                logger.warning(
                    "ACP health monitor thread did not exit within 5s — "
                    "thread may still be running"
                )
            _health_monitor_thread = None

    # Best-effort cleanup of all ACP sessions on shutdown
    _cleanup_all_acp_sessions()


def _run_health_check() -> None:
    """Single health check iteration."""
    # 1. Clean inactive sessions (was never called before this change).
    # Note: this intentionally applies to all sessions (not just ACP ones) —
    # the health monitor acts as server-wide session hygiene in ACP deployments.
    # Non-ACP sessions idle for more than _SESSION_MAX_AGE_MINUTES are also evicted.
    SessionManager.clean_inactive_sessions(max_age_minutes=_SESSION_MAX_AGE_MINUTES)

    # 2. Check ACP subprocess health
    for session_id, session in SessionManager.get_all_sessions():
        # Snapshot to a local variable: a concurrent _cleanup_all_acp_sessions()
        # can set session.acp_runtime = None between reads, causing AttributeError.
        acp_runtime = session.acp_runtime
        if acp_runtime is None:
            continue
        if session.generating:
            continue  # Don't disturb active generation
        if not acp_runtime.is_subprocess_alive():
            # Re-check generating flag before removing to narrow the TOCTOU window:
            # a /step request arriving between the check above and remove_session()
            # could start a generation on a session we are about to delete.
            if session.generating:
                continue
            logger.warning(
                "ACP subprocess dead for session %s (conversation=%s, pid=%s), "
                "cleaning up",
                session_id,
                session.conversation_id,
                acp_runtime.process_pid,
            )
            # Null out acp_runtime before remove_session so it doesn't
            # spawn a close_acp_runtime_bg thread for an already-dead process.
            session.acp_runtime = None
            SessionManager.remove_session(session_id)


def _cleanup_all_acp_sessions() -> None:
    """Close all ACP runtimes (called during server shutdown).

    Uses synchronous process termination rather than ``asyncio.run()`` because
    this function is invoked from an atexit handler where the asyncio machinery
    may already be partially torn down.
    """
    acp_sessions = [
        (sid, s)
        for sid, s in SessionManager.get_all_sessions()
        if s.acp_runtime is not None
    ]
    if not acp_sessions:
        return

    logger.info("Shutting down %d ACP session(s)", len(acp_sessions))
    for session_id, session in acp_sessions:
        acp_runtime = session.acp_runtime
        try:
            if acp_runtime is None:
                continue
            acp_runtime.terminate_subprocess_sync()
            logger.debug("Closed ACP runtime for session %s", session_id)
        except Exception:
            logger.warning(
                "Failed to close ACP runtime for session %s",
                session_id,
                exc_info=True,
            )
        finally:
            # Null out acp_runtime before remove_session so it won't re-trigger
            # close_acp_runtime_bg for an already-terminated process.
            session.acp_runtime = None
            # Remove from SessionManager to avoid stale entries surviving shutdown.
            # This is safe in the atexit path and prevents zombie sessions on
            # non-atexit calls (e.g. tests, hypothetical reload scenarios).
            SessionManager.remove_session(session_id)


# ---------------------------------------------------------------------------
# Helper Functions for Generation
# ---------------------------------------------------------------------------


def _get_use_acp_default() -> bool:
    """Return the server-wide default for ACP mode.

    Checks the ``GPTME_USE_ACP_DEFAULT`` environment variable (or its bare
    form ``USE_ACP_DEFAULT``) directly from the process environment.  When set
    to a truthy value (``1``, ``true``, ``yes``, ``on``), new sessions that
    don't explicitly pass ``use_acp`` in the step request will use ACP mode by
    default.

    Reads ``os.environ`` directly rather than going through
    :meth:`Config.from_workspace` to avoid clearing the shared
    ``_get_project_config_cached`` LRU cache on every step request.
    """
    val = os.environ.get("GPTME_USE_ACP_DEFAULT") or os.environ.get("USE_ACP_DEFAULT")
    if val is None:
        return False
    return val.lower() in ("1", "true", "yes", "on")


def _append_and_notify(manager: LogManager, session: ConversationSession, msg: Message):
    """Append a message and notify clients."""
    manager.append(msg)
    if session.conversation_id is None:
        raise ValueError("Server sessions must have conversation_id")
    SessionManager.add_event(
        session.conversation_id,
        {
            "type": "message_added",
            "message": msg2dict(msg, manager.workspace, manager.logdir),
        },
    )


def _persist_generation_error(
    manager: LogManager,
    session: ConversationSession,
    error_message: str,
) -> None:
    """Persist a visible generation error message and notify SSE clients."""
    _append_and_notify(manager, session, Message("system", f"Error: {error_message}"))
    manager.write()


def _try_auto_name_and_notify(
    config: ChatConfig,
    messages: list[Message],
    model: str,
    conversation_id: str,
) -> None:
    """Try auto-naming and notify SSE clients on success."""
    from ..util.auto_naming import try_auto_name

    name = try_auto_name(config, messages, model)
    if name:
        config_event: ConfigChangedEvent = {
            "type": "config_changed",
            "config": config.to_dict(),
            "changed_fields": ["name"],
        }
        SessionManager.add_event(conversation_id, config_event)


def close_acp_runtime_bg(acp_runtime: "AcpSessionRuntime") -> None:
    """Close an ACP runtime in a background thread (handles both sync and async callers)."""
    pid = acp_runtime.process_pid

    def _run() -> None:
        try:
            asyncio.run(acp_runtime.close())
            logger.debug("ACP runtime closed (pid=%s)", pid)
        except Exception:
            logger.warning("Failed to close ACP runtime (pid=%s)", pid, exc_info=True)

    t = threading.Thread(target=_run, daemon=True, name="acp-close")
    t.start()


def _iter_text_from_acp_update(update: Any) -> Iterable[str]:
    """Yield best-effort text chunks from ACP session_update payloads."""
    if update is None:
        return

    # Direct text payloads
    if isinstance(update, str):
        yield update
        return

    if isinstance(update, dict):
        if isinstance(update.get("text"), str):
            yield update["text"]
            return

        # Common shape: {message: {content: [{text: ...}]}}
        message = update.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        yield block["text"]
                return

        # Alternate shape: {content: [{text: ...}]}
        content = update.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    yield block["text"]
            return

    # Dataclass/object-style fallbacks
    text = getattr(update, "text", None)
    if isinstance(text, str):
        yield text
        return

    message = getattr(update, "message", None)
    if message is not None:
        msg_content = getattr(message, "content", None)
        if isinstance(msg_content, list):
            for block in msg_content:
                block_text = getattr(block, "text", None)
                if isinstance(block_text, str):
                    yield block_text


async def _acp_step(
    conversation_id: str,
    session: "ConversationSession",
    workspace: Path,
) -> None:
    """Run one conversation step via the per-session ACP subprocess.

    Sends all *pending* user messages (since the ACP cursor) to the ACP
    runtime and emits SSE events for the final assistant response. Tool
    execution happens autonomously inside the subprocess so no tool-confirmation
    flow is needed here.

    Limitations (compared to the in-process ``step()``):
    - No per-token streaming (response arrives in one chunk)
    - Tool confirmations are auto-approved inside the subprocess
    """

    # Validate acp_runtime is set (use explicit check, not assert which python -O disables)
    if session.acp_runtime is None:
        logger.error(
            "_acp_step called without acp_runtime for session %s", conversation_id
        )
        SessionManager.add_event(
            conversation_id,
            {"type": "error", "error": "Internal error: ACP runtime not initialized"},
        )
        session.generating = False
        session.generating_since = None
        return
    acp_runtime = session.acp_runtime  # snapshot to avoid TOCTOU races

    logdir = get_logs_dir() / conversation_id
    chat_config = ChatConfig.load_or_create(logdir, ChatConfig())
    prepare_execution_environment(
        workspace=workspace,
        tools=chat_config.tools,
        chat_config=chat_config,
    )

    manager = LogManager.load(conversation_id, lock=False)

    # Keep server-side hook semantics aligned with the in-process step path.
    assistant_messages = [m for m in manager.log.messages if m.role == "assistant"]
    if len(assistant_messages) == 0:
        if session_start_msgs := trigger_hook(
            HookType.SESSION_START,
            logdir=logdir,
            workspace=workspace,
            initial_msgs=manager.log.messages,
        ):
            for hook_msg in session_start_msgs:
                _append_and_notify(manager, session, hook_msg)
            manager.write()

    if pre_msgs := trigger_hook(
        HookType.STEP_PRE,
        manager=manager,
    ):
        for hook_msg in pre_msgs:
            _append_and_notify(manager, session, hook_msg)
        manager.write()

    user_messages = [m for m in manager.log.messages if m.role == "user"]
    if not user_messages:
        error_event: ErrorEvent = {
            "type": "error",
            "error": "No user message to process",
        }
        SessionManager.add_event(conversation_id, error_event)
        session.generating = False
        session.generating_since = None
        return

    next_user_index = session.acp_last_user_msg_index + 1
    pending_user_messages = user_messages[next_user_index:]
    if not pending_user_messages:
        duplicate_error_event: ErrorEvent = {
            "type": "error",
            "error": "No new user message to process",
        }
        SessionManager.add_event(conversation_id, duplicate_error_event)
        session.generating = False
        session.generating_since = None
        return

    SessionManager.add_event(conversation_id, {"type": "generation_started"})

    stream_tokens: list[str] = []

    async def _on_acp_update(_session_id: str, update: Any) -> None:
        # Best-effort bridge: forward ACP session_update text chunks to SSE.
        for chunk in _iter_text_from_acp_update(update):
            if not chunk:
                continue
            stream_tokens.append(chunk)
            SessionManager.add_event(
                conversation_id,
                {"type": "generation_progress", "token": chunk},
            )

    acp_runtime.set_on_update(_on_acp_update)

    try:
        final_msg: Message | None = None

        for absolute_index, user_msg in enumerate(
            pending_user_messages,
            start=next_user_index,
        ):
            text, _raw = await acp_runtime.prompt(user_msg.content)
            final_text = "".join(stream_tokens) if stream_tokens else text
            stream_tokens.clear()
            msg = Message("assistant", final_text)
            _append_and_notify(manager, session, msg)
            manager.write()
            session.acp_last_user_msg_index = absolute_index
            final_msg = msg

        if post_msgs := trigger_hook(
            HookType.TURN_POST,
            manager=manager,
        ):
            for hook_msg in post_msgs:
                _append_and_notify(manager, session, hook_msg)

        manager.write()

        if final_msg is None:
            # Should not happen: pending_user_messages was non-empty above, but
            # guard explicitly instead of using assert (disabled by python -O).
            logger.warning(
                "ACP step produced no final message for conversation %s",
                conversation_id,
            )
            no_msg_event: ErrorEvent = {
                "type": "error",
                "error": "ACP step completed but produced no assistant message",
            }
            SessionManager.add_event(conversation_id, no_msg_event)
        else:
            SessionManager.add_event(
                conversation_id,
                {
                    "type": "generation_complete",
                    "message": msg2dict(final_msg, manager.workspace, manager.logdir),
                },
            )

        # Auto-generate display name AFTER signaling generation_complete,
        # so the event isn't blocked by a potentially slow LLM call.
        _try_auto_name_and_notify(
            chat_config,
            manager.log.messages,
            chat_config.model or "",
            conversation_id,
        )
    except Exception as e:
        logger.exception("Error during ACP step: %s", e)
        session.last_error = str(e)
        SessionManager.add_event(conversation_id, {"type": "error", "error": str(e)})
    finally:
        acp_runtime.set_on_update(None)
        session.generating = False
        session.generating_since = None


def _start_acp_step_thread(
    conversation_id: str,
    session: "ConversationSession",
    workspace: Path,
) -> None:
    """Start an ACP-backed step in a background thread."""
    session.last_error = None
    session.generating = True
    session.generating_since = datetime.now(tz=timezone.utc)

    def _run() -> None:
        asyncio.run(_acp_step(conversation_id, session, workspace))

    t = threading.Thread(target=_run, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# In-Process Step Execution
# ---------------------------------------------------------------------------


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

    # Load chat config and prepare execution environment
    logdir = get_logs_dir() / conversation_id
    chat_config = ChatConfig.load_or_create(logdir, ChatConfig())
    prepare_execution_environment(
        workspace=workspace,
        tools=chat_config.tools,
        chat_config=chat_config,
    )

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

    # Set the workspace directory for the shell via thread-safe ContextVar.
    # This ensures each session's shell starts in the correct directory even
    # when multiple sessions are being served concurrently.
    # We also keep os.chdir() for the first message as a fallback for tools
    # that still use Path.cwd() (save, read, patch, etc.) — a full migration
    # to workspace-aware helpers is tracked separately.
    set_workspace_cwd(str(workspace))
    user_messages = [msg for msg in manager.log.messages if msg.role == "user"]
    if len(user_messages) <= 1:
        logger.debug(
            f"One or fewer user messages found, changing directory to workspace: {workspace}"
        )
        os.chdir(workspace)

    # Trigger STEP_PRE hook BEFORE preparing messages
    # This ensures hook messages are included in the LLM input
    if pre_msgs := trigger_hook(
        HookType.STEP_PRE,
        manager=manager,
    ):
        for msg in pre_msgs:
            _append_and_notify(manager, session, msg)
        # Write messages to disk to ensure they're persisted
        manager.write()
        logger.debug("Wrote step.pre hook messages to disk")

    # Prepare messages for the model
    msgs = prepare_messages(manager.log.messages)
    if not msgs:
        _persist_generation_error(manager, session, "No messages to process")
        error_event: ErrorEvent = {
            "type": "error",
            "error": "No messages to process",
        }
        SessionManager.add_event(conversation_id, error_event)
        session.generating = False
        session.generating_since = None
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
        # Handle streaming vs non-streaming differently
        metadata = None
        if stream:
            stream_wrapper = _stream(msgs, model, tools)
            chunks: Iterable[str] = stream_wrapper
        else:
            response, metadata = _chat_complete(msgs, model, tools)
            chunks = [response]  # Wrap in list to iterate
            stream_wrapper = None

        for token in (char for chunk in chunks for char in chunk):
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

        # Capture metadata from stream after iteration completes
        if (
            stream_wrapper is not None
            and hasattr(stream_wrapper, "metadata")
            and stream_wrapper.metadata
        ):
            metadata = stream_wrapper.metadata

        # Persist the assistant message
        msg = Message("assistant", output, metadata=metadata)
        _append_and_notify(manager, session, msg)
        # Write immediately after assistant message to ensure it's persisted
        manager.write()
        logger.debug("Persisted assistant message and wrote to disk")

        # Trigger TURN_POST hook (turn.post - after message processing completes)
        if post_msgs := trigger_hook(
            HookType.TURN_POST,
            manager=manager,
        ):
            for msg in post_msgs:
                _append_and_notify(manager, session, msg)

        # Write messages to disk to ensure they're persisted
        # This fixes race condition where messages might not be available when log is retrieved
        manager.write()
        logger.debug("Wrote messages to disk")

        # Signal message generation complete
        logger.debug("Generation complete")
        SessionManager.add_event(
            conversation_id,
            {
                "type": "generation_complete",
                "message": msg2dict(msg, manager.workspace, manager.logdir),
            },
        )

        # Auto-generate display name AFTER signaling generation_complete,
        # so the event isn't blocked by a potentially slow LLM call.
        # The CLI already runs this in a background thread (chat.py).
        _try_auto_name_and_notify(
            chat_config, manager.log.messages, model, conversation_id
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

    except Exception as e:
        logger.exception(f"Error during step execution: {e}")
        error_message = str(e) or "Generation failed"
        session.last_error = error_message
        try:
            _persist_generation_error(manager, session, error_message)
        except Exception:
            logger.exception("Failed to persist generation error message")
        SessionManager.add_event(
            conversation_id, {"type": "error", "error": error_message}
        )
    finally:
        session.generating = False
        session.generating_since = None


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
        # Set context vars for hook-based confirmation
        from ..hooks import current_conversation_id, current_session_id

        current_conversation_id.set(conversation_id)
        current_session_id.set(session.id)

        # Prepare execution environment (config, tools, hooks, .env)
        prepare_execution_environment(
            workspace=chat_config.workspace,
            tools=None,
            chat_config=chat_config,
        )

        # Load the conversation
        manager = LogManager.load(conversation_id, lock=False)

        # Use .get() to atomically retrieve and handle concurrent removal — the API
        # endpoint or another thread may have deleted this entry between the caller's
        # check and our execution here.
        tool_exec = session.pending_tools.get(tool_id)
        if tool_exec is None:
            logger.warning(
                f"Tool {tool_id} not found in pending tools (may have been handled by another thread)"
            )
            return
        tool_exec.status = ToolStatus.EXECUTING

        # use explicit tooluse if set (may be modified), else use the one from the pending tool
        tooluse: ToolUse = edited_tooluse or tool_exec.tooluse

        # Remove the tool from pending (use pop to avoid KeyError if concurrently removed)
        session.pending_tools.pop(tool_id, None)

        # Notify about tool execution
        SessionManager.add_event(
            conversation_id, {"type": "tool_executing", "tool_id": tool_id}
        )
        logger.info(f"Tool {tool_id} executing")

        # Execute the tool
        try:
            logger.info(f"Executing tool: {tooluse.tool}")
            tool_outputs = list(
                tooluse.execute(log=manager.log, workspace=manager.workspace)
            )
            logger.info(f"Tool execution complete, outputs: {len(tool_outputs)}")

            # Store the tool outputs
            for tool_output in tool_outputs:
                _append_and_notify(manager, session, tool_output)
        except Exception as e:
            logger.exception(f"Error executing tool {tooluse.tool}: {e}")
            tool_exec.status = ToolStatus.FAILED

            msg = Message("system", f"Error: {e!s}")
            _append_and_notify(manager, session, msg)

        # Persist tool outputs to disk (every other _append_and_notify call
        # site is followed by manager.write(); without this, tool outputs
        # survive only in memory until the next step writes)
        manager.write()

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
) -> None:
    """Start a step execution in a background thread.

    Clears any previous error state before starting.
    """

    # Clear previous error and mark as generating before starting thread
    # to avoid race condition where interrupt is called before the thread
    # sets generating=True.
    #
    # NOTE: the /step route handler also sets session.generating = True early
    # (under step_lock, with try/finally guard).  This assignment is still
    # needed because _start_step_thread is called directly from the
    # tool-confirm endpoint (api_conversation_tool_response), which bypasses
    # the /step route's guard.
    session.last_error = None
    session.generating = True
    session.generating_since = datetime.now(tz=timezone.utc)

    def step_thread() -> None:
        step(
            conversation_id=conversation_id,
            session=session,
            model=model,
            workspace=workspace,
            branch=branch,
            auto_confirm=auto_confirm,
            stream=stream,
        )

    # Start step execution in a thread
    thread = threading.Thread(target=step_thread)
    thread.daemon = True
    thread.start()


# ---------------------------------------------------------------------------
# Hook Resolution Helpers
# ---------------------------------------------------------------------------


def resolve_hook_confirmation(
    tool_id: str,
    action: str,
    edited_content: str | None = None,
) -> None:
    """Resolve a pending hook-based confirmation.

    This is called when the HTTP endpoint receives a tool confirmation response.
    It converts the HTTP action to a ConfirmationResult and signals any waiting hooks.

    Args:
        tool_id: The tool ID being confirmed
        action: The action (confirm, skip, edit, auto)
        edited_content: Content for edit action
    """
    try:
        from ..hooks.server_confirm import resolve_pending
    except ImportError:
        return  # Hook module not available

    # Convert HTTP action to ConfirmationResult
    if action == "confirm" or action == "auto":
        result = ConfirmationResult.confirm()
    elif action == "skip":
        result = ConfirmationResult.skip("Skipped by user")
    elif action == "edit":
        if edited_content:
            result = ConfirmationResult.edit(edited_content)
        else:
            result = ConfirmationResult.confirm()
    else:
        return  # Unknown action

    # Try to resolve - this will signal any waiting hooks
    resolve_pending(tool_id, result)


def resolve_hook_elicitation(
    elicit_id: str,
    action: str,
    value: str | None = None,
    values: list[str] | None = None,
) -> None:
    """Resolve a pending hook-based elicitation.

    Called by the HTTP endpoint when the client responds to an elicitation request.

    Args:
        elicit_id: The elicitation ID to resolve
        action: The action (accept, decline, cancel)
        value: Response value for text/choice/secret/confirmation/form types
        values: Selected values for multi_choice type
    """
    try:
        from ..hooks.elicitation import ElicitationResponse
        from ..hooks.server_elicit import get_pending, resolve_pending
    except ImportError:
        return  # Hook module not available

    if action == "cancel":
        result = ElicitationResponse.cancel()
    elif action == "decline":
        result = ElicitationResponse(cancelled=False, value=None)
    elif action == "accept":
        # Look up the pending request to check if sensitive (e.g. secret-type)
        pending = get_pending(elicit_id)
        is_sensitive = pending.request.sensitive if pending else False
        if values is not None:
            result = ElicitationResponse.multi(values)
        elif value is not None:
            result = ElicitationResponse.text(value, sensitive=is_sensitive)
        else:
            result = ElicitationResponse.text("", sensitive=is_sensitive)
    else:
        return  # Unknown action

    resolve_pending(elicit_id, result)
