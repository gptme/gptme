"""
Common types and utilities for V2 API.
"""

from pathlib import Path
from typing import Literal, TypedDict

from ..message import Message
from .api import _abs_to_rel_workspace


class MessageDict(TypedDict):
    """Message dictionary type."""

    role: str
    content: str
    timestamp: str
    files: list[str] | None


class ToolUseDict(TypedDict):
    """Tool use dictionary type."""

    tool: str
    args: list[str] | None
    content: str | None


# Event Type Definitions
# ---------------------


class BaseEvent(TypedDict):
    """Base event type with common fields."""

    type: Literal[
        "connected",
        "ping",
        "message_added",
        "generation_started",
        "generation_progress",
        "generation_complete",
        "tool_pending",
        "tool_executing",
        "interrupted",
        "error",
        "config_changed",
    ]


class ConnectedEvent(BaseEvent):
    """Sent when a client connects to the event stream."""

    session_id: str


class PingEvent(BaseEvent):
    """Periodic ping to keep connection alive."""


class MessageAddedEvent(BaseEvent):
    """
    Sent when a new message is added to the conversation, such as when a tool has output to display.

    Not used for streaming generated messages.
    """

    message: MessageDict


class GenerationStartedEvent(BaseEvent):
    """Sent when generation starts."""


class GenerationProgressEvent(BaseEvent):
    """Sent for each token during generation."""

    token: str


class GenerationCompleteEvent(BaseEvent):
    """Sent when generation is complete."""

    message: MessageDict


class ToolPendingEvent(BaseEvent):
    """Sent when a tool is detected and waiting for confirmation."""

    tool_id: str
    tooluse: ToolUseDict
    auto_confirm: bool


class ToolExecutingEvent(BaseEvent):
    """Sent when a tool is being executed."""

    tool_id: str


class InterruptedEvent(BaseEvent):
    """Sent when generation is interrupted."""


class ErrorEvent(BaseEvent):
    """Sent when an error occurs."""

    error: str


class ConfigChangedEvent(BaseEvent):
    """Sent when the conversation config is updated."""

    config: dict
    changed_fields: list[str]


# Union type for all possible events
EventType = (
    ConnectedEvent
    | PingEvent
    | MessageAddedEvent
    | GenerationStartedEvent
    | GenerationProgressEvent
    | GenerationCompleteEvent
    | ToolPendingEvent
    | ToolExecutingEvent
    | InterruptedEvent
    | ErrorEvent
    | ConfigChangedEvent
)


def msg2dict(msg: Message, workspace: Path) -> MessageDict:
    """Convert a Message object to a dictionary."""
    return {
        "role": msg.role,
        "content": msg.content,
        "timestamp": msg.timestamp.isoformat(),
        "files": [_abs_to_rel_workspace(f, workspace) for f in msg.files],
    }
