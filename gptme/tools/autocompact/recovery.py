"""Context-overflow recovery using the existing rule-based compactor."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .config import _get_keep_head
from .context_provider import CompressionConfig, get_context_provider

if TYPE_CHECKING:
    from ...logmanager import LogManager
    from ...message import Message


def compact_for_overflow(manager: LogManager) -> list[Message]:
    """Build a compacted view for an over-window provider request.

    Overflow bypasses the normal savings decision: the original request already
    failed, so any smaller request is preferable to terminating the session.
    The caller persists the result as a view, preserving the master log.
    """
    config = CompressionConfig(
        logdir=manager.logdir,
        keep_head=_get_keep_head(),
    )
    return (
        get_context_provider("default").compress(manager.log.messages, config).messages
    )
