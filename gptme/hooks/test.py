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
    """Test hook for MESSAGE_PRE_PROCESS."""
    yield Message("system", "TEST_MESSAGE_PRE_PROCESS hook triggered")


def test_message_post_process_hook(manager) -> Generator[Message, None, None]:
    """Test hook for MESSAGE_POST_PROCESS."""
    yield Message("system", "TEST_MESSAGE_POST_PROCESS hook triggered")


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
        "test_message_pre_process",
        HookType.MESSAGE_PRE_PROCESS,
        test_message_pre_process_hook,
        priority=100,
    )
    register_hook(
        "test_message_post_process",
        HookType.MESSAGE_POST_PROCESS,
        test_message_post_process_hook,
        priority=100,
    )
