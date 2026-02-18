"""
Common types and utilities for V2 API.
"""

from pathlib import Path
from typing import Literal, TypedDict

from typing_extensions import NotRequired

from ..message import Message
from .api import _abs_to_rel_workspace


class MessageDict(TypedDict):
    """Message dictionary type."""

    role: str
    content: str
    timestamp: str
    files: NotRequired[list[str] | None]
    hide: NotRequired[bool]


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
        "elicit_pending",
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


class FormFieldDict(TypedDict):
    """Form field dictionary type for elicitation."""

    name: str
    prompt: str
    type: str
    options: NotRequired[list[str] | None]
    required: NotRequired[bool]
    default: NotRequired[str | None]


class ElicitPendingEvent(BaseEvent):
    """Sent when the agent requests structured user input (elicitation).

    Clients should display an appropriate input UI based on ``elicit_type``:
    - ``text``: Free-form text input
    - ``choice``: Single selection from ``options``
    - ``multi_choice``: Multiple selection from ``options``
    - ``secret``: Hidden input (password field)
    - ``confirmation``: Yes/no question
    - ``form``: Multiple fields (described in ``fields``)
    """

    elicit_id: str
    elicit_type: str
    prompt: str
    options: NotRequired[list[str]]
    fields: NotRequired[list[FormFieldDict]]
    default: NotRequired[str]
    description: NotRequired[str]


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
    | ElicitPendingEvent
    | InterruptedEvent
    | ErrorEvent
    | ConfigChangedEvent
)


def msg2dict(msg: Message, workspace: Path) -> MessageDict:
    """Convert a Message object to a dictionary."""
    result: MessageDict = {
        "role": msg.role,
        "content": msg.content,
        "timestamp": msg.timestamp.isoformat(),
        "files": [_abs_to_rel_workspace(f, workspace) for f in msg.files],
    }
    if msg.hide:
        result["hide"] = True
    return result
