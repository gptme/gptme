"""
Common types and utilities for the gptme server API.
"""

import os
from pathlib import Path
from typing import Literal, TypedDict

from typing_extensions import NotRequired

from ..message import Message
from ..util.uri import URI, is_uri


def _is_debug_errors_enabled() -> bool:
    """Check if detailed error messages should be shown.

    When GPTME_DEBUG_ERRORS is set to '1', 'true', or 'yes' (case-insensitive),
    detailed error messages with exception information will be returned to clients.
    This is useful for development, testing, CI, and staging environments.

    In production, this should be disabled (default) to prevent information leakage.
    """
    return os.environ.get("GPTME_DEBUG_ERRORS", "").lower() in ("1", "true", "yes")


def _abs_to_rel_workspace(
    path: str | Path | URI, workspace: Path, logdir: Path | None = None
) -> str:
    """Convert an absolute path to a relative path.

    URIs are returned as-is since they are not workspace-relative.
    Files under workspace are returned relative to workspace.
    Files under logdir (e.g. attachments/) are returned relative to logdir.
    """
    # URIs should be returned as-is (they're not workspace-relative)
    if isinstance(path, URI) or (isinstance(path, str) and is_uri(path)):
        return str(path)

    path = Path(path).resolve()
    if path.is_relative_to(workspace):
        return str(path.relative_to(workspace))
    # For files outside workspace (e.g. logdir/attachments/), normalize against logdir
    if logdir is not None and path.is_relative_to(logdir):
        return str(path.relative_to(logdir))
    return str(path)


class MessageDict(TypedDict):
    """Message dictionary type."""

    role: str
    content: str
    timestamp: str
    files: NotRequired[list[str] | None]
    hide: NotRequired[bool]
    metadata: NotRequired[dict]


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
        "conversation_edited",
    ]


class ConnectedEvent(BaseEvent):
    """Sent when a client connects to the event stream."""

    session_id: str
    generating: NotRequired[bool]
    pending_tools: NotRequired[list]


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


class ConversationEditedEvent(BaseEvent):
    """Sent when a message is edited (and optionally truncated)."""

    index: int
    truncated: bool
    log: list
    branches: dict


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
    | ConversationEditedEvent
)


def msg2dict(msg: Message, workspace: Path, logdir: Path | None = None) -> MessageDict:
    """Convert a Message object to a dictionary."""
    result: MessageDict = {
        "role": msg.role,
        "content": msg.content,
        "timestamp": msg.timestamp.isoformat(),
    }
    if msg.files:
        result["files"] = [
            _abs_to_rel_workspace(f, workspace, logdir) for f in msg.files
        ]
    if msg.hide:
        result["hide"] = True
    if msg.metadata:
        result["metadata"] = dict(msg.metadata)
    return result
