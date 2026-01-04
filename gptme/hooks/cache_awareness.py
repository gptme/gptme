"""
Cache awareness hook.

Provides centralized cache state tracking that other hooks/plugins/tools
can rely on to get current cache usage or detect cache invalidation.

This module:
- Tracks when cache was last invalidated
- Tracks token counts before/after compaction
- Provides query functions for plugins to check cache state
- Emits events on cache invalidation for reactive plugins

Terminology:
    "turns" - The number of MESSAGE_POST_PROCESS hook invocations since
    the last cache invalidation. This represents assistant responses,
    not individual messages. See: https://github.com/gptme/gptme/issues/1075
    for discussion on standardizing "turns" vs "steps" terminology.

Limitations:
    Currently only tracks *explicit* cache invalidations triggered by
    auto-compact (via CACHE_INVALIDATED hook). Does NOT detect:
    - Cache expiry due to time (e.g., Anthropic's 5-minute TTL)
    - Cache misses from view regeneration/switching
    - Other implicit invalidation scenarios
    Future work may add heuristics for these cases (e.g., detecting
    >5min gaps between messages as probable cache misses).

Usage by other plugins:
    from gptme.hooks.cache_awareness import (
        get_cache_state,
        is_cache_valid,
        get_tokens_since_invalidation,
        on_cache_change,
    )
"""

import logging
from collections.abc import Callable, Generator
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, TypedDict

from ..hooks import HookType, StopPropagation, register_hook
from ..message import Message

if TYPE_CHECKING:
    from ..logmanager import LogManager

logger = logging.getLogger(__name__)


class CacheStatusSummary(TypedDict):
    """Type-safe return structure for get_status_summary().

    All fields are Optional because they may be None before first invalidation.
    """

    invalidation_count: int
    turns_since_invalidation: int
    tokens_since_invalidation: int
    last_invalidation: str | None
    last_invalidation_reason: str | None
    tokens_before: int | None
    tokens_after: int | None


@dataclass
class CacheState:
    """Represents the current state of the prompt cache."""

    # When cache was last invalidated (None if never)
    last_invalidation: datetime | None = None

    # Reason for last invalidation (e.g., "compact", "edit")
    last_invalidation_reason: str | None = None

    # Token counts from last invalidation event
    tokens_before_invalidation: int | None = None
    tokens_after_invalidation: int | None = None

    # Number of turns/messages since last invalidation
    turns_since_invalidation: int = 0

    # Estimated tokens added since last invalidation
    tokens_since_invalidation: int = 0

    # Total number of cache invalidations in this session
    invalidation_count: int = 0

    # Registered callbacks for cache change events
    _callbacks: list[Callable[["CacheState"], None]] = field(default_factory=list)


# Context-local storage for cache state (ensures context safety in gptme-server)
_cache_state_var: ContextVar[CacheState | None] = ContextVar(
    "cache_state", default=None
)


def _get_state() -> CacheState:
    """Get or create the context-local cache state."""
    state = _cache_state_var.get()
    if state is None:
        state = CacheState()
        _cache_state_var.set(state)
    return state


def _set_state(state: CacheState) -> None:
    """Set the context-local cache state."""
    _cache_state_var.set(state)


# === Public API for other plugins ===


def get_cache_state() -> CacheState:
    """Get the current cache state.

    Returns:
        CacheState object with all cache-related information.

    Example:
        state = get_cache_state()
        if state.turns_since_invalidation > 10:
            # Consider batching updates
            pass
    """
    return _get_state()


def is_cache_valid() -> bool:
    """Check if cache is currently valid (not recently invalidated).

    This is a simple heuristic - cache is considered "valid" if
    at least one turn has passed since invalidation.

    Returns:
        True if cache is likely valid, False if recently invalidated.
    """
    state = _get_state()
    return state.turns_since_invalidation > 0


def get_invalidation_count() -> int:
    """Get the total number of cache invalidations in this session.

    Returns:
        Number of times cache has been invalidated.
    """
    return _get_state().invalidation_count


def get_tokens_since_invalidation() -> int:
    """Get estimated tokens added since last cache invalidation.

    Returns:
        Estimated token count, or 0 if never invalidated.
    """
    return _get_state().tokens_since_invalidation


def get_turns_since_invalidation() -> int:
    """Get number of turns since last cache invalidation.

    Returns:
        Turn count, or 0 if never invalidated.
    """
    return _get_state().turns_since_invalidation


def on_cache_change(callback: Callable[[CacheState], None]) -> Callable[[], None]:
    """Register a callback to be called when cache is invalidated.

    This allows plugins to react to cache invalidation without
    registering their own CACHE_INVALIDATED hook.

    Args:
        callback: Function that takes CacheState and performs updates.

    Returns:
        Unsubscribe function to remove the callback.

    Example:
        def my_handler(state):
            print(f"Cache invalidated: {state.last_invalidation_reason}")

        unsubscribe = on_cache_change(my_handler)
        # Later: unsubscribe()
    """
    state = _get_state()
    state._callbacks.append(callback)

    def unsubscribe():
        if callback in state._callbacks:
            state._callbacks.remove(callback)

    return unsubscribe


def notify_token_usage(tokens: int) -> None:
    """Notify cache awareness of token usage.

    Plugins that track token usage can call this to help
    estimate tokens since last invalidation.

    Args:
        tokens: Number of tokens used/added.
    """
    state = _get_state()
    state.tokens_since_invalidation += tokens
    _set_state(state)


def notify_turn_complete() -> None:
    """Notify cache awareness that a turn has completed.

    Should be called after each message processing cycle
    to track turns since invalidation.
    """
    state = _get_state()
    state.turns_since_invalidation += 1
    _set_state(state)


def reset_state() -> None:
    """Reset cache state (for testing or session restart)."""
    _cache_state_var.set(None)


# === Internal hook handlers ===


def _handle_cache_invalidated(
    manager: "LogManager",
    reason: str,
    tokens_before: int | None = None,
    tokens_after: int | None = None,
) -> Generator[Message | StopPropagation, None, None]:
    """Handle CACHE_INVALIDATED events from autocompact.

    Updates internal state and notifies registered callbacks.

    Args:
        manager: Conversation manager
        reason: Reason for invalidation (e.g., "compact")
        tokens_before: Token count before operation
        tokens_after: Token count after operation

    Yields:
        Optional status message (hidden)
    """
    state = _get_state()

    # Update state
    state.last_invalidation = datetime.now()
    state.last_invalidation_reason = reason
    state.tokens_before_invalidation = tokens_before
    state.tokens_after_invalidation = tokens_after
    state.turns_since_invalidation = 0
    state.tokens_since_invalidation = 0
    state.invalidation_count += 1

    _set_state(state)

    logger.debug(
        f"Cache invalidated (reason={reason}, "
        f"tokens: {tokens_before} → {tokens_after}, "
        f"total invalidations: {state.invalidation_count})"
    )

    # Notify registered callbacks
    for callback in state._callbacks:
        try:
            callback(state)
        except Exception as e:
            logger.warning(f"Cache change callback failed: {e}")

    # Yield nothing - this is a tracking hook, not a message-producing hook
    yield from ()


def _handle_message_post_process(
    manager: "LogManager",
) -> Generator[Message | StopPropagation, None, None]:
    """Track turns after message processing.

    Args:
        manager: Conversation manager

    Yields:
        Nothing (tracking only)
    """
    notify_turn_complete()
    yield from ()


def register() -> None:
    """Register cache awareness hooks with the hook system."""
    # Listen for cache invalidation events.
    # NOTE: This only captures EXPLICIT invalidations from auto-compact.
    # It does NOT detect implicit invalidations like:
    # - Cache expiry (e.g., Anthropic's 5-minute TTL)
    # - View regeneration/switching
    # Future work may add time-based heuristics for implicit invalidation.
    register_hook(
        "cache_awareness.invalidated",
        HookType.CACHE_INVALIDATED,
        _handle_cache_invalidated,
        priority=100,  # High priority - update state before other handlers
    )

    # Track turns (MESSAGE_POST_PROCESS invocations) for invalidation counting.
    # See module docstring for "turns" vs "steps" terminology discussion.
    register_hook(
        "cache_awareness.turn_tracking",
        HookType.MESSAGE_POST_PROCESS,
        _handle_message_post_process,
        priority=0,  # Normal priority
    )

    logger.debug("Registered cache awareness hooks")


# === Convenience functions for common patterns ===


def should_batch_updates(threshold: int = 10) -> bool:
    """Check if enough turns have passed to justify batching updates.

    Useful for plugins that want to defer expensive operations
    until cache is about to be invalidated anyway.

    Args:
        threshold: Number of turns to consider "enough" for batching.

    Returns:
        True if turns_since_invalidation >= threshold.
    """
    return get_turns_since_invalidation() >= threshold


def get_status_summary() -> CacheStatusSummary:
    """Get a summary of cache state for logging/debugging.

    Returns:
        Type-safe dictionary with key cache metrics.
    """
    state = _get_state()
    return {
        "invalidation_count": state.invalidation_count,
        "turns_since_invalidation": state.turns_since_invalidation,
        "tokens_since_invalidation": state.tokens_since_invalidation,
        "last_invalidation": (
            state.last_invalidation.isoformat() if state.last_invalidation else None
        ),
        "last_invalidation_reason": state.last_invalidation_reason,
        "tokens_before": state.tokens_before_invalidation,
        "tokens_after": state.tokens_after_invalidation,
    }
