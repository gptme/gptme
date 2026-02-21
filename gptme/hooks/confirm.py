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
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from ..tools.base import ToolUse

logger = logging.getLogger(__name__)


# ============================================================================
# Centralized Auto-Confirm State (Thread-Safe via ContextVars)
# ============================================================================
# Using ContextVars for thread safety in server mode where multiple
# requests may be processed concurrently. Each thread/context gets
# its own auto-confirm state.

_auto_override: ContextVar[bool] = ContextVar("auto_override", default=False)
_auto_count: ContextVar[int] = ContextVar("auto_count", default=0)


def set_auto_confirm(count: int | None = None) -> None:
    """Set auto-confirm mode.

    Args:
        count: Number of operations to auto-confirm, or None for infinite
    """
    if count is None:
        _auto_override.set(True)
    else:
        _auto_count.set(count)


def reset_auto_confirm() -> None:
    """Reset auto-confirm state to defaults."""
    _auto_override.set(False)
    _auto_count.set(0)


def check_auto_confirm() -> tuple[bool, str | None]:
    """Check if auto-confirm is active and decrement counter if needed.

    Returns:
        Tuple of (should_auto_confirm, message_or_none)
    """
    if _auto_override.get():
        return True, None
    count = _auto_count.get()
    if count > 0:
        _auto_count.set(count - 1)
        return True, f"Auto-confirmed, {count - 1} left"
    return False, None


def is_auto_confirm_active() -> bool:
    """Check if auto-confirm mode is active (without decrementing)."""
    return _auto_override.get() or _auto_count.get() > 0


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
    - Multiple hooks can be registered with different priorities
    - Hooks are tried in priority order (highest first)
    - Returning None falls through to the next hook
    - First non-None result is used

    This enables tool-specific auto-approve hooks (high priority) to
    confirm allowlisted operations before falling through to CLI/server
    hooks for user confirmation.

    Args:
        tool_use: The tool execution to confirm
        preview: Optional preview of what will be executed
        workspace: Workspace directory (optional)

    Returns:
        ConfirmationResult to handle the confirmation, or None to fall through
        to the next hook in priority order.
    """

    def __call__(
        self,
        tool_use: "ToolUse",
        preview: str | None = None,
        workspace: Path | None = None,
    ) -> ConfirmationResult | None: ...


def confirm(msg: str, default: bool = True) -> bool:
    """Simple confirmation helper for tools.

    This is a convenience wrapper around get_confirmation() for tools that
    need to ask yes/no questions during execution. The current ToolUse is
    obtained from context.

    Args:
        msg: The confirmation message to show
        default: Default action if no hooks registered (True=confirm)

    Returns:
        True if confirmed, False otherwise
    """
    result = get_confirmation(preview=msg, default_confirm=default)
    return result.action == ConfirmAction.CONFIRM


def get_confirmation(
    tool_use: "ToolUse | None" = None,
    preview: str | None = None,
    workspace: Path | None = None,
    default_confirm: bool = True,
) -> ConfirmationResult:
    """Get confirmation for a tool execution via hooks.

    This function triggers the TOOL_CONFIRM hook and handles the result.
    If no hook is registered, returns auto-confirm (for autonomous mode).

    Args:
        tool_use: The tool to confirm. If None, uses current ToolUse from context.
        preview: Optional preview content
        workspace: Workspace directory
        default_confirm: Whether to auto-confirm if no hook is registered

    Returns:
        ConfirmationResult indicating the action to take
    """
    from ..tools.base import get_current_tool_use
    from . import HookType, get_hooks

    # Get tool_use from context if not provided
    if tool_use is None:
        tool_use = get_current_tool_use()
        if tool_use is None:
            # No tool context available - auto-confirm or skip based on default
            if default_confirm:
                logger.debug("No tool_use in context, auto-confirming")
                return ConfirmationResult.confirm()
            logger.debug("No tool_use in context, auto-skipping")
            return ConfirmationResult.skip("No tool context available")

    # Get registered TOOL_CONFIRM hooks
    hooks = get_hooks(HookType.TOOL_CONFIRM)
    enabled_hooks = [h for h in hooks if h.enabled]

    if not enabled_hooks:
        # No confirmation hook registered - autonomous mode
        # Auto-confirm or auto-skip based on default
        if default_confirm:
            logger.debug("No confirmation hook registered, auto-confirming")
            return ConfirmationResult.confirm()
        logger.debug("No confirmation hook registered, auto-skipping")
        return ConfirmationResult.skip("No confirmation hook registered")

    # Try hooks in priority order, falling through if a hook returns None
    for hook in enabled_hooks:
        try:
            logger.debug(f"Calling confirmation hook '{hook.name}'")
            # Cast to ToolConfirmHook - we know it's this type because we only
            # get hooks registered for TOOL_CONFIRM
            confirm_func = cast(ToolConfirmHook, hook.func)
            result = confirm_func(tool_use, preview, workspace)

            if result is None:
                # Hook declined to handle this confirmation, try next hook
                logger.debug(f"Hook '{hook.name}' returned None, trying next hook")
                continue

            if isinstance(result, ConfirmationResult):
                return result
            if isinstance(result, bool):
                # Backward compatibility: simple boolean return
                return (
                    ConfirmationResult.confirm()
                    if result
                    else ConfirmationResult.skip("Declined by user")
                )
            logger.warning(
                f"Confirmation hook '{hook.name}' returned unexpected type: {type(result)}"
            )
            # Treat unexpected types as "pass through"
            continue

        except Exception as e:
            logger.exception(f"Error in confirmation hook '{hook.name}'")
            # On error, skip to be safe
            return ConfirmationResult.skip(f"Error: {e}")

    # No hook handled the confirmation - apply default behavior
    if default_confirm:
        logger.debug("No hook handled confirmation, auto-confirming")
        return ConfirmationResult.confirm()
    logger.debug("No hook handled confirmation, auto-skipping")
    return ConfirmationResult.skip("No confirmation hook handled the request")
