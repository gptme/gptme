"""
Working directory tracking hook.

Tracks changes to the current working directory (cwd) and notifies the agent
when the cwd changes, helping maintain awareness of the execution context.

This hook monitors directory changes that may occur during tool execution and
provides feedback to help the assistant understand where commands are being executed.
"""

import logging
import os
from collections.abc import Generator
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from ..hooks import HookType, StopPropagation, register_hook
from ..logmanager import Log
from ..message import Message

logger = logging.getLogger(__name__)

# Context-local storage for cwd tracking (ensures context safety in gptme-server)
_cwd_before_var: ContextVar[str | None] = ContextVar("cwd_before", default=None)


def track_cwd_pre_execute(
    log: Log, workspace: Path | None, tool_use: Any
) -> Generator[Message | StopPropagation, None, None]:
    """Store the current working directory before tool execution.

    Args:
        log: The conversation log
        workspace: Workspace directory path
        tool_use: The tool being executed
    """
    try:
        # Store current cwd in context-local storage
        _cwd_before_var.set(os.getcwd())
    except Exception as e:
        logger.exception(f"Error tracking cwd pre-execute: {e}")

    # Pre-execute hooks don't yield messages normally
    return
    yield  # Make this a generator


def track_cwd_post_execute(
    log: Log, workspace: Path | None, tool_use: Any
) -> Generator[Message | StopPropagation, None, None]:
    """Check if the working directory changed after tool execution and notify.

    Args:
        log: The conversation log
        workspace: Workspace directory path
        tool_use: The tool being executed
    """
    try:
        prev_cwd = _cwd_before_var.get()
        if prev_cwd is None:
            # No stored cwd, nothing to compare
            return

        current_cwd = os.getcwd()

        if prev_cwd != current_cwd:
            # Directory changed, yield a notification message
            message = Message(
                "system",
                f"<system_info>Working directory changed to: {current_cwd}</system_info>",
            )
            yield message
            logger.debug(f"Working directory changed from {prev_cwd} to {current_cwd}")

    except Exception as e:
        logger.exception(f"Error tracking cwd post-execute: {e}")


def register() -> None:
    """Register the cwd tracking hooks with the hook system."""
    register_hook(
        "cwd_tracking.pre_execute",
        HookType.TOOL_PRE_EXECUTE,
        track_cwd_pre_execute,
        priority=0,  # Normal priority
    )
    register_hook(
        "cwd_tracking.post_execute",
        HookType.TOOL_POST_EXECUTE,
        track_cwd_post_execute,
        priority=0,  # Normal priority
    )
    logger.debug("Registered cwd tracking hooks")
