"""Tests for gptme.hooks.cache_awareness module.

Tests the centralized cache state tracking system that other hooks/plugins/tools
rely on for current cache usage and cache invalidation detection.
"""

import copy
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from gptme.hooks.cache_awareness import (
    CacheState,
    _get_state,
    _handle_cache_invalidated,
    _handle_generation_post,
    _handle_message_post_process,
    get_cache_state,
    get_elapsed_since_last_call,
    get_invalidation_count,
    get_status_summary,
    get_tokens_since_invalidation,
    get_turns_since_invalidation,
    is_cache_likely_cold,
    is_cache_valid,
    notify_token_usage,
    notify_turn_complete,
    on_cache_change,
    reset_state,
    should_batch_updates,
)


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset cache state before and after each test."""
    reset_state()
    yield
    reset_state()


# === CacheState dataclass ===


class TestCacheState:
    def test_default_values(self):
        state = CacheState()
        assert state.last_invalidation is None
        assert state.last_invalidation_reason is None
        assert state.tokens_before_invalidation is None
        assert state.tokens_after_invalidation is None
        assert state.turns_since_invalidation == 0
        assert state.tokens_since_invalidation == 0
        assert state.invalidation_count == 0
        assert state._callbacks == []

    def test_callbacks_are_independent_instances(self):
        """Each CacheState should have its own callback list."""
        state1 = CacheState()
        state2 = CacheState()
        state1._callbacks.append(lambda s: None)
        assert len(state1._callbacks) == 1
        assert len(state2._callbacks) == 0


# === Context-local state management ===


class TestStateManagement:
    def test_get_state_creates_on_first_access(self):
        state = _get_state()
        assert isinstance(state, CacheState)

    def test_get_state_returns_same_instance(self):
        state1 = _get_state()
        state2 = _get_state()
        assert state1 is state2

    def test_reset_state_clears(self):
        state1 = _get_state()
        state1.invalidation_count = 5
        reset_state()
        state2 = _get_state()
        assert state2.invalidation_count == 0
        assert state1 is not state2

    def test_get_cache_state_public_api(self):
        state = get_cache_state()
        assert isinstance(state, CacheState)
        assert state is _get_state()


# === Public query API ===


class TestIsValidCache:
    def test_invalid_initially(self):
        """Cache should be invalid initially (0 turns)."""
        assert not is_cache_valid()

    def test_valid_after_one_turn(self):
        notify_turn_complete()
        assert is_cache_valid()

    def test_invalid_after_invalidation(self):
        notify_turn_complete()
        assert is_cache_valid()
        # Simulate cache invalidation
        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="compact"))
        assert not is_cache_valid()

    def test_valid_again_after_turn_post_invalidation(self):
        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="compact"))
        assert not is_cache_valid()
        notify_turn_complete()
        assert is_cache_valid()


class TestInvalidationCount:
    def test_zero_initially(self):
        assert get_invalidation_count() == 0

    def test_increments_on_invalidation(self):
        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="compact"))
        assert get_invalidation_count() == 1

    def test_multiple_invalidations(self):
        manager = MagicMock()
        for _ in range(5):
            list(_handle_cache_invalidated(manager, reason="compact"))
        assert get_invalidation_count() == 5


class TestTokenTracking:
    def test_zero_initially(self):
        assert get_tokens_since_invalidation() == 0

    def test_notify_token_usage(self):
        notify_token_usage(100)
        assert get_tokens_since_invalidation() == 100

    def test_cumulative_token_usage(self):
        notify_token_usage(100)
        notify_token_usage(200)
        notify_token_usage(50)
        assert get_tokens_since_invalidation() == 350

    def test_resets_on_invalidation(self):
        notify_token_usage(500)
        assert get_tokens_since_invalidation() == 500
        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="compact"))
        assert get_tokens_since_invalidation() == 0

    def test_accumulates_after_invalidation(self):
        notify_token_usage(500)
        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="compact"))
        notify_token_usage(200)
        assert get_tokens_since_invalidation() == 200


class TestTurnTracking:
    def test_zero_initially(self):
        assert get_turns_since_invalidation() == 0

    def test_notify_turn_complete(self):
        notify_turn_complete()
        assert get_turns_since_invalidation() == 1

    def test_multiple_turns(self):
        for _ in range(7):
            notify_turn_complete()
        assert get_turns_since_invalidation() == 7

    def test_resets_on_invalidation(self):
        for _ in range(5):
            notify_turn_complete()
        assert get_turns_since_invalidation() == 5
        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="compact"))
        assert get_turns_since_invalidation() == 0


# === Callback system ===


class TestOnCacheChange:
    def test_register_callback(self):
        called_with = []
        on_cache_change(lambda s: called_with.append(s))

        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="compact"))

        assert len(called_with) == 1
        assert isinstance(called_with[0], CacheState)

    def test_callback_receives_current_state(self):
        received_state = []
        on_cache_change(lambda s: received_state.append(copy.copy(s)))

        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="edit"))

        assert received_state[0].last_invalidation_reason == "edit"
        assert received_state[0].invalidation_count == 1

    def test_multiple_callbacks(self):
        counts = [0, 0, 0]
        on_cache_change(lambda s: counts.__setitem__(0, counts[0] + 1))
        on_cache_change(lambda s: counts.__setitem__(1, counts[1] + 1))
        on_cache_change(lambda s: counts.__setitem__(2, counts[2] + 1))

        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="compact"))

        assert counts == [1, 1, 1]

    def test_unsubscribe(self):
        called = [0]
        unsubscribe = on_cache_change(lambda s: called.__setitem__(0, called[0] + 1))

        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="compact"))
        assert called[0] == 1

        unsubscribe()
        list(_handle_cache_invalidated(manager, reason="compact"))
        assert called[0] == 1  # Not called again

    def test_unsubscribe_idempotent(self):
        """Calling unsubscribe twice should not raise."""
        unsubscribe = on_cache_change(lambda s: None)
        unsubscribe()
        unsubscribe()  # Should not raise

    def test_callback_exception_does_not_block_others(self):
        """A failing callback should not prevent other callbacks from running."""
        results = []

        def good_cb_1(s):
            results.append("first")

        def bad_cb(s):
            raise ValueError("intentional test error")

        def good_cb_2(s):
            results.append("second")

        on_cache_change(good_cb_1)
        on_cache_change(bad_cb)
        on_cache_change(good_cb_2)

        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="compact"))

        assert results == ["first", "second"]

    def test_callback_not_called_on_turn(self):
        """Callbacks should only fire on invalidation, not on turn completion."""
        called = [False]
        on_cache_change(lambda s: called.__setitem__(0, True))
        notify_turn_complete()
        assert not called[0]

    def test_callback_not_called_on_token_usage(self):
        """Callbacks should only fire on invalidation, not on token notifications."""
        called = [False]
        on_cache_change(lambda s: called.__setitem__(0, True))
        notify_token_usage(100)
        assert not called[0]


# === Hook handlers ===


class TestHandleCacheInvalidated:
    def test_sets_invalidation_time(self):
        before = datetime.now(tz=timezone.utc)
        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="compact"))
        after = datetime.now(tz=timezone.utc)

        state = get_cache_state()
        assert state.last_invalidation is not None
        assert before <= state.last_invalidation <= after

    def test_sets_reason(self):
        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="edit"))
        assert get_cache_state().last_invalidation_reason == "edit"

    def test_stores_token_counts(self):
        manager = MagicMock()
        list(
            _handle_cache_invalidated(
                manager, reason="compact", tokens_before=50000, tokens_after=25000
            )
        )
        state = get_cache_state()
        assert state.tokens_before_invalidation == 50000
        assert state.tokens_after_invalidation == 25000

    def test_token_counts_default_none(self):
        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="compact"))
        state = get_cache_state()
        assert state.tokens_before_invalidation is None
        assert state.tokens_after_invalidation is None

    def test_resets_turns_and_tokens(self):
        notify_turn_complete()
        notify_turn_complete()
        notify_token_usage(1000)

        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="compact"))

        assert get_turns_since_invalidation() == 0
        assert get_tokens_since_invalidation() == 0

    def test_yields_nothing(self):
        """Handler is tracking-only, should not produce messages."""
        manager = MagicMock()
        results = list(_handle_cache_invalidated(manager, reason="compact"))
        assert results == []

    def test_successive_invalidations_update_reason(self):
        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="compact"))
        assert get_cache_state().last_invalidation_reason == "compact"

        list(_handle_cache_invalidated(manager, reason="edit"))
        assert get_cache_state().last_invalidation_reason == "edit"

    def test_successive_invalidations_update_tokens(self):
        manager = MagicMock()
        list(
            _handle_cache_invalidated(
                manager, reason="compact", tokens_before=50000, tokens_after=25000
            )
        )
        list(
            _handle_cache_invalidated(
                manager, reason="compact", tokens_before=30000, tokens_after=15000
            )
        )
        state = get_cache_state()
        assert state.tokens_before_invalidation == 30000
        assert state.tokens_after_invalidation == 15000


class TestHandleMessagePostProcess:
    def test_increments_turns(self):
        manager = MagicMock()
        list(_handle_message_post_process(manager))
        assert get_turns_since_invalidation() == 1

    def test_yields_nothing(self):
        manager = MagicMock()
        results = list(_handle_message_post_process(manager))
        assert results == []

    def test_multiple_calls(self):
        manager = MagicMock()
        for _ in range(3):
            list(_handle_message_post_process(manager))
        assert get_turns_since_invalidation() == 3


# === Convenience functions ===


class TestShouldBatchUpdates:
    def test_false_initially(self):
        assert not should_batch_updates()

    def test_false_below_threshold(self):
        for _ in range(9):
            notify_turn_complete()
        assert not should_batch_updates()

    def test_true_at_threshold(self):
        for _ in range(10):
            notify_turn_complete()
        assert should_batch_updates()

    def test_true_above_threshold(self):
        for _ in range(15):
            notify_turn_complete()
        assert should_batch_updates()

    def test_custom_threshold(self):
        for _ in range(3):
            notify_turn_complete()
        assert not should_batch_updates(threshold=5)
        assert should_batch_updates(threshold=3)
        assert should_batch_updates(threshold=1)

    def test_resets_after_invalidation(self):
        for _ in range(10):
            notify_turn_complete()
        assert should_batch_updates()

        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="compact"))
        assert not should_batch_updates()


class TestGetStatusSummary:
    def test_initial_state(self):
        summary = get_status_summary()
        assert isinstance(summary, dict)
        assert summary["invalidation_count"] == 0
        assert summary["turns_since_invalidation"] == 0
        assert summary["tokens_since_invalidation"] == 0
        assert summary["last_invalidation"] is None
        assert summary["last_invalidation_reason"] is None
        assert summary["tokens_before"] is None
        assert summary["tokens_after"] is None

    def test_after_activity(self):
        notify_turn_complete()
        notify_turn_complete()
        notify_token_usage(500)

        manager = MagicMock()
        list(
            _handle_cache_invalidated(
                manager, reason="compact", tokens_before=100000, tokens_after=50000
            )
        )
        notify_turn_complete()
        notify_token_usage(200)

        summary = get_status_summary()
        assert summary["invalidation_count"] == 1
        assert summary["turns_since_invalidation"] == 1
        assert summary["tokens_since_invalidation"] == 200
        assert summary["last_invalidation"] is not None
        assert summary["last_invalidation_reason"] == "compact"
        assert summary["tokens_before"] == 100000
        assert summary["tokens_after"] == 50000

    def test_last_invalidation_is_iso_format(self):
        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="compact"))
        summary = get_status_summary()
        # Should be parseable ISO format
        assert summary["last_invalidation"] is not None
        dt = datetime.fromisoformat(summary["last_invalidation"])
        assert dt.tzinfo is not None  # Should be timezone-aware

    def test_has_all_typed_keys(self):
        """Verify all CacheStatusSummary keys are present."""
        summary = get_status_summary()
        expected_keys = {
            "invalidation_count",
            "turns_since_invalidation",
            "tokens_since_invalidation",
            "last_invalidation",
            "last_invalidation_reason",
            "tokens_before",
            "tokens_after",
            "last_call_completed_at",
            "elapsed_since_last_call",
            "cache_likely_cold",
        }
        assert set(summary.keys()) == expected_keys


# === Integration / lifecycle scenarios ===


class TestLifecycleScenarios:
    def test_full_session_lifecycle(self):
        """Simulate a full session: turns → invalidation → more turns → invalidation."""
        manager = MagicMock()

        # Phase 1: Normal turns
        for _ in range(5):
            notify_turn_complete()
            notify_token_usage(1000)

        assert get_turns_since_invalidation() == 5
        assert get_tokens_since_invalidation() == 5000
        assert is_cache_valid()

        # Phase 2: Auto-compact triggers invalidation
        list(
            _handle_cache_invalidated(
                manager, reason="compact", tokens_before=150000, tokens_after=75000
            )
        )
        assert not is_cache_valid()
        assert get_turns_since_invalidation() == 0
        assert get_tokens_since_invalidation() == 0
        assert get_invalidation_count() == 1

        # Phase 3: More turns after invalidation
        for _ in range(3):
            notify_turn_complete()
            notify_token_usage(2000)

        assert is_cache_valid()
        assert get_turns_since_invalidation() == 3
        assert get_tokens_since_invalidation() == 6000

        # Phase 4: Second invalidation
        list(
            _handle_cache_invalidated(
                manager, reason="edit", tokens_before=80000, tokens_after=40000
            )
        )
        assert get_invalidation_count() == 2
        state = get_cache_state()
        assert state.last_invalidation_reason == "edit"
        assert state.tokens_before_invalidation == 80000

    def test_callback_lifecycle(self):
        """Test registering, firing, and unsubscribing callbacks."""
        log = []
        manager = MagicMock()

        unsub1 = on_cache_change(lambda s: log.append(("cb1", s.invalidation_count)))
        unsub2 = on_cache_change(lambda s: log.append(("cb2", s.invalidation_count)))

        # First invalidation fires both
        list(_handle_cache_invalidated(manager, reason="compact"))
        assert log == [("cb1", 1), ("cb2", 1)]

        # Unsubscribe cb1
        unsub1()
        log.clear()

        # Second invalidation fires only cb2
        list(_handle_cache_invalidated(manager, reason="compact"))
        assert log == [("cb2", 2)]

        unsub2()
        log.clear()

        # Third invalidation fires none
        list(_handle_cache_invalidated(manager, reason="compact"))
        assert log == []

    def test_reset_clears_everything_including_callbacks(self):
        """reset_state() creates a fresh CacheState, callbacks on old state are orphaned."""
        called = [0]
        on_cache_change(lambda s: called.__setitem__(0, called[0] + 1))

        manager = MagicMock()
        list(_handle_cache_invalidated(manager, reason="compact"))
        assert called[0] == 1

        reset_state()

        # After reset, callbacks are on the old state, so they won't fire
        list(_handle_cache_invalidated(manager, reason="compact"))
        # New state has no callbacks registered
        assert get_cache_state()._callbacks == []


# === TTL-based cache coldness heuristic ===


class TestCacheColdHeuristic:
    """Tests for is_cache_likely_cold / get_elapsed_since_last_call."""

    def test_no_prior_call_returns_false(self):
        """With no prior LLM call recorded, assume warm (conservative)."""

        assert is_cache_likely_cold() is False

    def test_get_elapsed_returns_none_before_first_call(self):

        assert get_elapsed_since_last_call() is None

    def test_recent_call_not_cold(self):
        """A call that completed seconds ago should not be considered cold."""
        from datetime import timedelta

        from gptme.hooks.cache_awareness import (
            _get_state,
            _set_state,
        )

        state = _get_state()
        state.last_call_completed_at = datetime.now(tz=timezone.utc) - timedelta(
            seconds=10
        )
        _set_state(state)

        assert is_cache_likely_cold(ttl_seconds=300.0) is False

    def test_old_call_is_cold(self):
        """A call that completed 6+ minutes ago should be considered cold (default 300s TTL)."""
        from datetime import timedelta

        from gptme.hooks.cache_awareness import (
            _get_state,
            _set_state,
        )

        state = _get_state()
        state.last_call_completed_at = datetime.now(tz=timezone.utc) - timedelta(
            seconds=400
        )
        _set_state(state)

        assert is_cache_likely_cold(ttl_seconds=300.0) is True

    def test_custom_ttl_threshold(self):
        """Custom TTL threshold should override the default 300 s."""
        from datetime import timedelta

        from gptme.hooks.cache_awareness import (
            _get_state,
            _set_state,
        )

        state = _get_state()
        state.last_call_completed_at = datetime.now(tz=timezone.utc) - timedelta(
            seconds=60
        )
        _set_state(state)

        assert is_cache_likely_cold(ttl_seconds=30.0) is True
        assert is_cache_likely_cold(ttl_seconds=120.0) is False

    def test_elapsed_since_last_call(self):
        from datetime import timedelta

        from gptme.hooks.cache_awareness import (
            _get_state,
            _set_state,
        )

        state = _get_state()
        state.last_call_completed_at = datetime.now(tz=timezone.utc) - timedelta(
            seconds=90
        )
        _set_state(state)

        elapsed = get_elapsed_since_last_call()
        assert elapsed is not None
        assert 89 <= elapsed <= 92  # allow 2s tolerance

    def test_handle_generation_post_records_time(self):
        """_handle_generation_post() should set last_call_completed_at."""
        from unittest.mock import MagicMock

        from gptme.hooks.cache_awareness import _get_state

        msg = MagicMock()
        before = datetime.now(tz=timezone.utc)
        list(_handle_generation_post(msg))
        after = datetime.now(tz=timezone.utc)

        state = _get_state()
        assert state.last_call_completed_at is not None
        assert before <= state.last_call_completed_at <= after

    def test_status_summary_includes_cold_heuristic(self):
        """get_status_summary() should expose the new TTL fields."""
        from gptme.hooks.cache_awareness import get_status_summary

        summary = get_status_summary()
        assert "last_call_completed_at" in summary
        assert "elapsed_since_last_call" in summary
        assert "cache_likely_cold" in summary
        # No call recorded yet → cold=False, elapsed=None
        assert summary["cache_likely_cold"] is False
        assert summary["elapsed_since_last_call"] is None
        assert summary["last_call_completed_at"] is None
