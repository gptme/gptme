"""gptme API v2 client for task queue integration.

Provides SessionManager integration for task execution via gptme server API.
"""

import json
import logging
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, cast

import requests

logger = logging.getLogger(__name__)


@dataclass
class ConversationEvent:
    """Event from conversation event stream."""

    type: str
    data: dict


class GptmeApiClient:
    """Client for gptme v2 API."""

    def __init__(
        self, base_url: str = "http://localhost:5000", auth_token: str | None = None
    ):
        """Initialize API client.

        Args:
            base_url: Base URL for gptme server
            auth_token: Optional authentication token
        """
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        if auth_token:
            self.session.headers["Authorization"] = f"Bearer {auth_token}"

    def create_session(self, conversation_id: str) -> str:
        """Create a new session for conversation via events endpoint.

        Args:
            conversation_id: The conversation ID

        Returns:
            session_id: The created session ID

        Raises:
            requests.HTTPError: If session creation fails
            ValueError: If session_id not received or is None
        """
        url = f"{self.base_url}/api/v2/conversations/{conversation_id}/events"

        logger.info(f"Creating session for conversation {conversation_id}")

        # Connect to events stream to create session
        response = self.session.get(url, stream=True, timeout=10)
        response.raise_for_status()

        try:
            # Read first event which contains session_id
            for line in response.iter_lines():
                if line and line.startswith(b"data:"):
                    data = json.loads(line[5:].strip())
                    if data.get("type") == "connected":
                        session_id = data.get("session_id")
                        if session_id is None:
                            raise ValueError(
                                "Received connected event with null session_id"
                            )
                        logger.info(f"Session created: {session_id}")
                        return cast(str, session_id)

            raise ValueError("Failed to get session_id from events stream")
        finally:
            # Always close connection to prevent resource leak
            response.close()

    def take_step(
        self,
        conversation_id: str,
        session_id: str,
        message: str | None = None,
        auto_confirm: bool = True,
        stream: bool = True,
    ) -> dict:
        """Take a step in the conversation.

        Args:
            conversation_id: The conversation ID
            session_id: The session ID
            message: Optional message to send (for initial prompt)
            auto_confirm: Whether to auto-confirm tool executions
            stream: Whether to enable streaming

        Returns:
            Response dict with status

        Raises:
            requests.HTTPError: If step fails
        """
        url = f"{self.base_url}/api/v2/conversations/{conversation_id}/step"

        payload = {
            "session_id": session_id,
            "auto_confirm": auto_confirm,
            "stream": stream,
        }

        if message:
            payload["message"] = message

        logger.info(f"Taking step for session {session_id}")

        response = self.session.post(url, json=payload, timeout=30)
        response.raise_for_status()

        return cast(dict[Any, Any], response.json())

    def stream_events(
        self, conversation_id: str, session_id: str
    ) -> Generator[ConversationEvent, None, None]:
        """Stream events from conversation.

        Args:
            conversation_id: The conversation ID
            session_id: The session ID

        Yields:
            ConversationEvent objects

        Raises:
            requests.HTTPError: If stream connection fails
        """
        url = f"{self.base_url}/api/v2/conversations/{conversation_id}/events?session_id={session_id}"

        logger.info(f"Streaming events for session {session_id}")

        response = self.session.get(url, stream=True, timeout=None)
        response.raise_for_status()

        try:
            for line in response.iter_lines():
                if line and line.startswith(b"data:"):
                    try:
                        data = json.loads(line[5:].strip())
                        yield ConversationEvent(
                            type=data.get("type", "unknown"), data=data
                        )
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse event: {e}")
                        continue
        finally:
            response.close()

    def interrupt(self, conversation_id: str, session_id: str) -> dict:
        """Interrupt the current generation or tool execution.

        Args:
            conversation_id: The conversation ID
            session_id: The session ID

        Returns:
            Response dict with status

        Raises:
            requests.HTTPError: If interrupt fails
        """
        url = f"{self.base_url}/api/v2/conversations/{conversation_id}/interrupt"

        payload = {"session_id": session_id}

        logger.info(f"Interrupting session {session_id}")

        response = self.session.post(url, json=payload, timeout=10)
        response.raise_for_status()

        return cast(dict[Any, Any], response.json())

    def confirm_tool(
        self,
        conversation_id: str,
        session_id: str,
        tool_id: str,
        action: str = "confirm",
        content: str | None = None,
        auto_continue: bool = False,
    ) -> dict:
        """Confirm, edit, or skip a pending tool execution.

        Args:
            conversation_id: The conversation ID
            session_id: The session ID
            tool_id: The tool execution ID
            action: Action to take (confirm, edit, skip, auto_confirm)
            content: Updated content if action=edit
            auto_continue: Whether to auto-continue after confirmation

        Returns:
            Response dict with status

        Raises:
            requests.HTTPError: If confirmation fails
        """
        url = f"{self.base_url}/api/v2/conversations/{conversation_id}/tool/confirm"

        payload = {
            "session_id": session_id,
            "tool_id": tool_id,
            "action": action,
            "auto_continue": auto_continue,
        }

        if content:
            payload["content"] = content

        logger.info(f"Confirming tool {tool_id} with action {action}")

        response = self.session.post(url, json=payload, timeout=10)
        response.raise_for_status()

        return cast(dict[Any, Any], response.json())

    def execute_conversation(
        self,
        conversation_id: str,
        prompt: str,
        auto_confirm: bool = True,
    ) -> tuple[bool, str | None]:
        """Execute a complete conversation from start to finish.

        Convenience method that creates session, executes prompt, and streams to completion.

        Args:
            conversation_id: The conversation ID
            prompt: The prompt to execute
            auto_confirm: Whether to auto-confirm tool executions

        Returns:
            (success, error_message): Success status and optional error message
        """
        try:
            # Create session
            logger.info(f"Creating session for conversation {conversation_id}")
            session_id = self.create_session(conversation_id)

            # Take initial step
            logger.info(f"Taking step with prompt: {prompt[:50]}...")
            self.take_step(
                conversation_id=conversation_id,
                session_id=session_id,
                message=prompt,
                auto_confirm=auto_confirm,
                stream=True,
            )

            # Stream events to completion
            logger.info("Streaming events to completion")
            for event in self.stream_events(conversation_id, session_id):
                if event.type == "generation_complete":
                    logger.info("Generation complete - success")
                    return (True, None)
                elif event.type == "error":
                    error_msg = event.data.get("error", "Unknown error")
                    logger.error(f"Conversation error: {error_msg}")
                    return (False, error_msg)

            # If we exit loop without completion or error, treat as incomplete
            logger.warning("Event stream ended without completion signal")
            return (False, "Incomplete: Event stream ended unexpectedly")

        except Exception as e:
            logger.exception("Exception during conversation execution")
            return (False, str(e))
