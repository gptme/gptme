"""Deliver attributed peer input at safe step boundaries, without subagent tools."""

from collections.abc import Generator

from ..logmanager import LogManager
from ..message import Message
from ..prompt_queue import drain_peer_prompts
from . import HookType, register_hook


def peer_messages(
    manager: LogManager, **kwargs: object
) -> Generator[Message, None, None]:
    """Only the receiver's existing writer drains its branch's peer inbox."""
    yield from drain_peer_prompts(manager.logdir, branch=manager.current_branch)


def register() -> None:
    register_hook("peer_messages", HookType.STEP_PRE, peer_messages, 10)
