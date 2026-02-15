"""Shell session cleanup hook.

Closes shell sessions and their file descriptors when conversations end,
preventing file descriptor leaks in the server.

The shell tool uses ContextVar for thread-local shell state, which works well
for the CLI. In the server, tool execution threads create shells that become
unreachable when the thread ends, leaking file descriptors. This hook cleans
them up via the conversation-level shell registry when SESSION_END fires.
"""

import logging
from collections.abc import Generator
from typing import TYPE_CHECKING

from ..message import Message
from . import HookType, StopPropagation, register_hook

if TYPE_CHECKING:
    from ..logmanager import LogManager

logger = logging.getLogger(__name__)


def session_end_shell_cleanup(
    manager: "LogManager", **kwargs
) -> Generator[Message | StopPropagation, None, None]:
    """Close shell session for a conversation to prevent file descriptor leaks.

    Args:
        manager: The LogManager for the session (contains conversation_id)
        **kwargs: Additional arguments (e.g., logdir)

    Yields:
        Nothing - side-effect only (closes shell process and pipes)
    """
    from ..tools.shell import close_conversation_shell

    conversation_id = manager.logdir.name if manager.logdir else None
    if conversation_id:
        close_conversation_shell(conversation_id)

    yield from ()


def register() -> None:
    """Register the shell cleanup hooks with the hook system."""
    register_hook(
        "shell_cleanup.session_end",
        HookType.SESSION_END,
        session_end_shell_cleanup,
        priority=0,
    )
