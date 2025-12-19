"""ACP Agent implementation for gptme.

This module implements the Agent Client Protocol, allowing gptme to be used
as a coding agent from any ACP-compatible editor (Zed, JetBrains, etc.).
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ..init import init
from ..logmanager import LogManager
from ..message import Message
from ..prompts import get_prompt
from ..session import SessionRegistry
from ..tools import get_tools
from .adapter import acp_content_to_gptme_message, gptme_message_to_acp_content
from .types import (
    PermissionKind,
    PermissionOption,
    ToolCall,
    ToolCallStatus,
    gptme_tool_to_acp_kind,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Lazy imports to avoid dependency issues when acp is not installed
Agent: type | None = None
InitializeResponse: type | None = None
NewSessionResponse: type | None = None
PromptResponse: type | None = None
Client: type | None = None


def _import_acp() -> bool:
    """Import ACP modules lazily."""
    global Agent, InitializeResponse, NewSessionResponse, PromptResponse, Client
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

        Agent = _Agent
        InitializeResponse = _InitializeResponse
        NewSessionResponse = _NewSessionResponse
        PromptResponse = _PromptResponse
        Client = _Client
        return True
    except ImportError:
        return False


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
        self._model: str = "anthropic/claude-sonnet-4-20250514"
        # Phase 2: Track active tool calls per session
        self._tool_calls: dict[str, dict[str, ToolCall]] = {}
        # Phase 2: Permission policies per session (allow_always, reject_always)
        self._permission_policies: dict[str, dict[str, str]] = {}

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

            outcome = result.get("outcome", {})
            if outcome.get("outcome") == "cancelled":
                return False

            option_id = outcome.get("optionId", "")

            # Cache always policies
            if option_id == "allow-always":
                if session_id not in self._permission_policies:
                    self._permission_policies[session_id] = {}
                self._permission_policies[session_id][tool_call.kind.value] = "allow"
                return True
            elif option_id == "reject-always":
                if session_id not in self._permission_policies:
                    self._permission_policies[session_id] = {}
                self._permission_policies[session_id][tool_call.kind.value] = "reject"
                return False
            elif option_id == "allow-once":
                return True
            else:
                return False

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
        if not self._initialized:
            init(
                model=self._model,
                interactive=False,
                tool_allowlist=None,
                tool_format="markdown",
            )
            self._initialized = True

        logger.info(f"ACP Initialize: protocol_version={protocol_version}")
        assert InitializeResponse is not None
        return InitializeResponse(protocol_version=protocol_version)

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

        session_id = uuid4().hex

        # Create a temporary directory for the log
        logdir = Path(tempfile.mkdtemp(prefix=f"gptme-acp-{session_id[:8]}-"))

        # Get tools and initial prompt
        tools = get_tools()
        initial_msgs = get_prompt(
            tools=tools,
            tool_format="markdown",
            prompt="full",
            interactive=False,
            model=self._model,
            workspace=Path(cwd) if cwd else None,
        )

        # Create LogManager for this session
        log = LogManager.load(
            logdir=logdir,
            initial_msgs=initial_msgs,
            create=True,
            lock=False,
        )

        self._registry.create(session_id, log=log)
        logger.info(f"ACP NewSession: session_id={session_id}, cwd={cwd}")

        assert NewSessionResponse is not None
        return NewSessionResponse(session_id=session_id)

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

        session = self._registry.get(session_id)
        if not session:
            logger.error(f"Unknown session: {session_id}")
            assert PromptResponse is not None
            return PromptResponse(stop_reason="error")
        # Update last_activity timestamp for cleanup tracking
        session.touch()
        log = session.log
        assert log is not None, "ACP sessions must have a log"

        # Convert ACP prompt to gptme message
        msg = acp_content_to_gptme_message(prompt, "user")
        log.append(msg)

        content_preview = msg.content[:100] if msg.content else ""
        logger.info(
            f"ACP Prompt: session={session_id[:8]}, content={content_preview}..."
        )

        try:
            # Import chat step
            from ..chat import step as chat_step

            # Run gptme chat step in executor to not block event loop
            loop = asyncio.get_running_loop()

            # Phase 2: Create confirm callback that reports tool calls
            confirm_callback = self._create_confirm_with_tools(session_id, loop)

            def run_chat_step() -> list[Message]:
                """Run chat step synchronously."""
                return list(
                    chat_step(
                        log=log.log,
                        stream=False,
                        confirm=confirm_callback,  # Phase 2: Tool call reporting
                        tool_format="markdown",
                        model=self._model,
                    )
                )

            response_msgs = await loop.run_in_executor(None, run_chat_step)

            # Phase 2: Mark all in-progress tool calls as completed
            await self._complete_pending_tool_calls(session_id)

            # Stream each response message back
            for response_msg in response_msgs:
                if response_msg.role == "assistant":
                    content = gptme_message_to_acp_content(response_msg)
                    for block in content:
                        text = block.get("text", "")
                        if text:
                            chunk = update_agent_message(text_block(text))
                            await self._conn.session_update(
                                session_id=session_id,
                                update=chunk,
                                source="gptme",
                            )
                    # Also add to log
                    log.append(response_msg)

            assert PromptResponse is not None
            return PromptResponse(stop_reason="end_turn")

        except Exception as e:
            logger.exception(f"Error processing prompt: {e}")
            # Phase 2: Mark tool calls as failed on error
            await self._complete_pending_tool_calls(session_id, success=False)
            # Send error message
            error_chunk = update_agent_message(text_block(f"Error: {e}"))
            await self._conn.session_update(
                session_id=session_id,
                update=error_chunk,
                source="gptme",
            )
            assert PromptResponse is not None
            return PromptResponse(stop_reason="error")

    async def load_session(
        self,
        session_id: str,
        **kwargs: Any,
    ) -> Any:
        """Load an existing session (Phase 2 feature).

        Args:
            session_id: Session ID to load

        Returns:
            Session data or error
        """
        # Phase 2: Implement session persistence
        logger.warning(f"load_session not yet implemented: {session_id}")
        raise NotImplementedError("Session loading not yet implemented")

    async def cancel(
        self,
        session_id: str,
        **kwargs: Any,
    ) -> None:
        """Cancel an ongoing operation (Phase 2 feature).

        Args:
            session_id: Session to cancel
        """
        # Phase 2: Implement cancellation
        logger.warning(f"cancel not yet implemented: {session_id}")


def create_agent() -> GptmeAgent:
    """Create a new GptmeAgent instance.

    Returns:
        Configured GptmeAgent
    """
    return GptmeAgent()
