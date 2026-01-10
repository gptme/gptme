"""Bridge between the hook-based confirmation system and the legacy ConfirmFunc interface.

This module provides utilities to integrate the new hook-based confirmation
system with the existing ConfirmFunc-based tool execution.
"""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from .confirm import ConfirmAction, ConfirmationResult, get_confirmation

if TYPE_CHECKING:
    from ..tools.base import ToolUse

logger = logging.getLogger(__name__)


def make_confirm_func_from_hooks(
    workspace: Path | None = None,
    default_confirm: bool = True,
) -> Callable[[str], bool]:
    """Create a ConfirmFunc that uses the hook system for confirmation.

    This provides backward compatibility with the existing ConfirmFunc interface
    while delegating to the hook-based confirmation system.

    Args:
        workspace: Workspace directory for context
        default_confirm: Whether to auto-confirm if no hook is registered

    Returns:
        A ConfirmFunc that uses hooks for confirmation
    """

    def confirm_func(message: str) -> bool:
        # Create a minimal ToolUse for the hook
        # Note: This is a simplified version - in practice, the tool would
        # call this with the actual ToolUse
        from ..tools.base import ToolUse

        # Create a placeholder ToolUse since we only have the message
        tool_use = ToolUse(
            tool="unknown",
            args=[],
            kwargs={},
            content=message,
        )

        result = get_confirmation(
            tool_use=tool_use,
            preview=message,
            workspace=workspace,
            default_confirm=default_confirm,
        )

        return result.action == ConfirmAction.CONFIRM

    return confirm_func


def confirm_tool_use(
    tool_use: "ToolUse",
    workspace: Path | None = None,
    default_confirm: bool = True,
) -> ConfirmationResult:
    """Get confirmation for a specific ToolUse via the hook system.

    This is the preferred way to get confirmation in new code,
    providing access to the full ConfirmationResult including
    edited content.

    Args:
        tool_use: The tool use to confirm
        workspace: Workspace directory for context
        default_confirm: Whether to auto-confirm if no hook is registered

    Returns:
        ConfirmationResult with action and optional edited content
    """
    # Generate preview from tool use
    preview = _generate_preview(tool_use)

    return get_confirmation(
        tool_use=tool_use,
        preview=preview,
        workspace=workspace,
        default_confirm=default_confirm,
    )


def _generate_preview(tool_use: "ToolUse") -> str:
    """Generate a preview string for a ToolUse."""
    if tool_use.content:
        return tool_use.content

    parts = [f"Tool: {tool_use.tool}"]
    if tool_use.args:
        parts.append(f"Args: {tool_use.args}")
    if tool_use.kwargs:
        parts.append(f"Kwargs: {tool_use.kwargs}")

    return "\n".join(parts)
