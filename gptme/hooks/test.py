"""Test hooks for use in test suite only."""

import logging
from collections.abc import Generator
from pathlib import Path

from ..message import Message

logger = logging.getLogger(__name__)


def test_session_start_hook(
    logdir: Path, workspace: Path | None, initial_msgs: list[Message]
) -> Generator[Message, None, None]:
    """Test hook for SESSION_START."""
    yield Message("system", "TEST_SESSION_START hook triggered")


def test_message_pre_process_hook(manager) -> Generator[Message, None, None]:
    """Test hook for STEP_PRE (step.pre - before each step)."""
    yield Message("system", "TEST_STEP_PRE hook triggered")


def test_message_post_process_hook(manager) -> Generator[Message, None, None]:
    """Test hook for TURN_POST (turn.post - after turn completes)."""
    yield Message("system", "TEST_TURN_POST hook triggered")


def register_test_hooks() -> None:
    """Register all test hooks."""
    from . import HookType, register_hook

    register_hook(
        "test_session_start",
        HookType.SESSION_START,
        test_session_start_hook,
        priority=100,  # High priority so it runs early
    )
    register_hook(
        "test_step_pre",
        HookType.STEP_PRE,
        test_message_pre_process_hook,
        priority=100,
    )
    register_hook(
        "test_turn_post",
        HookType.TURN_POST,
        test_message_post_process_hook,
        priority=100,
    )
