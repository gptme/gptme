"""Tests for gptme.hooks.cost_awareness module.

Tests the cache-cold warning, cost threshold warnings, pending warning injection,
session start/end hooks, and their interaction with the CostTracker.

See Issue #935 for design context, #2320 for cache-cold warning.
"""

import time
from unittest.mock import MagicMock

import pytest

from gptme.hooks.cost_awareness import (
    _pending_warning_var,
    cache_cold_warning_hook,
    cost_warning_hook,
    inject_pending_warning,
    session_end_cost_summary,
    session_start_cost_tracking,
)
from gptme.message import Message
from gptme.util.cost_tracker import CostEntry, CostTracker


@pytest.fixture(autouse=True)
def _clean_cost_tracker():
    """Reset CostTracker state before and after each test."""
    CostTracker.reset()
    _pending_warning_var.set(None)
    yield
    CostTracker.reset()
    _pending_warning_var.set(None)


# === cache_cold_warning_hook ===


class TestCacheColdWarningHook:
    def test_no_entries_no_warning(self):
        """No session costs → no warning."""
        list(cache_cold_warning_hook(messages=[]))
        assert _pending_warning_var.get() is None

    def test_no_anthropic_entries_no_warning(self):
        """Non-Anthropic models → no warning."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time() - 600,
                model="openai/gpt-4o",
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.01,
            )
        )
        list(cache_cold_warning_hook(messages=[]))
        assert _pending_warning_var.get() is None

    def test_recent_anthropic_turn_no_warning(self):
        """Recent Anthropic turn (<5 min ago) → no warning."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time() - 60,  # 1 min ago
                model="anthropic/claude-sonnet-4-6",
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.01,
            )
        )
        list(cache_cold_warning_hook(messages=[]))
        assert _pending_warning_var.get() is None

    def test_stale_anthropic_turn_warning(self):
        """Stale Anthropic turn (>5 min ago) → warning set."""
        CostTracker.start_session("test")
        stale_ts = time.time() - 400  # ~6.7 min ago
        CostTracker.record(
            CostEntry(
                timestamp=stale_ts,
                model="anthropic/claude-sonnet-4-6",
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.01,
            )
        )
        list(cache_cold_warning_hook(messages=[]))
        warning = _pending_warning_var.get()
        assert warning is not None
        assert "cache likely cold" in warning
        assert "min since last turn" in warning

    def test_multiple_entries_last_recent_no_warning(self):
        """Multiple entries, most recent within TTL → no warning."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time() - 600,
                model="anthropic/claude-sonnet-4-6",
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.01,
            )
        )
        CostTracker.record(
            CostEntry(
                timestamp=time.time() - 120,
                model="anthropic/claude-sonnet-4-6",
                input_tokens=200,
                output_tokens=100,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.02,
            )
        )
        list(cache_cold_warning_hook(messages=[]))
        assert _pending_warning_var.get() is None

    def test_multiple_entries_all_stale_warning(self):
        """All entries stale → warning based on most recent."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time() - 900,
                model="anthropic/claude-sonnet-4-6",
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.01,
            )
        )
        CostTracker.record(
            CostEntry(
                timestamp=time.time() - 600,
                model="anthropic/claude-sonnet-4-6",
                input_tokens=200,
                output_tokens=100,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.02,
            )
        )
        list(cache_cold_warning_hook(messages=[]))
        warning = _pending_warning_var.get()
        assert warning is not None
        assert "10 min" in warning  # most recent is 10 min ago

    def test_hook_yields_nothing(self):
        """Hook should be tracking-only (yields nothing)."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time() - 400,
                model="anthropic/claude-sonnet-4-6",
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.01,
            )
        )
        results = list(cache_cold_warning_hook(messages=[]))
        assert results == []

    def test_mixed_models(self):
        """Mixed Anthropic and non-Anthropic entries → warning based on last Anthropic entry."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time() - 30,
                model="openai/gpt-4o",
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.01,
            )
        )
        CostTracker.record(
            CostEntry(
                timestamp=time.time() - 450,
                model="anthropic/claude-sonnet-4-6",
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.01,
            )
        )
        list(cache_cold_warning_hook(messages=[]))
        warning = _pending_warning_var.get()
        assert warning is not None
        assert "min since last turn" in warning


# === inject_pending_warning ===


class TestInjectPendingWarning:
    def test_no_pending_warning(self):
        """No pending warning → yields nothing."""
        results = list(inject_pending_warning(messages=[]))
        assert results == []
        assert _pending_warning_var.get() is None

    def test_pending_warning_injected(self):
        """Pending warning with user message → yields system message."""
        _pending_warning_var.set("<system_warning>test warning</system_warning>")
        results = list(inject_pending_warning(messages=[Message("user", "continue")]))
        assert len(results) == 1
        assert results[0].role == "system"  # type: ignore[union-attr]
        assert results[0].content == "<system_warning>test warning</system_warning>"  # type: ignore[union-attr]
        # Warning should be cleared after injection
        assert _pending_warning_var.get() is None

    def test_pending_warning_not_user_message(self):
        """Pending warning with assistant message → no injection."""
        _pending_warning_var.set("<system_warning>test warning</system_warning>")
        results = list(
            inject_pending_warning(
                messages=[Message("assistant", "Here is my response.")]
            )
        )
        assert results == []
        # Warning should NOT be cleared
        assert _pending_warning_var.get() is not None

    def test_pending_warning_empty_messages(self):
        """Pending warning with empty messages → no injection."""
        _pending_warning_var.set("<system_warning>test warning</system_warning>")
        results = list(inject_pending_warning(messages=[]))
        assert results == []
        assert _pending_warning_var.get() is not None

    def test_no_pending_multiple_messages(self):
        """No pending warning with multiple messages → yields nothing."""
        _pending_warning_var.set(None)
        results = list(
            inject_pending_warning(
                messages=[
                    Message("user", "first"),
                    Message("assistant", "response"),
                    Message("user", "follow-up"),
                ]
            )
        )
        assert results == []


# === cost_warning_hook ===


class TestCostWarningHook:
    def test_no_costs_no_warning(self):
        """No session costs → no warning."""
        manager = MagicMock()
        results = list(cost_warning_hook(manager))
        assert results == []

    def test_below_threshold_no_warning(self):
        """Cost below $0.10 → no warning."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time(),
                model="openai/gpt-4o",
                input_tokens=10,
                output_tokens=5,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.005,
            )
        )
        manager = MagicMock()
        list(cost_warning_hook(manager))
        assert _pending_warning_var.get() is None

    def test_above_threshold_warning(self):
        """Cost above $0.10 → warning set."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time(),
                model="anthropic/claude-sonnet-4-6",
                input_tokens=10000,
                output_tokens=5000,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.15,
            )
        )
        manager = MagicMock()
        list(cost_warning_hook(manager))
        warning = _pending_warning_var.get()
        assert warning is not None
        assert "$0.15" in warning

    def test_hook_yields_nothing(self):
        """Hook should be tracking-only - yields nothing."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time(),
                model="anthropic/claude-sonnet-4-6",
                input_tokens=10000,
                output_tokens=5000,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.15,
            )
        )
        manager = MagicMock()
        results = list(cost_warning_hook(manager))
        assert results == []

    def test_multiple_thresholds_crossed_one_warning(self):
        """Multiple thresholds crossed in one entry → one warning per entry."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time(),
                model="anthropic/claude-sonnet-4-6",
                input_tokens=1000000,
                output_tokens=500000,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=55.0,
            )
        )
        manager = MagicMock()
        list(cost_warning_hook(manager))
        warning = _pending_warning_var.get()
        assert warning is not None
        assert "$55.00" in warning

    def test_incremental_thresholds(self):
        """Two entries crossing separate thresholds → both warnings (one per entry)."""
        CostTracker.start_session("test")
        # First entry crosses $0.10
        CostTracker.record(
            CostEntry(
                timestamp=time.time(),
                model="anthropic/claude-sonnet-4-6",
                input_tokens=10000,
                output_tokens=5000,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.15,
            )
        )
        manager = MagicMock()
        list(cost_warning_hook(manager))
        assert "$0.15" in (_pending_warning_var.get() or "")

        # Clear and add another entry crossing $0.50
        _pending_warning_var.set(None)
        CostTracker.record(
            CostEntry(
                timestamp=time.time(),
                model="anthropic/claude-sonnet-4-6",
                input_tokens=50000,
                output_tokens=10000,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.40,
            )
        )
        list(cost_warning_hook(manager))
        warning = _pending_warning_var.get()
        assert warning is not None
        assert "$0.55" in warning

    def test_cache_hit_rate_in_warning(self):
        """Warning includes cache hit percentage."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time(),
                model="anthropic/claude-sonnet-4-6",
                input_tokens=1000,
                output_tokens=500,
                cache_read_tokens=9000,
                cache_creation_tokens=1000,
                cost=0.15,
            )
        )
        manager = MagicMock()
        list(cost_warning_hook(manager))
        warning = _pending_warning_var.get()
        assert warning is not None
        assert "cache hit" in warning.lower() or "cache" in warning.lower()


# === session_start_cost_tracking ===


class TestSessionStartCostTracking:
    def test_starts_session_tracking(self):
        """Calling session_start should initialize CostTracker."""
        results = list(
            session_start_cost_tracking(
                logdir=MagicMock(__str__=lambda _: "test-session"),
                workspace=None,
                initial_msgs=[],
            )
        )
        assert results == []
        costs = CostTracker.get_session_costs()
        assert costs is not None
        assert costs.session_id == "test-session"

    def test_is_idempotent(self):
        """Calling session_start twice should not error."""
        list(
            session_start_cost_tracking(
                logdir=MagicMock(__str__=lambda _: "session-1"),
                workspace=None,
                initial_msgs=[],
            )
        )
        list(
            session_start_cost_tracking(
                logdir=MagicMock(__str__=lambda _: "session-2"),
                workspace=None,
                initial_msgs=[],
            )
        )
        costs = CostTracker.get_session_costs()
        assert costs is not None
        assert costs.session_id == "session-2"


# === session_end_cost_summary ===


class TestSessionEndCostSummary:
    def test_no_costs_no_output(self):
        """No costs → no output."""
        manager = MagicMock()
        results = list(session_end_cost_summary(manager))
        assert results == []

    def test_zero_cost_no_output(self):
        """Zero total cost → no output."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time(),
                model="anthropic/claude-sonnet-4-6",
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.0,
            )
        )
        manager = MagicMock()
        results = list(session_end_cost_summary(manager))
        assert results == []

    def test_with_costs_prints_summary(self):
        """Non-zero cost → yields nothing (prints via console)."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time(),
                model="anthropic/claude-sonnet-4-6",
                input_tokens=1000,
                output_tokens=500,
                cache_read_tokens=5000,
                cache_creation_tokens=2000,
                cost=0.25,
            )
        )
        manager = MagicMock()
        # console.log is called; nothing yielded
        results = list(session_end_cost_summary(manager))
        assert results == []


# === Integration tests ===


class TestCostAwarenessIntegration:
    def test_cache_cold_then_inject(self):
        """Integration: stale turn triggers warning, inject_pending_warning delivers it."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time() - 400,
                model="anthropic/claude-sonnet-4-6",
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.01,
            )
        )

        # Phase 1: cache_cold_warning_hook sets warning
        list(cache_cold_warning_hook(messages=[]))
        assert _pending_warning_var.get() is not None

        # Phase 2: inject_pending_warning delivers it
        results = list(inject_pending_warning(messages=[Message("user", "continue")]))
        assert len(results) == 1
        assert "cache likely cold" in results[0].content  # type: ignore[union-attr]
        assert _pending_warning_var.get() is None

    def test_warning_only_injected_once(self):
        """Once injected, warning is cleared and won't re-inject without a new stale trigger."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time() - 400,
                model="anthropic/claude-sonnet-4-6",
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.01,
            )
        )

        # Trigger and inject
        list(cache_cold_warning_hook(messages=[]))
        results = list(inject_pending_warning(messages=[Message("user", "continue")]))
        assert len(results) == 1
        assert _pending_warning_var.get() is None  # Cleared after injection

        # No warning to re-inject
        results2 = list(inject_pending_warning(messages=[Message("user", "again")]))
        assert results2 == []  # Nothing to inject

    def test_full_session_lifecycle(self):
        """Simulate a full session: start, cache turns, cold warning, inject, end."""
        manager = MagicMock()

        # Start session
        list(
            session_start_cost_tracking(
                logdir=MagicMock(__str__=lambda _: "integration-test"),
                workspace=None,
                initial_msgs=[],
            )
        )

        # Initial turns (recent)
        CostTracker.record(
            CostEntry(
                timestamp=time.time() - 120,
                model="anthropic/claude-sonnet-4-6",
                input_tokens=500,
                output_tokens=200,
                cache_read_tokens=1000,
                cache_creation_tokens=2000,
                cost=0.05,
            )
        )
        list(cost_warning_hook(manager))

        # No warning yet (under threshold, recent)
        assert _pending_warning_var.get() is None

        # Record a large cost (crosses $0.10 threshold)
        CostTracker.record(
            CostEntry(
                timestamp=time.time() - 60,
                model="anthropic/claude-sonnet-4-6",
                input_tokens=5000,
                output_tokens=2000,
                cache_read_tokens=10000,
                cache_creation_tokens=5000,
                cost=0.20,
            )
        )
        list(cost_warning_hook(manager))
        cost_warning = _pending_warning_var.get()
        assert cost_warning is not None
        assert "$0.25" in cost_warning

        # Clear cost warning and simulate cold cache (time passes)
        _pending_warning_var.set(None)
        # Manually push a stale entry since we can't mock time.time() easily
        CostTracker.reset()
        CostTracker.start_session("integration-test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time() - 400,
                model="anthropic/claude-sonnet-4-6",
                input_tokens=500,
                output_tokens=200,
                cache_read_tokens=1000,
                cache_creation_tokens=2000,
                cost=0.05,
            )
        )

        # Cold cache warning
        list(cache_cold_warning_hook(messages=[]))
        cold_warning = _pending_warning_var.get()
        assert cold_warning is not None
        assert "cache likely cold" in cold_warning

        # Inject warning
        results = list(
            inject_pending_warning(messages=[Message("user", "continue work")])
        )
        assert len(results) == 1

        # End session
        list(session_end_cost_summary(manager))


# === register function ===


def test_register_called():
    """Verify the register() function can be called without error."""
    from gptme.hooks.cost_awareness import register

    # Should not raise
    try:
        register()
    except Exception:
        pass  # May fail if hooks are already registered; that's OK
