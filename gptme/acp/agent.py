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
from ..tools import get_tools
from .adapter import acp_content_to_gptme_message, gptme_message_to_acp_content

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
        from acp import (
            Agent as _Agent,
        )
        from acp import (
            InitializeResponse as _InitializeResponse,
        )
        from acp import (
            NewSessionResponse as _NewSessionResponse,
        )
        from acp import (
            PromptResponse as _PromptResponse,
        )
        from acp.interfaces import Client as _Client

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
        self._sessions: dict[str, LogManager] = {}
        self._initialized = False
        self._model: str = "anthropic/claude-sonnet-4-20250514"

    def on_connect(self, conn: Any) -> None:
        """Called when a client connects.

        Args:
            conn: The client connection for sending notifications.
        """
        self._conn = conn

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
            raise RuntimeError("agent-client-protocol package not installed")

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
            raise RuntimeError("agent-client-protocol package not installed")

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

        self._sessions[session_id] = log
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
            raise RuntimeError("agent-client-protocol package not installed")

        from acp import text_block, update_agent_message

        log = self._sessions.get(session_id)
        if not log:
            logger.error(f"Unknown session: {session_id}")
            assert PromptResponse is not None
            return PromptResponse(stop_reason="error")

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

            def run_chat_step() -> list[Message]:
                """Run chat step synchronously."""
                return list(
                    chat_step(
                        log=log.log,
                        stream=False,
                        confirm=lambda _: True,  # Auto-confirm for Phase 1
                        tool_format="markdown",
                        model=self._model,
                    )
                )

            response_msgs = await loop.run_in_executor(None, run_chat_step)

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
