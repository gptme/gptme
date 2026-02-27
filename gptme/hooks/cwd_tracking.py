"""
Working directory tracking hook.

Notifies the agent when the working directory changes, helping maintain
awareness of the execution context.

Subscribes to the centralized CWD_CHANGED hook type instead of independently
tracking pre/post CWD values.

See: https://github.com/gptme/gptme/issues/1521
"""

import logging
from collections.abc import Generator
from pathlib import Path
from typing import Any

from ..hooks import HookType, StopPropagation, register_hook
from ..logmanager import Log
from ..message import Message

logger = logging.getLogger(__name__)


def on_cwd_changed(
    log: Log,
    workspace: Path | None,
    old_cwd: str,
    new_cwd: str,
    tool_use: Any,
) -> Generator[Message | StopPropagation, None, None]:
    """Notify the agent that the working directory changed.

    Args:
        log: The conversation log
        workspace: Workspace directory path
        old_cwd: Previous working directory
        new_cwd: New working directory
        tool_use: The tool that caused the change
    """
    yield Message(
        "system",
        f"<system_info>Working directory changed to: {new_cwd}</system_info>",
    )
    logger.debug(f"Working directory changed from {old_cwd} to {new_cwd}")


def register() -> None:
    """Register the cwd tracking hook."""
    register_hook(
        "cwd_tracking.notification",
        HookType.CWD_CHANGED,
        on_cwd_changed,
        priority=0,
    )
    logger.debug("Registered cwd tracking hook")
