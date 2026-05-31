"""
Centralized CWD change detection.

Monitors directory changes during tool execution and fires the CWD_CHANGED
hook type. Other hooks subscribe to CWD_CHANGED directly instead of each
implementing their own pre/post CWD comparison.

See: https://github.com/gptme/gptme/issues/1521
"""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from typing import TYPE_CHECKING

from ..hooks import HookType, StopPropagation, register_hook, trigger_hook

if TYPE_CHECKING:
    from collections.abc import Generator

    from ..hooks.types import ToolExecutePostData, ToolExecutePreData
    from ..message import Message

logger = logging.getLogger(__name__)

# Context-local storage for CWD before tool execution
_cwd_before_var: ContextVar[str | None] = ContextVar("cwd_changed_before", default=None)


def _store_cwd(
    data: ToolExecutePreData,
) -> Generator[Message | StopPropagation, None, None]:
    """Store the current working directory before tool execution."""
    try:
        _cwd_before_var.set(os.getcwd())
    except Exception as e:
        logger.exception(f"Error storing CWD: {e}")

    return
    yield  # make generator


def _detect_change(
    data: ToolExecutePostData,
) -> Generator[Message | StopPropagation, None, None]:
    """Detect CWD changes and trigger CWD_CHANGED hooks."""
    try:
        prev_cwd = _cwd_before_var.get()
        if prev_cwd is None:
            return

        current_cwd = os.getcwd()

        if prev_cwd != current_cwd:
            logger.debug(f"CWD changed: {prev_cwd} → {current_cwd}")
            yield from trigger_hook(
                HookType.CWD_CHANGED,
                log=data.log,
                workspace=data.workspace,
                old_cwd=prev_cwd,
                new_cwd=current_cwd,
                tool_use=data.tool_use,
            )
    except Exception as e:
        logger.exception(f"Error detecting CWD change: {e}")


def register() -> None:
    """Register the centralized CWD change detection hooks."""
    register_hook(
        "cwd_changed.store",
        HookType.TOOL_EXECUTE_PRE,
        _store_cwd,
        priority=100,  # High priority — run first to capture CWD
    )
    register_hook(
        "cwd_changed.detect",
        HookType.TOOL_EXECUTE_POST,
        _detect_change,
        priority=100,  # High priority — run first to detect CWD changes
    )
