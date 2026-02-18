"""Server-based elicitation hook for WebUI/API clients.

This module provides an elicitation hook for the gptme server that integrates
with the SSE event system and HTTP endpoint for structured user input.

The hook:
1. Stores the pending elicitation request in a registry
2. Emits an SSE event to notify connected clients
3. Blocks until the client responds via HTTP
4. Returns the ElicitationResponse

This enables WebUI clients to show rich input forms (text fields, dropdowns,
checkboxes, secret inputs) when the agent requests structured user input.

Usage:
    In server mode, register this hook:

        from gptme.hooks.server_elicit import register
        register()
"""

import logging
import threading
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field

from .elicitation import ElicitationRequest, ElicitationResponse

logger = logging.getLogger(__name__)

# Re-use the same context variables as server_confirm
# These are set by the server before starting tool execution
current_conversation_id: ContextVar[str | None] = ContextVar(
    "elicit_conversation_id", default=None
)
current_session_id: ContextVar[str | None] = ContextVar(
    "elicit_session_id", default=None
)


@dataclass
class PendingElicitation:
    """Tracks a pending elicitation request."""

    request: ElicitationRequest
    event: threading.Event = field(default_factory=threading.Event)
    result: ElicitationResponse | None = None


# Global registry for pending elicitations (keyed by elicit_id)
_pending_elicitations: dict[str, PendingElicitation] = {}
_lock = threading.Lock()


def register_pending(
    elicit_id: str,
    request: ElicitationRequest,
) -> PendingElicitation:
    """Register a pending elicitation request.

    Args:
        elicit_id: Unique ID for this elicitation
        request: The elicitation request

    Returns:
        PendingElicitation with an Event to wait on
    """
    with _lock:
        pending = PendingElicitation(request=request)
        _pending_elicitations[elicit_id] = pending
        return pending


def resolve_pending(
    elicit_id: str,
    result: ElicitationResponse,
) -> bool:
    """Resolve a pending elicitation with a result.

    Called by the HTTP endpoint when the client responds.

    Args:
        elicit_id: The elicitation ID to resolve
        result: The elicitation response

    Returns:
        True if the elicitation was found and resolved, False otherwise
    """
    with _lock:
        if elicit_id not in _pending_elicitations:
            logger.warning(f"Pending elicitation not found: {elicit_id}")
            return False

        pending = _pending_elicitations[elicit_id]
        pending.result = result
        pending.event.set()
        return True


def remove_pending(elicit_id: str) -> None:
    """Remove a pending elicitation (cleanup after resolution)."""
    with _lock:
        _pending_elicitations.pop(elicit_id, None)


def get_pending(elicit_id: str) -> PendingElicitation | None:
    """Get a pending elicitation by ID."""
    with _lock:
        return _pending_elicitations.get(elicit_id)


def server_elicit_hook(
    request: ElicitationRequest,
) -> ElicitationResponse | None:
    """Server-based elicitation hook using SSE events.

    This hook integrates with the server's SSE event system:
    1. Creates a pending elicitation with a unique ID
    2. Emits SSE event to notify clients (with request details)
    3. Waits for HTTP endpoint to signal completion
    4. Returns the response

    Falls through (returns None) when:
    - Not in a server session context (no conversation/session ID set)
    """
    elicit_id = str(uuid.uuid4())

    # Get session context from contextvars
    # Try our own context vars first, then fall back to server_confirm's
    conversation_id = current_conversation_id.get()
    session_id = current_session_id.get()

    if not conversation_id or not session_id:
        # Try the server_confirm context vars (shared server context)
        try:
            from .server_confirm import (
                current_conversation_id as sc_conv_id,
            )
            from .server_confirm import (
                current_session_id as sc_sess_id,
            )

            conversation_id = sc_conv_id.get()
            session_id = sc_sess_id.get()
        except ImportError:
            pass

    if not conversation_id or not session_id:
        # Not in a server session context - fall through to CLI handler
        logger.debug("No session context available for server elicitation")
        return None

    try:
        from ..server.api_v2_common import ElicitPendingEvent
        from ..server.api_v2_sessions import SessionManager

        # Create pending elicitation
        pending = register_pending(elicit_id, request)

        # Build the event payload
        event_data: ElicitPendingEvent = {
            "type": "elicit_pending",
            "elicit_id": elicit_id,
            "elicit_type": request.type,
            "prompt": request.prompt,
        }
        if request.options:
            event_data["options"] = request.options
        if request.fields:
            event_data["fields"] = [
                {
                    "name": f.name,
                    "prompt": f.prompt,
                    "type": f.type,
                    "options": f.options,
                    "required": f.required,
                    "default": f.default,
                }
                for f in request.fields
            ]
        if request.default:
            event_data["default"] = request.default
        if request.description:
            event_data["description"] = request.description

        # Emit SSE event to notify client
        SessionManager.add_event(conversation_id, event_data)

        logger.debug(
            f"Server elicitation hook: emitted SSE event for elicit {elicit_id}"
        )

        # Wait for resolution (with timeout to prevent infinite blocking)
        if not pending.event.wait(timeout=3600):  # 1 hour timeout
            logger.warning(f"Server elicitation timed out for {elicit_id}")
            remove_pending(elicit_id)
            return ElicitationResponse.cancel()

        # Get the result
        result = pending.result
        remove_pending(elicit_id)

        if result is None:
            logger.error(f"Pending elicitation resolved but no result: {elicit_id}")
            return ElicitationResponse.cancel()

        logger.debug(f"Server elicitation received for {elicit_id}")
        return result

    except ImportError:
        # Server modules not available - fall through
        logger.debug("Server modules not available for elicitation")
        return None

    except Exception as e:
        logger.exception(f"Error in server elicitation hook: {e}")
        return ElicitationResponse.cancel()


def register() -> None:
    """Register the server elicitation hook."""
    from . import HookType, register_hook

    register_hook(
        name="server_elicit",
        hook_type=HookType.ELICIT,
        func=server_elicit_hook,
        priority=100,
        enabled=True,
    )
    logger.debug("Registered server_elicit hook")


def unregister() -> None:
    """Unregister the server elicitation hook."""
    from . import HookType, unregister_hook

    unregister_hook("server_elicit", HookType.ELICIT)
