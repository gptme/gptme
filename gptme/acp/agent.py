"""ACP Agent implementation for gptme.

This module implements the Agent Client Protocol, allowing gptme to be used
as a coding agent from any ACP-compatible editor (Zed, JetBrains, etc.).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import get_project_config
from ..dirs import get_logs_dir
from ..init import init
from ..llm.models import get_default_model, set_default_model
from ..logmanager import LogManager, list_conversations
from ..prompts import get_prompt
from ..session import SessionRegistry
from ..tools import get_tools, set_tools
from ..util.auto_naming import generate_conversation_id
from .adapter import acp_content_to_gptme_message, gptme_message_to_acp_content
from .types import (
    PermissionKind,
    PermissionOption,
    ToolCall,
    ToolCallStatus,
    gptme_tool_to_acp_kind,
)

if TYPE_CHECKING:
    from ..message import Message

logger = logging.getLogger(__name__)

# Lazy imports to avoid dependency issues when acp is not installed
Agent: type | None = None
InitializeResponse: type | None = None
NewSessionResponse: type | None = None
PromptResponse: type | None = None
Client: type | None = None
Implementation: type | None = None


def _check_acp_import(cls: type | None, name: str) -> type:
    """Verify a lazy-imported ACP class is available.

    Uses explicit check instead of assert, which can be disabled with python -O.
    See also: server/api.py and server/api_v2.py for the same pattern.
    """
    if cls is None:
        raise RuntimeError(
            f"ACP class {name} not imported; is the 'acp' package installed?"
        )
    return cls


def _import_acp() -> bool:
    """Import ACP modules lazily."""
    global \
        Agent, \
        InitializeResponse, \
        NewSessionResponse, \
        PromptResponse, \
        Client, \
        Implementation
    try:
        from acp import (  # type: ignore[import-not-found]
            Agent as _Agent,
        )
        from acp import (  # type: ignore[import-not-found]
            InitializeResponse as _InitializeResponse,
        )
        from acp import (  # type: ignore[import-not-found]
            NewSessionResponse as _NewSessionResponse,
        )
        from acp import (  # type: ignore[import-not-found]
            PromptResponse as _PromptResponse,
        )
        from acp.interfaces import Client as _Client  # type: ignore[import-not-found]
        from acp.schema import (  # type: ignore[import-not-found]
            Implementation as _Implementation,
        )

        Agent = _Agent
        InitializeResponse = _InitializeResponse
        NewSessionResponse = _NewSessionResponse
        PromptResponse = _PromptResponse
        Client = _Client
        Implementation = _Implementation
        return True
    except ImportError:
        return False


def _agent_info() -> Any:
    """Build the agentInfo for InitializeResponse."""
    _Impl = _check_acp_import(Implementation, "Implementation")
    from gptme import __version__

    return _Impl(name="gptme-acp", version=__version__)


class GptmeAgent:
    """ACP-compatible agent wrapping gptme functionality.

    This agent responds to prompts from ACP-compatible clients (like Zed)
    and executes them using gptme's chat infrastructure.
    """

    def __init__(self) -> None:
        """Initialize the gptme agent."""
        self._conn: Any = None
        self._registry = SessionRegistry()
        self._initialized = False
        self._init_error: str | None = None
        self._model: str | None = None
        self._tools: list[Any] | None = None
        # Per-session model overrides (populated from per-project gptme.toml)
        self._session_models: dict[str, str | None] = {}
        # Per-session mode (default: "default", can be "auto" for no-confirm)
        self._session_modes: dict[str, str] = {}
        # Phase 2: Track active tool calls per session
        self._tool_calls: dict[str, dict[str, ToolCall]] = {}
        # Phase 2: Permission policies per session (allow_always, reject_always)
        self._permission_policies: dict[str, dict[str, str]] = {}
        # Track sessions where AvailableCommandsUpdate was successfully sent.
        # Used to deduplicate sends between _send_session_open_notifications()
        # and the prompt() fallback (in case the deferred notification races
        # with an early first prompt).
        self._session_commands_advertised: set[str] = set()

    def on_connect(self, conn: Any) -> None:
        """Called when a client connects.

        Args:
            conn: The client connection for sending notifications.
        """
        self._conn = conn

    # Phase 2: Tool call methods

    async def _report_tool_call(
        self,
        session_id: str,
        tool_call: ToolCall,
    ) -> None:
        """Report a tool call to the client via session/update.

        Args:
            session_id: The session ID
            tool_call: The tool call to report
        """
        if not self._conn:
            logger.warning("No connection to report tool call")
            return

        # Store tool call
        if session_id not in self._tool_calls:
            self._tool_calls[session_id] = {}
        self._tool_calls[session_id][tool_call.tool_call_id] = tool_call

        await self._conn.session_update(
            session_id=session_id,
            update=tool_call.to_dict(),
            source="gptme",
        )

    async def _update_tool_call(
        self,
        session_id: str,
        tool_call_id: str,
        status: ToolCallStatus,
        content: list[dict[str, Any]] | None = None,
    ) -> None:
        """Update a tool call status via session/update.

        Args:
            session_id: The session ID
            tool_call_id: The tool call ID to update
            status: New status
            content: Optional content to add
        """
        if not self._conn:
            return

        # Update stored tool call
        if session_id in self._tool_calls:
            if tool_call_id in self._tool_calls[session_id]:
                tc = self._tool_calls[session_id][tool_call_id]
                tc.status = status
                if content:
                    tc.content = content

        update: dict[str, Any] = {
            "sessionUpdate": "tool_call_update",
            "toolCallId": tool_call_id,
            "status": status.value,
        }
        if content:
            update["content"] = content

        await self._conn.session_update(
            session_id=session_id,
            update=update,
            source="gptme",
        )

    async def _request_tool_permission(
        self,
        session_id: str,
        tool_call: ToolCall,
    ) -> bool:
        """Request permission to execute a tool call.

        Args:
            session_id: The session ID
            tool_call: The tool call requiring permission

        Returns:
            True if permission granted, False otherwise
        """
        if not self._conn:
            # No connection - auto-allow for backward compatibility
            return True

        # In "auto" mode, skip permission prompts (like --no-confirm)
        if self._session_modes.get(session_id) == "auto":
            logger.debug(
                f"Auto-approving {tool_call.kind.value} in auto mode "
                f"(session={session_id})"
            )
            return True

        # Check cached permission policies
        if session_id in self._permission_policies:
            policies = self._permission_policies[session_id]
            tool_key = f"{tool_call.kind.value}"
            if tool_key in policies:
                return policies[tool_key] == "allow"

        # Request permission from client
        try:
            options = [
                PermissionOption(
                    option_id="allow-once",
                    name="Allow once",
                    kind=PermissionKind.ALLOW_ONCE,
                ),
                PermissionOption(
                    option_id="allow-always",
                    name="Allow always",
                    kind=PermissionKind.ALLOW_ALWAYS,
                ),
                PermissionOption(
                    option_id="reject-once",
                    name="Reject",
                    kind=PermissionKind.REJECT_ONCE,
                ),
                PermissionOption(
                    option_id="reject-always",
                    name="Reject always",
                    kind=PermissionKind.REJECT_ALWAYS,
                ),
            ]

            result = await self._conn.request_permission(
                session_id=session_id,
                tool_call={"toolCallId": tool_call.tool_call_id},
                options=[opt.to_dict() for opt in options],
            )

            outcome = result.outcome
            if outcome.outcome == "cancelled":
                return False

            option_id = outcome.option_id

            # Cache always policies
            if option_id == "allow-always":
                if session_id not in self._permission_policies:
                    self._permission_policies[session_id] = {}
                self._permission_policies[session_id][tool_call.kind.value] = "allow"
                return True
            if option_id == "reject-always":
                if session_id not in self._permission_policies:
                    self._permission_policies[session_id] = {}
                self._permission_policies[session_id][tool_call.kind.value] = "reject"
                return False
            return option_id == "allow-once"

        except Exception as e:
            logger.warning(f"Permission request failed: {e}, auto-allowing")
            return True

    def _create_confirm_with_tools(
        self,
        session_id: str,
        loop: asyncio.AbstractEventLoop,
    ) -> Any:
        """Create a confirm callback that reports tool calls.

        Args:
            session_id: The session ID
            loop: The event loop for async operations

        Returns:
            A confirm callback function
        """

        def confirm_callback(msg: str) -> bool:
            """Confirm callback that reports tool calls to ACP client."""
            # Parse tool name from confirmation message patterns
            # gptme tools use various formats, so we pattern-match common ones
            tool_name = "unknown"
            content_preview = msg[:100]

            # Map confirmation message patterns to tool names
            msg_lower = msg.lower()
            if "run command" in msg_lower:
                tool_name = "shell"
            elif "execute this code" in msg_lower:
                tool_name = "python"
            elif "execute commands" in msg_lower:
                tool_name = "tmux"
            elif "apply patch" in msg_lower:
                tool_name = "patch"
            elif "save to" in msg_lower or "overwrite" in msg_lower:
                tool_name = "save"
            elif "append to" in msg_lower:
                tool_name = "append"
            elif "create" in msg_lower and (
                "file" in msg_lower or "folder" in msg_lower
            ):
                tool_name = "save"
            elif "load mcp server" in msg_lower:
                tool_name = "mcp"
            elif "unload mcp server" in msg_lower:
                tool_name = "mcp"
            elif "restart gptme" in msg_lower:
                tool_name = "restart"

            # Create tool call
            tool_call = ToolCall(
                tool_call_id=ToolCall.generate_id(),
                title=f"Executing {tool_name}",
                kind=gptme_tool_to_acp_kind(tool_name),
                status=ToolCallStatus.PENDING,
                raw_input={"tool": tool_name, "preview": content_preview},
            )

            # Report tool call and request permission (run in event loop)
            async def report_and_request() -> bool:
                await self._report_tool_call(session_id, tool_call)
                allowed = await self._request_tool_permission(session_id, tool_call)

                if allowed:
                    await self._update_tool_call(
                        session_id,
                        tool_call.tool_call_id,
                        ToolCallStatus.IN_PROGRESS,
                    )
                else:
                    await self._update_tool_call(
                        session_id,
                        tool_call.tool_call_id,
                        ToolCallStatus.FAILED,
                        content=[
                            {
                                "type": "content",
                                "content": {
                                    "type": "text",
                                    "text": "Permission denied",
                                },
                            }
                        ],
                    )
                return allowed

            # Run async code in event loop
            # Use configurable timeout for permission requests (default 60s)
            # Longer timeout allows users time to review complex operations
            permission_timeout = 60.0
            future = asyncio.run_coroutine_threadsafe(report_and_request(), loop)
            try:
                return future.result(timeout=permission_timeout)
            except TimeoutError:
                logger.warning(
                    f"Tool permission request timed out after {permission_timeout}s, auto-allowing"
                )
                return True
            except Exception as e:
                logger.warning(f"Tool permission check failed: {e}, auto-allowing")
                return True

        return confirm_callback

    async def _complete_pending_tool_calls(
        self,
        session_id: str,
        success: bool = True,
    ) -> None:
        """Mark all in-progress tool calls as completed.

        Args:
            session_id: The session ID
            success: Whether execution succeeded
        """
        if session_id not in self._tool_calls:
            return

        for tool_call_id, tool_call in self._tool_calls[session_id].items():
            # Complete both IN_PROGRESS and PENDING tool calls
            # PENDING calls may be orphaned if permission request failed to transition
            if tool_call.status in (ToolCallStatus.IN_PROGRESS, ToolCallStatus.PENDING):
                status = ToolCallStatus.COMPLETED if success else ToolCallStatus.FAILED
                await self._update_tool_call(
                    session_id,
                    tool_call_id,
                    status,
                )

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Any | None = None,
        client_info: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        """Handle initialize request from client.

        Args:
            protocol_version: ACP protocol version from client
            client_capabilities: Client's capabilities
            client_info: Client implementation info

        Returns:
            InitializeResponse with negotiated protocol version
        """
        if not _import_acp():
            # Phase 1: Raise exception since we can't construct ACP error response
            # without the package. Future: Consider early validation in __main__.py
            raise RuntimeError(
                "agent-client-protocol package not installed. "
                "Install with: pip install 'gptme[acp]'"
            )

        # Initialize gptme on first connection
        if not self._initialized and not self._init_error:
            try:
                init(
                    model=self._model,
                    interactive=False,
                    tool_allowlist=None,
                    tool_format="markdown",
                )
            except Exception as e:
                # Store the error instead of raising — raising would kill the
                # ACP connection and leave the editor showing "Loading..." with
                # no explanation. The error is surfaced in prompt() instead.
                self._init_error = (
                    f"gptme initialization failed: {e}. "
                    "Ensure API keys are set in environment or config.toml."
                )
                logger.error(self._init_error)
                return _check_acp_import(InitializeResponse, "InitializeResponse")(
                    protocol_version=protocol_version,
                    agent_info=_agent_info(),
                )  # type: ignore[misc]

            self._initialized = True
            # Capture the resolved model (from config/env/auto-detect)
            # so subsequent handlers use the same model
            resolved = get_default_model()
            if resolved:
                self._model = f"{resolved.provider}/{resolved.model}"
            # Store tools for re-setting in other handler contexts
            self._tools = get_tools()

        logger.info(f"ACP Initialize: protocol_version={protocol_version}")
        _InitializeResponse = _check_acp_import(
            InitializeResponse, "InitializeResponse"
        )
        return _InitializeResponse(
            protocol_version=protocol_version,
            agent_info=_agent_info(),
        )

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[Any],
        **kwargs: Any,
    ) -> Any:
        """Create a new gptme session.

        Args:
            cwd: Working directory for the session
            mcp_servers: MCP servers to connect to

        Returns:
            NewSessionResponse with session ID
        """
        if not _import_acp():
            raise RuntimeError(
                "agent-client-protocol package not installed. "
                "Install with: pip install 'gptme[acp]'"
            )

        # Re-set ContextVars that may be missing in this task's context.
        # ACP framework may dispatch each RPC method in a separate asyncio task,
        # so ContextVars set during initialize() aren't visible here.
        if self._model:
            set_default_model(self._model)
        if self._tools is not None:
            set_tools(self._tools)

        # Resolve per-project model from gptme.toml in the session's cwd.
        # Each Zed window may have its own project with a different MODEL in gptme.toml,
        # so we check the project config and override the global model for this session.
        session_model = self._model
        if cwd:
            project_cfg = get_project_config(Path(cwd))
            if project_cfg and project_cfg.env.get("MODEL"):
                session_model = project_cfg.env["MODEL"]
                logger.info(
                    f"ACP NewSession: using per-project model {session_model!r} from {cwd}/gptme.toml"
                )

        # Use conversation ID as session ID for persistence.
        # This makes sessions discoverable by list_sessions and resumable
        # by load_session across ACP server restarts.
        logs_dir = get_logs_dir()
        session_id = generate_conversation_id(name=None, logs_dir=logs_dir)
        logdir = logs_dir / session_id

        # Store per-session model for use in prompt() and other handlers
        self._session_models[session_id] = session_model

        # Apply session model to context for this task
        if session_model and session_model != self._model:
            set_default_model(session_model)

        # Get tools and initial prompt
        tools = get_tools()
        initial_msgs = get_prompt(
            tools=tools,
            tool_format="markdown",
            prompt="full",
            interactive=False,
            model=session_model,
            workspace=Path(cwd) if cwd else None,
        )

        # Create LogManager for this session
        log = LogManager.load(
            logdir=logdir,
            initial_msgs=initial_msgs,
            create=True,
            lock=False,
        )

        self._registry.create(session_id, log=log, cwd=str(cwd) if cwd else None)
        logger.info(f"ACP NewSession: session_id={session_id}, cwd={cwd}")

        # Schedule session-open notifications to run AFTER NewSessionResponse is
        # returned. Sending session/update notifications synchronously during
        # new_session() causes Zed to reject them with "Failed to get session"
        # because Zed only registers the session after it processes the response.
        # By deferring with asyncio.sleep(0) we let the event loop flush the
        # response to the socket before sending notifications.
        if self._conn:
            asyncio.create_task(
                self._send_session_open_notifications(session_id, session_model, cwd)
            )

        _NewSessionResponse = _check_acp_import(
            NewSessionResponse, "NewSessionResponse"
        )

        # Build modes and models state for the session response.
        modes = self._build_modes_state(session_id)
        models = self._build_models_state(session_model)

        return _NewSessionResponse(
            session_id=session_id,
            modes=modes,
            models=models,
        )

    def _build_modes_state(self, session_id: str) -> Any:
        """Build SessionModeState for the session response.

        gptme supports two modes:
        - default: Interactive mode (tools require confirmation)
        - auto: Autonomous mode (tools run without confirmation, like --no-confirm)
        """
        try:
            from acp.schema import (  # type: ignore[import-not-found]
                SessionMode,
                SessionModeState,
            )
        except ImportError:
            return None

        current_mode = self._session_modes.get(session_id, "default")
        return SessionModeState(
            available_modes=[
                SessionMode(
                    id="default",
                    name="Default",
                    description="Interactive mode — tools require confirmation before executing",
                ),
                SessionMode(
                    id="auto",
                    name="Auto",
                    description="Autonomous mode — tools run without confirmation",
                ),
            ],
            current_mode_id=current_mode,
        )

    def _build_models_state(self, session_model: str | None) -> Any:
        """Build SessionModelState from gptme's model registry."""
        try:
            from acp.schema import (  # type: ignore[import-not-found]
                ModelInfo,
                SessionModelState,
            )
        except ImportError:
            return None

        from ..llm.models import MODELS

        available: list[Any] = []
        for provider, models_dict in MODELS.items():
            for model_name, meta in models_dict.items():
                if meta.get("deprecated", False):
                    continue
                model_id = f"{provider}/{model_name}"
                available.append(
                    ModelInfo(
                        model_id=model_id,
                        name=model_name,
                        description=f"{provider} — context: {meta['context']:,} tokens",
                    )
                )

        current = session_model or (self._model or "default")
        return SessionModelState(
            available_models=available,
            current_model_id=current,
        )

    async def _send_session_open_notifications(
        self, session_id: str, session_model: str | None, cwd: str
    ) -> None:
        """Send session-open notifications after NewSessionResponse is returned.

        Must run as an asyncio.create_task() so it executes after the current
        coroutine (new_session) returns. This gives Zed time to process
        NewSessionResponse and register the session before we send notifications
        that reference the session_id.
        """
        # Yield to the event loop so new_session() can return and the response
        # can be flushed to the socket before we send notifications.
        await asyncio.sleep(0)

        if not self._conn:
            return

        try:
            from acp import (  # type: ignore[import-not-found]
                text_block,
                update_agent_message,
            )
        except ImportError:
            logger.debug("acp not installed, skipping session-open notifications")
            await self._send_available_commands(session_id)
            return

        # Surface model and workspace info immediately in the ACP panel.
        model_info = session_model or "default"
        workspace_info = str(cwd) if cwd else "none"
        info_text = f"ℹ️ Using model: {model_info}\nUsing workspace: {workspace_info}"
        info_chunk = update_agent_message(text_block(info_text))
        try:
            await self._conn.session_update(
                session_id=session_id,
                update=info_chunk,
                source="gptme",
            )
        except Exception as e:
            logger.debug(f"Failed to send session info notification: {e}")

        # Surface initialization errors immediately so the user sees them
        # without having to send a prompt.
        if self._init_error:
            error_chunk = update_agent_message(text_block(f"⚠️ {self._init_error}"))
            try:
                await self._conn.session_update(
                    session_id=session_id,
                    update=error_chunk,
                    source="gptme",
                )
            except Exception as e:
                logger.debug(f"Failed to send init error notification: {e}")
            # Keep _init_error set so prompt() can still surface it for retries

        # Advertise slash commands for client-side autocomplete.
        await self._send_available_commands(session_id)

    async def _send_available_commands(self, session_id: str) -> None:
        """Send AvailableCommandsUpdate notification to advertise slash commands.

        Called from _send_session_open_notifications() (deferred, after session
        registration) and as a fallback at the start of the first prompt() call.
        """
        if not self._conn or session_id in self._session_commands_advertised:
            return
        try:
            from acp.helpers import (  # type: ignore[import-not-found]
                update_available_commands,
            )
            from acp.schema import (  # type: ignore[import-not-found]
                AvailableCommand,
            )

            from ..commands import get_commands_with_descriptions

            acp_commands = [
                AvailableCommand(name=name, description=desc)
                for name, desc in get_commands_with_descriptions()
            ]
            await self._conn.session_update(
                session_id=session_id,
                update=update_available_commands(acp_commands),
                source="gptme",
            )
            self._session_commands_advertised.add(session_id)
            logger.info(
                f"ACP AvailableCommandsUpdate: sent {len(acp_commands)} commands"
                f" for session {session_id[:8]}"
            )
        except Exception as e:
            # Non-fatal: clients still work without autocomplete
            logger.warning(f"Failed to send available commands: {e}", exc_info=True)

    async def _handle_slash_command(
        self,
        msg: Message,
        log: LogManager,
        session_id: str,
        text_block: Any,
        update_agent_message: Any,
    ) -> Any:
        """Execute a slash command and stream the output back to the ACP client.

        Some commands are unsafe in an ACP context (e.g. /exit would kill the server,
        /restart would restart the process). These are blocked and a helpful message is
        returned instead.

        Args:
            msg: The user message containing the slash command
            log: The session's LogManager
            session_id: The ACP session ID
            text_block: ACP text_block factory
            update_agent_message: ACP update_agent_message factory

        Returns:
            PromptResponse with appropriate stop_reason
        """
        import contextlib
        import io

        from ..commands import handle_cmd

        cmd_name = msg.content.lstrip("/").split()[0]

        # Block commands that are unsafe in server/ACP context
        acp_blocked_commands = {"exit", "restart"}
        if cmd_name in acp_blocked_commands:
            output = f"The /{cmd_name} command is not available in ACP mode."
            error_chunk = update_agent_message(text_block(output))
            if self._conn:
                await self._conn.session_update(
                    session_id=session_id,
                    update=error_chunk,
                    source="gptme",
                )
            _PromptResponse = _check_acp_import(PromptResponse, "PromptResponse")
            return _PromptResponse(stop_reason="end_turn")

        # Capture stdout from commands that use print() (e.g. /help, /tools)
        captured = io.StringIO()
        response_msgs: list[Message] = []
        try:
            with contextlib.redirect_stdout(captured):
                for resp in handle_cmd(msg.content, log):
                    log.append(resp)
                    response_msgs.append(resp)
        except Exception as e:
            logger.exception(f"Error executing slash command {msg.content!r}: {e}")
            error_text = f"Error executing command: {e}"
            error_chunk = update_agent_message(text_block(error_text))
            if self._conn:
                await self._conn.session_update(
                    session_id=session_id,
                    update=error_chunk,
                    source="gptme",
                )
            _PromptResponse = _check_acp_import(PromptResponse, "PromptResponse")
            return _PromptResponse(stop_reason="cancelled")

        # Combine captured stdout with any yielded system messages
        output_parts: list[str] = []
        stdout_output = captured.getvalue()
        if stdout_output:
            output_parts.append(stdout_output.rstrip())
        output_parts.extend(
            resp_msg.content for resp_msg in response_msgs if resp_msg.content
        )

        output = "\n".join(output_parts) if output_parts else f"/{cmd_name}: done"

        chunk = update_agent_message(text_block(output))
        if self._conn:
            await self._conn.session_update(
                session_id=session_id,
                update=chunk,
                source="gptme",
            )

        _PromptResponse = _check_acp_import(PromptResponse, "PromptResponse")
        return _PromptResponse(stop_reason="end_turn")

    async def prompt(
        self,
        prompt: list[Any],
        session_id: str,
        **kwargs: Any,
    ) -> Any:
        """Handle a prompt from the client.

        This is the main interaction method. It:
        1. Converts ACP prompt to gptme messages
        2. Runs through gptme's chat loop
        3. Streams responses back via session/update

        Args:
            prompt: List of ACP content blocks
            session_id: Session ID from new_session

        Returns:
            PromptResponse with stop reason
        """
        if not _import_acp():
            raise RuntimeError(
                "agent-client-protocol package not installed. "
                "Install with: pip install 'gptme[acp]'"
            )

        from acp import (  # type: ignore[import-not-found]
            text_block,
            update_agent_message,
        )

        # Surface initialization errors as visible agent messages
        if self._init_error:
            error_chunk = update_agent_message(text_block(f"⚠️ {self._init_error}"))
            if self._conn:
                await self._conn.session_update(
                    session_id=session_id,
                    update=error_chunk,
                    source="gptme",
                )
            # Clear the error so the user can retry after fixing their config
            self._init_error = None
            _PromptResponse = _check_acp_import(PromptResponse, "PromptResponse")
            return _PromptResponse(stop_reason="cancelled")

        # Re-set ContextVars that may be missing in this task's context.
        # ACP framework may dispatch each RPC method in a separate asyncio task,
        # so ContextVars set during initialize() aren't visible here.
        # Use per-session model if available (set from per-project gptme.toml in new_session).
        effective_model = self._session_models.get(session_id, self._model)
        if effective_model:
            set_default_model(effective_model)
        elif self._model:
            set_default_model(self._model)
        if self._tools is not None:
            set_tools(self._tools)

        # Resend AvailableCommandsUpdate if not yet sent for this session.
        # Handles the case where the deferred task from new_session() hasn't run yet
        # (e.g., early first prompt arriving before asyncio.sleep(0) yields).
        await self._send_available_commands(session_id)

        session = self._registry.get(session_id)
        if not session:
            logger.error(f"Unknown session: {session_id}")
            _PromptResponse = _check_acp_import(PromptResponse, "PromptResponse")
            return _PromptResponse(stop_reason="cancelled")
        # Update last_activity timestamp for cleanup tracking
        session.touch()
        log = session.log
        if log is None:
            raise RuntimeError("ACP sessions must have a log")

        # Convert ACP prompt to gptme message
        msg = acp_content_to_gptme_message(prompt, "user")
        log.append(msg)

        content_preview = msg.content[:100] if msg.content else ""
        logger.info(
            f"ACP Prompt: session={session_id[:8]}, content={content_preview}..."
        )

        # Handle slash commands before passing to the LLM.
        # Commands like /help, /model, /tools are handled locally and their output
        # is streamed back to the ACP client as a system message.
        from ..util.content import is_message_command  # fmt: skip

        if is_message_command(msg.content):
            return await self._handle_slash_command(
                msg, log, session_id, text_block, update_agent_message
            )

        try:
            # Import chat step
            from ..chat import step as chat_step

            # Run gptme chat step in executor to not block event loop
            loop = asyncio.get_running_loop()

            def run_chat_step() -> list[Message]:
                """Run chat step synchronously."""
                # Note: confirmation is now handled via the hook system
                return list(
                    chat_step(
                        log=log.log,
                        stream=False,
                        tool_format="markdown",
                        model=effective_model,
                    )
                )

            # Copy context to propagate ContextVars (model, config, tools, etc.)
            # to the executor thread — run_in_executor doesn't do this by default
            ctx = contextvars.copy_context()
            response_msgs = await loop.run_in_executor(None, ctx.run, run_chat_step)

            # Phase 2: Mark all in-progress tool calls as completed
            await self._complete_pending_tool_calls(session_id)

            # Stream each response message back
            for response_msg in response_msgs:
                if response_msg.role == "assistant":
                    content = gptme_message_to_acp_content(response_msg)
                    for block in content:
                        text = block.get("text", "")
                        if text and self._conn:
                            chunk = update_agent_message(text_block(text))
                            await self._conn.session_update(
                                session_id=session_id,
                                update=chunk,
                                source="gptme",
                            )
                    # Also add to log
                    log.append(response_msg)

            _PromptResponse = _check_acp_import(PromptResponse, "PromptResponse")
            return _PromptResponse(stop_reason="end_turn")

        except Exception as e:
            logger.exception(f"Error processing prompt: {e}")
            # Phase 2: Mark tool calls as failed on error
            await self._complete_pending_tool_calls(session_id, success=False)
            # Send error message
            error_chunk = update_agent_message(text_block(f"Error: {e}"))
            if self._conn:
                await self._conn.session_update(
                    session_id=session_id,
                    update=error_chunk,
                    source="gptme",
                )
            _PromptResponse = _check_acp_import(PromptResponse, "PromptResponse")
            return _PromptResponse(stop_reason="cancelled")

    async def load_session(
        self,
        session_id: str,
        **kwargs: Any,
    ) -> Any:
        """Load an existing session from persistent log storage.

        Looks up the session by conversation ID in the gptme logs directory.
        If found, restores the LogManager and registers the session.
        Returns None if the session doesn't exist on disk, letting the client
        gracefully fall back to creating a new session via new_session().

        Args:
            session_id: Conversation ID (e.g. "2025-08-30-jumping-orange-walrus")

        Returns:
            NewSessionResponse if session found, None otherwise
        """
        if not _import_acp():
            return None

        # Check if already loaded in-memory
        if self._registry.get(session_id):
            logger.info(f"load_session: {session_id[:16]} already in registry")
            _NewSessionResponse = _check_acp_import(
                NewSessionResponse, "NewSessionResponse"
            )
            return _NewSessionResponse(session_id=session_id)

        # Try to load from persistent storage
        logs_dir = get_logs_dir()
        logdir = logs_dir / session_id
        logfile = logdir / "conversation.jsonl"

        if not logfile.exists():
            logger.info(f"load_session: {session_id[:16]} not found on disk")
            return None

        try:
            log = LogManager.load(
                logdir=logdir,
                create=False,
                lock=False,
            )
            self._registry.create(session_id, log=log)
            logger.info(
                f"load_session: restored {session_id[:16]} ({len(log.log)} messages)"
            )

            _NewSessionResponse = _check_acp_import(
                NewSessionResponse, "NewSessionResponse"
            )
            return _NewSessionResponse(session_id=session_id)
        except Exception as e:
            logger.warning(f"load_session: failed to load {session_id[:16]}: {e}")
            return None

    def _cleanup_session(self, session_id: str) -> None:
        """Remove all per-session state for a given session.

        Cleans up _session_models, _session_modes, _tool_calls, and
        _permission_policies to prevent unbounded memory growth from
        accumulated sessions.
        """
        self._session_models.pop(session_id, None)
        self._session_modes.pop(session_id, None)
        self._tool_calls.pop(session_id, None)
        self._permission_policies.pop(session_id, None)
        self._session_commands_advertised.discard(session_id)
        self._registry.remove(session_id)

    async def cancel(
        self,
        session_id: str,
        **kwargs: Any,
    ) -> None:
        """Cancel an ongoing operation and clean up session state.

        Args:
            session_id: Session to cancel
        """
        logger.info(f"Cancelling session {session_id}")
        self._cleanup_session(session_id)

    async def list_sessions(
        self,
        cursor: str | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """List available sessions from persistent storage.

        Returns sessions from disk (gptme logs directory), merged with any
        active in-memory sessions. Sessions are sorted by modification time
        (most recent first).
        """
        if not _import_acp():
            raise RuntimeError("agent-client-protocol package not installed")
        from acp.client.connection import (  # type: ignore[import-not-found]
            ListSessionsResponse,
        )
        from acp.schema import SessionInfo  # type: ignore[import-not-found]

        # Get persistent conversations from disk
        conversations = list_conversations(limit=50)

        # Build session list, merging disk metadata with in-memory state
        active_ids = set(self._registry.list_sessions())
        sessions: list[Any] = []

        for conv in conversations:
            # Prefer in-memory cwd (set at session creation) over disk workspace
            in_memory = self._registry.get(conv.id)
            session_cwd = (
                (in_memory.cwd or "")
                if in_memory and in_memory.cwd
                else (conv.workspace if conv.workspace != "." else "")
            )

            # Apply cwd filter if specified
            if cwd and session_cwd != cwd:
                active_ids.discard(conv.id)
                continue

            sessions.append(
                SessionInfo(
                    session_id=conv.id,
                    cwd=session_cwd,
                    title=conv.name if conv.name != conv.id else None,
                    updated_at=datetime.fromtimestamp(
                        conv.modified, tz=timezone.utc
                    ).isoformat(),
                )
            )
            active_ids.discard(conv.id)

        # Add any in-memory sessions not yet on disk
        for sid in active_ids:
            in_memory = self._registry.get(sid)
            session_cwd = (in_memory.cwd or "") if in_memory else ""
            if cwd and session_cwd != cwd:
                continue
            sessions.append(
                SessionInfo(
                    session_id=sid,
                    cwd=session_cwd,
                    updated_at=(
                        in_memory.last_activity.isoformat() if in_memory else None
                    ),
                )
            )

        return ListSessionsResponse(sessions=sessions)

    async def authenticate(
        self,
        method_id: str,
        **kwargs: Any,
    ) -> None:
        """Handle authentication request (not supported)."""
        logger.warning(f"authenticate not implemented: {method_id}")
        return

    async def set_session_model(
        self,
        model_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> None:
        """Set the model for a specific session."""
        logger.info(f"set_session_model: session={session_id}, model={model_id}")
        self._session_models[session_id] = model_id
        return

    async def set_session_mode(
        self,
        mode_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> None:
        """Set the mode for a session.

        Supported modes:
        - default: Interactive mode (tools require confirmation)
        - auto: Autonomous mode (tools run without confirmation)
        """
        valid_modes = {"default", "auto"}
        if mode_id not in valid_modes:
            logger.warning(
                f"set_session_mode: unknown mode {mode_id!r}, ignoring. "
                f"Valid modes: {valid_modes}"
            )
            return
        logger.info(f"set_session_mode: session={session_id}, mode={mode_id}")
        self._session_modes[session_id] = mode_id

    async def ext_method(
        self,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle extension method calls."""
        logger.warning(f"ext_method not implemented: {method}")
        return {}

    async def ext_notification(
        self,
        method: str,
        params: dict[str, Any],
    ) -> None:
        """Handle extension notifications."""
        logger.debug(f"ext_notification: {method}")


def create_agent() -> GptmeAgent:
    """Create a new GptmeAgent instance.

    Returns:
        Configured GptmeAgent
    """
    return GptmeAgent()
