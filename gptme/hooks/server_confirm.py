"""Server-based tool confirmation hook.

This module provides a confirmation hook for the gptme server that integrates
with the SSE event system and HTTP endpoint for tool confirmation.

The hook:
1. Stores the pending tool in the session
2. Emits an SSE event to notify clients
3. Blocks until the client responds via HTTP
4. Returns the ConfirmationResult

Usage:
    In server mode, register this hook:

        from gptme.hooks.server_confirm import register
        register()
"""

import logging
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .confirm import ConfirmationResult

if TYPE_CHECKING:
    from ..tools.base import ToolUse

logger = logging.getLogger(__name__)


@dataclass
class PendingConfirmation:
    """Tracks a pending confirmation request."""

    tool_use: "ToolUse"
    preview: str | None
    event: threading.Event
    result: ConfirmationResult | None = None


# Global registry for pending confirmations (keyed by tool_id)
# This allows the HTTP endpoint to signal the waiting hook
_pending_confirmations: dict[str, PendingConfirmation] = {}
_lock = threading.Lock()


def register_pending(
    tool_id: str,
    tool_use: "ToolUse",
    preview: str | None,
) -> PendingConfirmation:
    """Register a pending confirmation request.

    Args:
        tool_id: Unique ID for this confirmation
        tool_use: The tool to confirm
        preview: Optional preview content

    Returns:
        PendingConfirmation with an Event to wait on
    """
    with _lock:
        pending = PendingConfirmation(
            tool_use=tool_use,
            preview=preview,
            event=threading.Event(),
        )
        _pending_confirmations[tool_id] = pending
        return pending


def resolve_pending(
    tool_id: str,
    result: ConfirmationResult,
) -> bool:
    """Resolve a pending confirmation with a result.

    Called by the HTTP endpoint when the client responds.

    Args:
        tool_id: The tool ID to resolve
        result: The confirmation result

    Returns:
        True if the tool was found and resolved, False otherwise
    """
    with _lock:
        if tool_id not in _pending_confirmations:
            logger.warning(f"Pending confirmation not found: {tool_id}")
            return False

        pending = _pending_confirmations[tool_id]
        pending.result = result
        pending.event.set()
        return True


def remove_pending(tool_id: str) -> None:
    """Remove a pending confirmation (cleanup after resolution)."""
    with _lock:
        _pending_confirmations.pop(tool_id, None)


def get_pending(tool_id: str) -> PendingConfirmation | None:
    """Get a pending confirmation by ID."""
    with _lock:
        return _pending_confirmations.get(tool_id)


def server_confirm_hook(
    tool_use: "ToolUse",
    preview: str | None = None,
    workspace: Path | None = None,
) -> ConfirmationResult:
    """Server-based confirmation hook using SSE events.

    This hook integrates with the server's pending_tools system:
    1. Creates a pending confirmation with a unique ID
    2. Emits SSE event (handled by the server)
    3. Waits for HTTP endpoint to signal completion
    4. Returns the result

    Note: This hook expects to be called from within a server request context
    where the session is available. If called outside that context, it will
    auto-confirm to avoid blocking.
    """
    # Generate unique tool ID
    tool_id = str(uuid.uuid4())

    # Try to get the current session from the server context
    try:
        # Import to verify we're in a server context
        from ..server.api_v2_sessions import SessionManager  # noqa: F401

        # We need to find the session - this is a bit tricky since we're
        # called from the tool execution context. The session ID should be
        # available via the request context or thread local storage.

        # For now, we'll use a different approach: register our pending
        # confirmation in our own registry, and the HTTP endpoint will
        # look it up here and also update the session's pending_tools.

        # Create pending confirmation
        pending = register_pending(tool_id, tool_use, preview)

        logger.debug(f"Server confirmation hook: waiting for tool {tool_id}")

        # Wait for resolution (with timeout to prevent infinite blocking)
        # In practice, the client should respond or the session will timeout
        if not pending.event.wait(timeout=3600):  # 1 hour timeout
            logger.warning(f"Server confirmation timed out for tool {tool_id}")
            remove_pending(tool_id)
            return ConfirmationResult.skip("Confirmation timed out")

        # Get the result
        result = pending.result
        remove_pending(tool_id)

        if result is None:
            logger.error(f"Pending confirmation resolved but no result: {tool_id}")
            return ConfirmationResult.skip("No result received")

        logger.debug(
            f"Server confirmation received for tool {tool_id}: {result.action}"
        )
        return result

    except ImportError:
        # Server modules not available - we're not in server mode
        logger.warning("Server modules not available, auto-confirming")
        return ConfirmationResult.confirm()

    except Exception as e:
        logger.exception(f"Error in server confirmation hook: {e}")
        return ConfirmationResult.skip(f"Error: {e}")


def register() -> None:
    """Register the server confirmation hook."""
    from . import HookType, register_hook

    register_hook(
        name="server_confirm",
        hook_type=HookType.TOOL_CONFIRM,
        func=server_confirm_hook,
        priority=100,
        enabled=True,
    )
    logger.debug("Registered server_confirm hook")


def unregister() -> None:
    """Unregister the server confirmation hook."""
    from . import HookType, unregister_hook

    unregister_hook("server_confirm", HookType.TOOL_CONFIRM)
