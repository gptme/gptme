"""Auto-confirm hook for autonomous/non-interactive mode.

This hook automatically confirms all tool executions without user interaction.
Useful for autonomous mode, testing, or when tool confirmations should be skipped.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .confirm import ConfirmationResult

if TYPE_CHECKING:
    from ..tools.base import ToolUse

logger = logging.getLogger(__name__)


def auto_confirm_hook(
    tool_use: "ToolUse",
    preview: str | None = None,
    workspace: Path | None = None,
    confirm_msg: str | None = None,
) -> ConfirmationResult:
    """Auto-confirm hook that always confirms execution.

    This hook is for autonomous/non-interactive mode where all tool
    executions should proceed without confirmation.

    Args:
        tool_use: The tool execution to confirm
        preview: Optional preview content (unused in auto-confirm)
        workspace: Workspace directory (unused in auto-confirm)
        confirm_msg: Optional confirmation message (unused in auto-confirm)
    """
    logger.debug(f"Auto-confirming tool execution: {tool_use.tool}")
    return ConfirmationResult.confirm()


def register():
    """Register the auto-confirm hook."""
    from . import HookType, register_hook

    register_hook(
        name="auto_confirm",
        hook_type=HookType.TOOL_CONFIRM,
        func=auto_confirm_hook,
        priority=0,
        enabled=True,
    )
    logger.debug("Registered auto-confirm hook")
