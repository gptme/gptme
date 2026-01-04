"""Tests for cache_awareness hook."""

import pytest

from gptme.hooks.cache_awareness import (
    CacheState,
    get_cache_state,
    get_invalidation_count,
    get_status_summary,
    get_tokens_since_invalidation,
    get_turns_since_invalidation,
    is_cache_valid,
    notify_token_usage,
    notify_turn_complete,
    on_cache_change,
    reset_state,
    should_batch_updates,
)


@pytest.fixture(autouse=True)
def reset_cache_state():
    """Reset cache state before each test."""
    reset_state()
    yield
    reset_state()


class TestCacheStateBasics:
    """Tests for basic cache state operations."""

    def test_initial_state(self):
        """Test that initial state has expected defaults."""
        state = get_cache_state()
        assert state.last_invalidation is None
        assert state.last_invalidation_reason is None
        assert state.tokens_before_invalidation is None
        assert state.tokens_after_invalidation is None
        assert state.turns_since_invalidation == 0
        assert state.tokens_since_invalidation == 0
        assert state.invalidation_count == 0

    def test_is_cache_valid_initial(self):
        """Test cache validity check on initial state."""
        # Initially, no turns have passed, so cache is considered invalid
        assert not is_cache_valid()

    def test_is_cache_valid_after_turn(self):
        """Test cache validity after turn completes."""
        notify_turn_complete()
        assert is_cache_valid()

    def test_turns_tracking(self):
        """Test turn counting."""
        assert get_turns_since_invalidation() == 0
        notify_turn_complete()
        assert get_turns_since_invalidation() == 1
        notify_turn_complete()
        assert get_turns_since_invalidation() == 2


class TestTokenTracking:
    """Tests for token usage tracking."""

    def test_token_tracking(self):
        """Test token usage notification."""
        assert get_tokens_since_invalidation() == 0
        notify_token_usage(100)
        assert get_tokens_since_invalidation() == 100
        notify_token_usage(50)
        assert get_tokens_since_invalidation() == 150

    def test_tokens_reset_on_get_state(self):
        """Test that token count persists in state."""
        notify_token_usage(500)
        state = get_cache_state()
        assert state.tokens_since_invalidation == 500


class TestInvalidationCount:
    """Tests for invalidation counting."""

    def test_invalidation_count_initial(self):
        """Test initial invalidation count."""
        assert get_invalidation_count() == 0

    def test_invalidation_count_increments(self):
        """Test that invalidation count increments correctly."""
        # Simulate cache invalidation by updating state directly
        state = get_cache_state()
        state.invalidation_count = 3
        assert get_invalidation_count() == 3


class TestBatchingLogic:
    """Tests for batching decision logic."""

    def test_should_batch_initial(self):
        """Test batch decision with no turns."""
        assert not should_batch_updates(threshold=10)

    def test_should_batch_below_threshold(self):
        """Test batch decision below threshold."""
        for _ in range(5):
            notify_turn_complete()
        assert not should_batch_updates(threshold=10)

    def test_should_batch_at_threshold(self):
        """Test batch decision at threshold."""
        for _ in range(10):
            notify_turn_complete()
        assert should_batch_updates(threshold=10)

    def test_should_batch_above_threshold(self):
        """Test batch decision above threshold."""
        for _ in range(15):
            notify_turn_complete()
        assert should_batch_updates(threshold=10)


class TestCallbacks:
    """Tests for cache change callback system."""

    def test_callback_registration(self):
        """Test callback registration and unregistration."""
        callback_called = []

        def my_callback(state: CacheState):
            callback_called.append(state)

        unsubscribe = on_cache_change(my_callback)

        # Verify callback is registered
        state = get_cache_state()
        assert my_callback in state._callbacks

        # Unsubscribe
        unsubscribe()
        assert my_callback not in state._callbacks

    def test_multiple_callbacks(self):
        """Test multiple callbacks can be registered."""
        results = []

        def callback1(state):
            results.append("cb1")

        def callback2(state):
            results.append("cb2")

        unsub1 = on_cache_change(callback1)
        unsub2 = on_cache_change(callback2)

        state = get_cache_state()
        assert callback1 in state._callbacks
        assert callback2 in state._callbacks

        # Clean up
        unsub1()
        unsub2()


class TestStatusSummary:
    """Tests for status summary generation."""

    def test_status_summary_initial(self):
        """Test status summary with initial state."""
        summary = get_status_summary()
        assert summary["invalidation_count"] == 0
        assert summary["turns_since_invalidation"] == 0
        assert summary["tokens_since_invalidation"] == 0
        assert summary["last_invalidation"] is None
        assert summary["last_invalidation_reason"] is None
        assert summary["tokens_before"] is None
        assert summary["tokens_after"] is None

    def test_status_summary_after_activity(self):
        """Test status summary after some activity."""
        notify_turn_complete()
        notify_turn_complete()
        notify_token_usage(1000)

        summary = get_status_summary()
        assert summary["turns_since_invalidation"] == 2
        assert summary["tokens_since_invalidation"] == 1000


class TestResetState:
    """Tests for state reset functionality."""

    def test_reset_clears_all(self):
        """Test that reset clears all state."""
        # Add some state
        notify_turn_complete()
        notify_token_usage(500)
        state = get_cache_state()
        state.invalidation_count = 5

        # Reset
        reset_state()

        # Verify clean state
        new_state = get_cache_state()
        assert new_state.turns_since_invalidation == 0
        assert new_state.tokens_since_invalidation == 0
        assert new_state.invalidation_count == 0
