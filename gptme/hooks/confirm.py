"""Tool confirmation hook system.

This module provides the hook infrastructure for tool confirmations,
allowing different confirmation implementations (CLI, Server, Auto) to be
plugged in via the hook system.

Usage:
    - CLI: Register cli_confirm_hook for terminal-based confirmation
    - Server: Register server_confirm_hook for SSE event-based confirmation
    - Autonomous: No hook registered (or auto_confirm_hook for explicit auto-confirm)
"""

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..tools.base import ToolUse

logger = logging.getLogger(__name__)


# ============================================================================
# Centralized Auto-Confirm State
# ============================================================================
# These globals are shared between CLI and other confirmation mechanisms.
# This eliminates duplicate state that was previously in both ask_execute.py
# and cli_confirm.py.

_auto_override: bool = False  # When True, auto-confirm all operations
_auto_count: int = 0  # When > 0, auto-confirm this many operations


def set_auto_confirm(count: int | None = None) -> None:
    """Set auto-confirm mode.

    Args:
        count: Number of operations to auto-confirm, or None for infinite
    """
    global _auto_override, _auto_count
    if count is None:
        _auto_override = True
    else:
        _auto_count = count


def reset_auto_confirm() -> None:
    """Reset auto-confirm state to defaults."""
    global _auto_override, _auto_count
    _auto_override = False
    _auto_count = 0


def check_auto_confirm() -> tuple[bool, str | None]:
    """Check if auto-confirm is active and decrement counter if needed.

    Returns:
        Tuple of (should_auto_confirm, message_or_none)
    """
    global _auto_count
    if _auto_override:
        return True, None
    if _auto_count > 0:
        _auto_count -= 1
        return True, f"Auto-confirmed, {_auto_count} left"
    return False, None


def is_auto_confirm_active() -> bool:
    """Check if auto-confirm mode is active (without decrementing)."""
    return _auto_override or _auto_count > 0


class ConfirmAction(str, Enum):
    """Actions that can be taken on a tool confirmation request."""

    CONFIRM = "confirm"  # Execute the tool
    SKIP = "skip"  # Skip execution
    EDIT = "edit"  # Edit content and execute


@dataclass
class ConfirmationResult:
    """Result of a tool confirmation request.

    Attributes:
        action: The action to take (confirm, skip, edit)
        edited_content: If action is EDIT, the edited content
        message: Optional message to include (e.g., "skipped by user")
    """

    action: ConfirmAction
    edited_content: str | None = None
    message: str | None = None

    @classmethod
    def confirm(cls) -> "ConfirmationResult":
        """Create a confirmation result that confirms execution."""
        return cls(action=ConfirmAction.CONFIRM)

    @classmethod
    def skip(cls, message: str | None = None) -> "ConfirmationResult":
        """Create a confirmation result that skips execution."""
        return cls(action=ConfirmAction.SKIP, message=message or "Operation skipped")

    @classmethod
    def edit(cls, edited_content: str) -> "ConfirmationResult":
        """Create a confirmation result with edited content."""
        return cls(action=ConfirmAction.EDIT, edited_content=edited_content)


class ToolConfirmHook(Protocol):
    """Protocol for tool confirmation hooks.

    Tool confirmation hooks are different from other hooks:
    - They RETURN a ConfirmationResult instead of yielding Messages
    - They are blocking (wait for user/client decision)
    - Only ONE should be registered at a time

    Args:
        tool_use: The tool execution to confirm
        preview: Optional preview of what will be executed
        workspace: Workspace directory (optional)
    """

    def __call__(
        self,
        tool_use: "ToolUse",
        preview: str | None = None,
        workspace: Path | None = None,
    ) -> ConfirmationResult: ...


def get_confirmation(
    tool_use: "ToolUse",
    preview: str | None = None,
    workspace: Path | None = None,
    default_confirm: bool = True,
) -> ConfirmationResult:
    """Get confirmation for a tool execution via hooks.

    This function triggers the TOOL_CONFIRM hook and handles the result.
    If no hook is registered, returns auto-confirm (for autonomous mode).

    Args:
        tool_use: The tool to confirm
        preview: Optional preview content
        workspace: Workspace directory
        default_confirm: Whether to auto-confirm if no hook is registered

    Returns:
        ConfirmationResult indicating the action to take
    """
    from . import HookType, get_hooks

    # Get registered TOOL_CONFIRM hooks
    hooks = get_hooks(HookType.TOOL_CONFIRM)
    enabled_hooks = [h for h in hooks if h.enabled]

    if not enabled_hooks:
        # No confirmation hook registered - autonomous mode
        # Auto-confirm or auto-skip based on default
        if default_confirm:
            logger.debug("No confirmation hook registered, auto-confirming")
            return ConfirmationResult.confirm()
        else:
            logger.debug("No confirmation hook registered, auto-skipping")
            return ConfirmationResult.skip("No confirmation hook registered")

    # Use the first (highest priority) enabled hook
    hook = enabled_hooks[0]

    try:
        logger.debug(f"Calling confirmation hook '{hook.name}'")
        # Cast to ToolConfirmHook - we know it's this type because we only
        # get hooks registered for TOOL_CONFIRM
        from typing import cast

        confirm_func = cast(ToolConfirmHook, hook.func)
        result = confirm_func(tool_use, preview, workspace)

        if isinstance(result, ConfirmationResult):
            return result
        elif isinstance(result, bool):
            # Backward compatibility: simple boolean return
            return (
                ConfirmationResult.confirm()
                if result
                else ConfirmationResult.skip("Declined by user")
            )
        else:
            logger.warning(
                f"Confirmation hook '{hook.name}' returned unexpected type: {type(result)}"
            )
            return ConfirmationResult.confirm()

    except Exception as e:
        logger.exception(f"Error in confirmation hook '{hook.name}'")
        # On error, skip to be safe
        return ConfirmationResult.skip(f"Error: {e}")
