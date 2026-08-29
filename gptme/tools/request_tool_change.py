"""Opt-in tool for assistants to request session tool configuration changes.

Phase 1 (audit-only) recorded the intent in tool-call history without mutating
the active tool set.  Phase 2 (this file) actually applies enable/disable
requests by mutating the session-local ``_loaded_tools_var`` ContextVar that
``get_tools()`` reads.

``configure_tool`` requests remain audit-only; a future phase can add actuation
for them without altering the enable/disable paths.
"""

from __future__ import annotations

import logging

from ..message import Message
from . import (
    get_available_tools,
    get_session_allowlist,
    get_tools,
    load_tool,
    unload_tool,
)
from ._allowlist import tool_matches_allowlist
from .base import Parameter, ToolSpec

logger = logging.getLogger(__name__)

_VALID_CHANGE_TYPES = {"enable_tool", "disable_tool", "configure_tool"}
_VALID_URGENCY = {"low", "medium", "high"}

#: The tool that provides this mechanism must never be disabled by itself.
_SELF_NAME = "request_tool_change"


def _enable_tool(tool_name: str) -> Message:
    """Add *tool_name* to the session's active tool set.

    Returns a system message describing the outcome (success or no-op).
    """
    loaded = get_tools()
    if any(t.name == tool_name for t in loaded):
        return Message(
            "system",
            f"request_tool_change: '{tool_name}' is already enabled — no change.",
            quiet=True,
        )

    allowlist = get_session_allowlist()
    if allowlist is not None:
        spec = next((t for t in get_available_tools() if t.name == tool_name), None)
        hints = spec.hints if spec is not None else frozenset()
        if not tool_matches_allowlist(tool_name, allowlist, hints):
            return Message(
                "system",
                f"request_tool_change: '{tool_name}' is not permitted by "
                "the session tool allowlist — no change.",
                quiet=True,
            )

    try:
        load_tool(tool_name)
    except Exception as exc:
        logger.exception("request_tool_change: failed to enable '%s'", tool_name)
        return Message(
            "system",
            f"request_tool_change: could not enable '{tool_name}': {exc}",
            quiet=True,
        )

    logger.info("request_tool_change: enabled tool '%s'", tool_name)
    return Message(
        "system",
        f"Tool '{tool_name}' has been enabled for this session.",
        quiet=True,
    )


def _disable_tool(tool_name: str) -> Message:
    """Remove *tool_name* from the session's active tool set.

    Returns a system message describing the outcome (success, no-op, or
    refusal when trying to disable the mechanism itself).
    """
    if tool_name == _SELF_NAME:
        return Message(
            "system",
            "request_tool_change: cannot disable itself — no change.",
            quiet=True,
        )

    if not any(t.name == tool_name for t in get_tools()):
        return Message(
            "system",
            f"request_tool_change: '{tool_name}' is not currently enabled — no change.",
            quiet=True,
        )

    try:
        unload_tool(tool_name)
    except Exception as exc:
        logger.exception("request_tool_change: failed to disable '%s'", tool_name)
        return Message(
            "system",
            f"request_tool_change: could not disable '{tool_name}': {exc}",
            quiet=True,
        )

    logger.info("request_tool_change: disabled tool '%s'", tool_name)
    return Message(
        "system",
        f"Tool '{tool_name}' has been disabled for this session.",
        quiet=True,
    )


def execute_request_tool_change(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Message:
    """Validate and apply a tool-change request.

    ``enable_tool`` and ``disable_tool`` requests are applied immediately by
    mutating the session-local tool list returned by ``get_tools()``.
    ``configure_tool`` requests are still audit-only.
    """
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

    # Include MCP tools (default get_available_tools) so an allowlisted MCP
    # tool can be enabled before it is loaded. Union with get_tools() so a
    # file-loaded tool that is not in module discovery can still be disabled.
    known_tools = {t.name for t in get_available_tools()} | {
        t.name for t in get_tools()
    }
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

    if change_type == "enable_tool":
        return _enable_tool(tool_name)
    if change_type == "disable_tool":
        return _disable_tool(tool_name)
    # configure_tool: audit-only (Phase 2 scope)
    return Message(
        "system",
        f"Tool configure request recorded: {tool_name}. "
        "Configuration changes are not yet applied automatically.",
        quiet=True,
    )


tool = ToolSpec(
    name="request_tool_change",
    desc=(
        "Enable or disable a tool for the current session, "
        "or record a configuration change request"
    ),
    instructions="""
Use this tool to adjust which tools are available mid-session.

- ``enable_tool``: adds a known tool to the active set so it can be called in
  subsequent turns.
- ``disable_tool``: removes a tool from the active set for the remainder of the
  session.  You cannot disable ``request_tool_change`` itself.
- ``configure_tool``: records a configuration change request (audit-only for now).

Changes take effect immediately; the next LLM turn will see the updated tool list.
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
