"""Tests for cost_awareness hook."""

import time
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from gptme.hooks.cost_awareness import (
    COST_WARNING_THRESHOLDS,
    _pending_warning_var,
    cost_warning_hook,
    inject_pending_warning,
    session_end_cost_summary,
    session_start_cost_tracking,
)
from gptme.message import Message
from gptme.util.cost_tracker import CostEntry, CostTracker


@pytest.fixture(autouse=True)
def reset_cost_state():
    """Reset cost tracker and pending warning before each test."""
    CostTracker.reset()
    _pending_warning_var.set(None)
    yield
    CostTracker.reset()
    _pending_warning_var.set(None)


class TestSessionStartCostTracking:
    """Tests for session_start_cost_tracking hook."""

    def test_initializes_cost_tracking(self, tmp_path: Path):
        """Cost tracking is initialized with session ID from logdir."""
        logdir = tmp_path / "session-1"
        list(session_start_cost_tracking(logdir, None, []))

        costs = CostTracker.get_session_costs()
        assert costs is not None
        assert costs.session_id == str(logdir)
        assert costs.entries == []

    def test_yields_nothing(self, tmp_path: Path):
        """Session start hook yields no messages."""
        logdir = tmp_path / "session-2"
        msgs = list(session_start_cost_tracking(logdir, None, []))
        assert msgs == []

    def test_with_workspace(self, tmp_path: Path):
        """Works with workspace parameter provided."""
        logdir = tmp_path / "logs"
        workspace = tmp_path / "workspace"
        list(session_start_cost_tracking(logdir, workspace, []))

        costs = CostTracker.get_session_costs()
        assert costs is not None

    def test_with_initial_messages(self, tmp_path: Path):
        """Works with initial messages parameter."""
        logdir = tmp_path / "logs"
        initial = [Message("user", "hello")]
        list(session_start_cost_tracking(logdir, None, initial))

        costs = CostTracker.get_session_costs()
        assert costs is not None


class TestCostWarningHook:
    """Tests for cost_warning_hook."""

    def _make_manager(self) -> Any:
        """Create a minimal LogManager mock (duck-typed for testing)."""
        from types import SimpleNamespace

        return SimpleNamespace(log=[])

    def _record_cost(
        self, cost: float, input_tokens: int = 100, output_tokens: int = 50
    ):
        """Record a cost entry in the tracker."""
        CostTracker.record(
            CostEntry(
                timestamp=time.time(),
                model="test-model",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=cost,
            )
        )

    def test_no_costs_recorded(self):
        """No warning when no costs have been recorded."""
        # No session started
        manager = self._make_manager()
        msgs = list(cost_warning_hook(manager))
        assert msgs == []

    def test_no_entries(self, tmp_path: Path):
        """No warning when session started but no entries."""
        CostTracker.start_session("test")
        manager = self._make_manager()
        msgs = list(cost_warning_hook(manager))
        assert msgs == []

    def test_below_first_threshold(self, tmp_path: Path):
        """No warning when cost is below first threshold ($0.10)."""
        CostTracker.start_session("test")
        self._record_cost(0.05)
        manager = self._make_manager()
        msgs = list(cost_warning_hook(manager))
        assert msgs == []
        # No pending warning
        assert _pending_warning_var.get() is None

    def test_crosses_first_threshold(self):
        """Warning stored when cost crosses $0.10 threshold."""
        CostTracker.start_session("test")
        self._record_cost(0.05)  # Below threshold
        list(cost_warning_hook(self._make_manager()))
        assert _pending_warning_var.get() is None

        self._record_cost(0.06)  # Total: 0.11 → crosses $0.10
        list(cost_warning_hook(self._make_manager()))
        warning = _pending_warning_var.get()
        assert warning is not None
        assert "$0.11" in warning
        assert "system_warning" in warning

    def test_crosses_multiple_thresholds_one_at_a_time(self):
        """Only one warning per request even if multiple thresholds crossed."""
        CostTracker.start_session("test")
        # Jump from $0 to $0.60 in one request → crosses $0.10, $0.50
        # But only the first crossed threshold ($0.10) triggers
        self._record_cost(0.60)
        list(cost_warning_hook(self._make_manager()))
        warning = _pending_warning_var.get()
        assert warning is not None
        assert "$0.60" in warning

    def test_warning_includes_token_counts(self):
        """Warning text includes token count information."""
        CostTracker.start_session("test")
        self._record_cost(0.15, input_tokens=500, output_tokens=200)
        list(cost_warning_hook(self._make_manager()))
        warning = _pending_warning_var.get()
        assert warning is not None
        assert "500" in warning
        assert "200" in warning

    def test_warning_includes_cache_hit_rate(self):
        """Warning text includes cache hit percentage."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time(),
                model="test",
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=400,
                cache_creation_tokens=0,
                cost=0.15,
            )
        )
        list(cost_warning_hook(self._make_manager()))
        warning = _pending_warning_var.get()
        assert warning is not None
        assert "cache hit:" in warning

    def test_yields_nothing(self):
        """Hook yields no messages (warning is stored, not yielded)."""
        CostTracker.start_session("test")
        self._record_cost(0.15)
        msgs = list(cost_warning_hook(self._make_manager()))
        assert msgs == []

    def test_threshold_values_are_sorted(self):
        """Verify COST_WARNING_THRESHOLDS are in ascending order."""
        for i in range(len(COST_WARNING_THRESHOLDS) - 1):
            assert COST_WARNING_THRESHOLDS[i] < COST_WARNING_THRESHOLDS[i + 1]


class TestInjectPendingWarning:
    """Tests for inject_pending_warning hook."""

    def test_no_pending_warning(self):
        """No message yielded when no pending warning."""
        messages = [Message("user", "hello")]
        msgs = list(inject_pending_warning(messages))
        assert msgs == []

    def test_injects_warning_after_user_message(self):
        """Injects pending warning when last message is from user."""
        _pending_warning_var.set("<system_warning>Cost reached $1.00</system_warning>")
        messages = [Message("user", "hello")]
        msgs = list(inject_pending_warning(messages))
        assert len(msgs) == 1
        msg = cast(Message, msgs[0])
        assert msg.role == "system"
        assert "Cost reached $1.00" in msg.content
        assert msg.hide is True

    def test_clears_warning_after_injection(self):
        """Pending warning is cleared after being injected."""
        _pending_warning_var.set("<system_warning>test</system_warning>")
        messages = [Message("user", "hello")]
        list(inject_pending_warning(messages))
        assert _pending_warning_var.get() is None

    def test_does_not_inject_after_assistant_message(self):
        """No injection when last message is from assistant."""
        _pending_warning_var.set("<system_warning>test</system_warning>")
        messages = [Message("assistant", "response")]
        msgs = list(inject_pending_warning(messages))
        assert msgs == []
        # Warning is NOT cleared
        assert _pending_warning_var.get() is not None

    def test_does_not_inject_after_system_message(self):
        """No injection when last message is from system."""
        _pending_warning_var.set("<system_warning>test</system_warning>")
        messages = [Message("system", "info")]
        msgs = list(inject_pending_warning(messages))
        assert msgs == []

    def test_empty_messages(self):
        """No injection when messages list is empty."""
        _pending_warning_var.set("<system_warning>test</system_warning>")
        msgs = list(inject_pending_warning([]))
        assert msgs == []


class TestSessionEndCostSummary:
    """Tests for session_end_cost_summary hook."""

    def _make_manager(self) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(log=[])

    def test_no_session(self):
        """No output when no session exists."""
        msgs = list(session_end_cost_summary(self._make_manager()))
        assert msgs == []

    def test_no_entries(self):
        """No output when session has no entries."""
        CostTracker.start_session("test")
        msgs = list(session_end_cost_summary(self._make_manager()))
        assert msgs == []

    def test_zero_cost(self):
        """No output when total cost is zero."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time(),
                model="test",
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.0,
            )
        )
        msgs = list(session_end_cost_summary(self._make_manager()))
        assert msgs == []

    def test_prints_summary(self):
        """Prints summary to console when costs exist."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time(),
                model="test",
                input_tokens=1000,
                output_tokens=500,
                cache_read_tokens=800,
                cache_creation_tokens=200,
                cost=0.05,
            )
        )

        with patch("gptme.util.console.log") as mock_log:
            msgs = list(session_end_cost_summary(self._make_manager()))
            mock_log.assert_called_once()
            log_msg = mock_log.call_args[0][0]
            assert "$0.05" in log_msg
            assert "1 turns" in log_msg

        assert msgs == []

    def test_yields_nothing(self):
        """Session end hook yields no messages."""
        CostTracker.start_session("test")
        CostTracker.record(
            CostEntry(
                timestamp=time.time(),
                model="test",
                input_tokens=5000,
                output_tokens=2000,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.10,
            )
        )

        with patch("gptme.util.console.log"):
            msgs = list(session_end_cost_summary(self._make_manager()))
        assert msgs == []
