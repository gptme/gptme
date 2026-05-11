"""
Cache awareness hook.

Provides centralized cache state tracking that other hooks/plugins/tools
can rely on to get current cache usage or detect cache invalidation.

This module:
- Tracks when cache was last invalidated (explicit, via CACHE_INVALIDATED hook)
- Tracks when the last LLM call completed (via GENERATION_POST hook)
- Provides ``is_cache_likely_cold()`` to predict implicit TTL-based expiry
- Tracks token counts before/after compaction
- Provides query functions for plugins to check cache state
- Emits events on cache invalidation for reactive plugins

Terminology:
    "turns" - The number of TURN_POST (turn.post) hook invocations since
    the last cache invalidation. This represents assistant responses,
    not individual messages. See: https://github.com/gptme/gptme/issues/1075
    for discussion on standardizing "turns" vs "steps" terminology.

Cache coldness heuristic:
    ``is_cache_likely_cold(ttl_seconds)`` returns True when the time elapsed
    since the last LLM call exceeds the provider's cache TTL (default 300 s
    for Anthropic).  This lets plugins like ToolOutputTrimmer trim context
    *before* a predicted cold-cache request, rather than reacting after the
    fact.  The heuristic is conservative: if no prior call has been recorded
    it returns False (unknown → assume warm).

Usage by other plugins:
    from gptme.hooks.cache_awareness import (
        get_cache_state,
        is_cache_valid,
        is_cache_likely_cold,
        get_elapsed_since_last_call,
        get_tokens_since_invalidation,
        on_cache_change,
    )
"""

import logging
from collections.abc import Callable, Generator
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, TypedDict

# Default Anthropic prompt-cache TTL in seconds.
# The actual TTL is ~5 min; we use 300 s as the heuristic threshold.
ANTHROPIC_CACHE_TTL_SECONDS: float = 300.0

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
    last_call_completed_at: str | None
    elapsed_since_last_call: float | None
    cache_likely_cold: bool


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

    # Timestamp when the last LLM call completed (GENERATION_POST).
    # Used to predict implicit cache expiry via TTL heuristic.
    last_call_completed_at: datetime | None = None

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


def is_cache_likely_cold(ttl_seconds: float = ANTHROPIC_CACHE_TTL_SECONDS) -> bool:
    """Check whether the prompt cache is likely cold (expired due to inactivity).

    Uses a time-gap heuristic: if more than *ttl_seconds* have elapsed since
    the last LLM call completed, the provider's TTL has almost certainly
    expired and the cache is cold.

    This lets plugins act *before* the next LLM request rather than reacting
    to a confirmed cache miss after the fact.

    Returns False (assume warm) when no prior call has been recorded.

    Note:
        The default threshold (``ANTHROPIC_CACHE_TTL_SECONDS``) is calibrated
        for Anthropic's ~5-minute prompt-cache TTL.  For non-Anthropic providers
        (OpenAI, local models, etc.) pass an explicit *ttl_seconds* value, or
        treat the result as meaningless if that provider has no prompt cache.

    Args:
        ttl_seconds: Cache TTL threshold in seconds.
                     Defaults to ``ANTHROPIC_CACHE_TTL_SECONDS`` (300 s).

    Returns:
        True if the cache is predicted to be cold, False otherwise.

    Example::

        if is_cache_likely_cold():
            # Trim context before the request to save cost on cold-cache hit
            pass
    """
    elapsed = get_elapsed_since_last_call()
    if elapsed is None:
        return False  # No prior call recorded — assume warm (conservative)
    return elapsed >= ttl_seconds


def get_elapsed_since_last_call() -> float | None:
    """Return the number of seconds elapsed since the last LLM call completed.

    Returns:
        Elapsed seconds as a float, or None if no call has been recorded yet.

    Example::

        elapsed = get_elapsed_since_last_call()
        if elapsed is not None and elapsed > 60:
            print(f"Last call was {elapsed:.0f}s ago")
    """
    state = _get_state()
    if state.last_call_completed_at is None:
        return None
    now = datetime.now(tz=timezone.utc)
    return (now - state.last_call_completed_at).total_seconds()


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
    state.last_invalidation = datetime.now(tz=timezone.utc)
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


def _handle_generation_post(
    message: Message,
    **kwargs: object,
) -> Generator[Message | StopPropagation, None, None]:
    """Record the completion time of each LLM call.

    Stores the current UTC timestamp in ``CacheState.last_call_completed_at``
    so that ``is_cache_likely_cold()`` can later check whether the provider's
    cache TTL has elapsed since this call.

    Args:
        message: The generated assistant message (unused here).

    Yields:
        Nothing (tracking only)
    """
    state = _get_state()
    state.last_call_completed_at = datetime.now(tz=timezone.utc)
    _set_state(state)
    logger.debug("Recorded LLM call completion time for cache TTL heuristic")
    yield from ()


def register() -> None:
    """Register cache awareness hooks with the hook system."""
    # Listen for cache invalidation events.
    # NOTE: This only captures EXPLICIT invalidations from auto-compact.
    # For implicit TTL-based expiry, use is_cache_likely_cold() instead.
    register_hook(
        "cache_awareness.invalidated",
        HookType.CACHE_INVALIDATED,
        _handle_cache_invalidated,
        priority=100,  # High priority - update state before other handlers
    )

    # Track turns (TURN_POST invocations) for invalidation counting.
    # See module docstring for "turns" vs "steps" terminology discussion.
    register_hook(
        "cache_awareness.turn_tracking",
        HookType.TURN_POST,
        _handle_message_post_process,
        priority=0,  # Normal priority
    )

    # Track LLM call completion time for TTL-based cache coldness heuristic.
    # Fires after each assistant generation so is_cache_likely_cold() can
    # measure elapsed time at the next GENERATION_PRE.
    register_hook(
        "cache_awareness.generation_post",
        HookType.GENERATION_POST,
        _handle_generation_post,
        priority=100,  # High priority - record time before other handlers
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
    # Compute elapsed once so elapsed_since_last_call and cache_likely_cold are
    # derived from the same instant — avoids contradictory snapshots at the threshold.
    elapsed = get_elapsed_since_last_call()
    cache_cold = elapsed is not None and elapsed >= ANTHROPIC_CACHE_TTL_SECONDS
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
        "last_call_completed_at": (
            state.last_call_completed_at.isoformat()
            if state.last_call_completed_at
            else None
        ),
        "elapsed_since_last_call": elapsed,
        "cache_likely_cold": cache_cold,
    }
