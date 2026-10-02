"""
Common types and utilities for the gptme server API.
"""

import os
from pathlib import Path
from typing import Literal, TypedDict

import flask
from typing_extensions import NotRequired

from ..message import Message
from ..util.conversation_ids import conversation_id_error
from ..util.uri import URI, is_uri

_MAX_ID_LENGTH = 255  # Linux NAME_MAX: 255 UTF-8 bytes for a single path component
_BRANCH_SUFFIX_LEN = len(
    ".jsonl"
)  # branches/{name}.jsonl on disk — suffix counts against NAME_MAX


def _validate_conversation_id(
    conversation_id: str,
) -> tuple[flask.Response, int] | None:
    """Validate conversation_id to prevent path traversal attacks and OS errors.

    Returns None if valid, or (error_response, status_code) if invalid.

    The shared validator owns the exact filesystem-safety rules. This wrapper
    keeps the server API's historical response strings stable.
    """
    if error := conversation_id_error(conversation_id):
        if "too long" in error:
            return flask.jsonify({"error": "conversation_id too long"}), 400
        return flask.jsonify({"error": "Invalid conversation_id"}), 400
    return None


def _is_tool_file_entry(item: str) -> bool:
    """Mirror init_tools(): allowlist items that are file paths, not tool names."""
    return item.endswith(".py") or "/" in item or "\\" in item


def _server_tool_allowlist() -> list[str] | None:
    """The server's ``--tools`` allowlist, or None when unrestricted."""
    return flask.current_app.config.get("SERVER_TOOL_ALLOWLIST")


def _tools_unset(tools: list[str] | None) -> bool:
    """Whether a conversation's tools still need a default.

    On a restricted server only ``None`` means "omitted", so an explicit ``[]``
    stays "no tools"; unrestricted servers keep treating ``[]`` as omitted.
    """
    if _server_tool_allowlist() is None:
        return not tools
    return tools is None


def _default_conversation_tools() -> list[str]:
    """Tool names a new conversation gets when the client doesn't pick any.

    Honors the server's ``--tools`` allowlist (``SERVER_TOOL_ALLOWLIST``);
    without one, every available non-MCP tool. File-path entries from the
    allowlist are kept verbatim (get_toolchain() skips them).
    """
    from ..tools import get_toolchain

    allowlist = _server_tool_allowlist()
    names = [t.name for t in get_toolchain(allowlist, strict=False) if not t.is_mcp]
    if allowlist:
        names += [a for a in allowlist if _is_tool_file_entry(a) and a not in names]
    return names


def _is_tool_pattern(entry: str) -> bool:
    """Whether an entry is a preset, glob, or hint pattern rather than a plain name."""
    from ..tools._allowlist import TOOL_PRESETS, is_hint_pattern

    return (
        entry in TOOL_PRESETS
        or is_hint_pattern(entry)
        or any(c in entry for c in "*?[")
    )


def _resolve_requested_tools(
    requested: list[str] | None,
) -> tuple[list[str] | None, list[str]]:
    """Resolve a requested tool selection against the server's ``--tools`` allowlist.

    Returns ``(normalized, forbidden)``:

    * ``normalized`` — the concrete tool list to save. Presets, globs, and hint
      patterns (``read-only``, ``read*``, ``hint:read-only``) are resolved to the
      tool names they currently match, so a saved selection can never widen later
      when a new matching tool becomes available. File-path entries and plain
      names that appear in the allowlist are kept verbatim. Entry order is
      preserved; duplicates are dropped.
    * ``forbidden`` — entries that grant a tool outside the allowlist, or that
      resolve to no tool at all (unknown/unavailable names, non-allowlisted file
      paths). Callers reject the request when this is non-empty.

    An unrestricted server (no allowlist) returns ``requested`` unchanged.
    """
    from ..tools import get_toolchain

    allowlist = _server_tool_allowlist()
    if allowlist is None or not requested:
        return requested, []

    allowed = {t.name for t in get_toolchain(allowlist, strict=False)}
    allowed.update(allowlist)

    normalized: list[str] = []
    forbidden: list[str] = []
    for entry in requested:
        if not _is_tool_pattern(entry) and entry in allowed:
            resolved = [entry]
        else:
            resolved = [t.name for t in get_toolchain([entry], strict=False)]
        if not resolved or not set(resolved) <= allowed:
            forbidden.append(entry)
            continue
        for name in resolved:
            if name not in normalized:
                normalized.append(name)
    return normalized, forbidden


def _validate_branch(branch: object) -> tuple[flask.Response, int] | None:
    """Validate branch name to prevent path traversal attacks and OS errors.

    Branch names are used to construct file paths like ``branches/{branch}.jsonl``,
    so they must not contain path separators or traversal sequences.

    The effective byte limit is ``NAME_MAX - len(".jsonl")`` because the on-disk
    filename is ``{branch}.jsonl``; a branch name filling all 255 bytes would
    produce a 261-byte filename, still triggering ``OSError: [Errno 36]``.

    Returns None if valid, or (error_response, status_code) if invalid.
    """
    if not isinstance(branch, str):
        return flask.jsonify({"error": "Invalid branch name"}), 400
    if len(branch.encode()) > _MAX_ID_LENGTH - _BRANCH_SUFFIX_LEN:
        return flask.jsonify({"error": "branch name too long"}), 400
    if "/" in branch or ".." in branch or "\\" in branch or "\x00" in branch:
        return flask.jsonify({"error": "Invalid branch name"}), 400
    return None


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
    call_id: NotRequired[str]
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
        "step_complete",
        "tool_pending",
        "tool_executing",
        "tool_output",
        "tool_complete",
        "elicit_pending",
        "interrupted",
        "error",
        "config_changed",
        "conversation_edited",
        "watch_event",
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


class StepCompleteEvent(BaseEvent):
    """Sent after session.generating is set to False.

    Unlike generation_complete (which fires before finalizer work), this event
    fires only after the server's generating reservation has been fully released.
    Clients should gate queued-message flushes on this event rather than
    inferring state from generation_complete.
    """


class WatchEvent(BaseEvent):
    """Sent when watched asynchronous work has an update for a conversation."""

    kind: str
    status: str
    ref: str
    message: str


class ToolPendingEvent(BaseEvent):
    """Sent when a tool is detected and waiting for confirmation."""

    tool_id: str
    tooluse: ToolUseDict
    auto_confirm: bool


class ToolExecutingEvent(BaseEvent):
    """Sent when a tool is being executed."""

    tool_id: str


class ToolOutputEvent(BaseEvent):
    """Sent when a tool produces partial output during execution."""

    tool_id: str
    output: str


class ToolCompleteEvent(BaseEvent):
    """Sent when a tool has finished executing."""

    tool_id: str
    duration_ms: float
    success: bool


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
    | StepCompleteEvent
    | WatchEvent
    | ToolPendingEvent
    | ToolExecutingEvent
    | ToolOutputEvent
    | ToolCompleteEvent
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
    if msg.call_id:
        result["call_id"] = msg.call_id
    if msg.metadata:
        result["metadata"] = dict(msg.metadata)
    return result
