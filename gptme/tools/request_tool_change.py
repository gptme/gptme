"""Opt-in tool for assistants to request session tool configuration changes.

The request is audit-only: calling this tool records structured intent in ordinary
assistant tool-call and result-message history. It does not change the active tool
configuration.
"""

from __future__ import annotations

from ..message import Message
from . import get_available_tools
from .base import Parameter, ToolSpec

_VALID_CHANGE_TYPES = {"enable_tool", "disable_tool", "configure_tool"}
_VALID_URGENCY = {"low", "medium", "high"}


def execute_request_tool_change(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Message:
    """Validate and acknowledge an audit-only tool-change request."""
    del code, args
    values = kwargs or {}

    change_type = values.get("change_type", "")
    tool_name = values.get("tool_name", "")
    reason = values.get("reason", "")
    urgency = values.get("urgency", "")

    if not all(
        isinstance(value, str) for value in (change_type, tool_name, reason, urgency)
    ):
        return Message(
            "system",
            "request_tool_change: all arguments must be strings",
            quiet=True,
        )

    if change_type not in _VALID_CHANGE_TYPES:
        return Message(
            "system",
            "request_tool_change: change_type must be one of: "
            "enable_tool, disable_tool, configure_tool",
            quiet=True,
        )

    known_tools = {tool.name for tool in get_available_tools(include_mcp=False)}
    if tool_name not in known_tools:
        return Message(
            "system",
            f"request_tool_change: unknown tool '{tool_name}'",
            quiet=True,
        )

    if not reason.strip():
        return Message(
            "system",
            "request_tool_change: reason must not be empty",
            quiet=True,
        )

    if urgency not in _VALID_URGENCY:
        return Message(
            "system",
            "request_tool_change: urgency must be one of: low, medium, high",
            quiet=True,
        )

    return Message(
        "system",
        f"Tool change request recorded: {change_type} {tool_name}. "
        "No tool configuration was changed.",
        quiet=True,
    )


tool = ToolSpec(
    name="request_tool_change",
    desc="Record an audit-only request to change the current session's tool configuration",
    instructions="""
Use this tool when the current tool configuration blocks or unnecessarily expands
what you need to do. The request is recorded in ordinary tool-call history for an
operator to inspect; it does not itself enable, disable, or configure any tool.
""".strip(),
    execute=execute_request_tool_change,
    block_types=["request_tool_change"],
    disabled_by_default=True,
    parameters=[
        Parameter(
            name="change_type",
            type='Literal["enable_tool", "disable_tool", "configure_tool"]',
            description="Requested configuration change",
            required=True,
        ),
        Parameter(
            name="tool_name",
            type="string",
            description="Name of a tool known to gptme's module system",
            required=True,
        ),
        Parameter(
            name="reason",
            type="string",
            description="Why the session needs this change",
            required=True,
        ),
        Parameter(
            name="urgency",
            type='Literal["low", "medium", "high"]',
            description="Urgency of the request",
            required=True,
        ),
    ],
)

__doc__ = tool.get_doc(__doc__)
