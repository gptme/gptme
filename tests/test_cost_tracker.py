"""Tests for the cost tracking system."""

import pytest

from gptme.util.cost_tracker import CostEntry, CostTracker, SessionCosts


class TestCostEntry:
    """Tests for CostEntry dataclass."""

    def test_create_entry(self):
        """Test creating a cost entry."""
        entry = CostEntry(
            timestamp=1234567890.0,
            model="claude-sonnet-4-5",
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=800,
            cache_creation_tokens=200,
            cost=0.015,
        )
        assert entry.model == "claude-sonnet-4-5"
        assert entry.input_tokens == 1000
        assert entry.output_tokens == 500
        assert entry.cost == 0.015


class TestSessionCosts:
    """Tests for SessionCosts aggregation."""

    def test_empty_session(self):
        """Test empty session has zero costs."""
        session = SessionCosts(session_id="test-session")
        assert session.total_cost == 0.0
        assert session.total_input_tokens == 0
        assert session.total_output_tokens == 0
        assert session.cache_hit_rate == 0.0
        assert session.request_count == 0

    def test_single_entry(self):
        """Test session with single entry."""
        session = SessionCosts(session_id="test-session")
        session.entries.append(
            CostEntry(
                timestamp=1234567890.0,
                model="claude-sonnet-4-5",
                input_tokens=1000,
                output_tokens=500,
                cache_read_tokens=800,
                cache_creation_tokens=200,
                cost=0.015,
            )
        )
        assert session.total_cost == 0.015
        assert session.total_input_tokens == 1000
        assert session.total_output_tokens == 500
        assert session.request_count == 1

    def test_multiple_entries(self):
        """Test session with multiple entries aggregates correctly."""
        session = SessionCosts(session_id="test-session")
        session.entries.extend(
            [
                CostEntry(
                    timestamp=1.0,
                    model="claude-sonnet-4-5",
                    input_tokens=1000,
                    output_tokens=500,
                    cache_read_tokens=0,
                    cache_creation_tokens=1000,
                    cost=0.010,
                ),
                CostEntry(
                    timestamp=2.0,
                    model="claude-sonnet-4-5",
                    input_tokens=500,
                    output_tokens=300,
                    cache_read_tokens=1000,
                    cache_creation_tokens=0,
                    cost=0.005,
                ),
            ]
        )
        assert session.total_cost == 0.015
        assert session.total_input_tokens == 1500
        assert session.total_output_tokens == 800
        assert session.request_count == 2

    def test_cache_hit_rate(self):
        """Test cache hit rate calculation."""
        session = SessionCosts(session_id="test-session")
        session.entries.append(
            CostEntry(
                timestamp=1.0,
                model="claude-sonnet-4-5",
                input_tokens=200,  # Uncached
                output_tokens=100,
                cache_read_tokens=800,  # Cached
                cache_creation_tokens=0,
                cost=0.005,
            )
        )
        # Cache hit rate = cache_read / (input + cache_read) = 800 / 1000 = 0.8
        assert session.cache_hit_rate == 0.8


class TestCostTracker:
    """Tests for CostTracker context-safe tracking."""

    @pytest.fixture(autouse=True)
    def reset_tracker(self):
        """Reset tracker before each test."""
        CostTracker.reset()
        yield
        CostTracker.reset()

    def test_no_session(self):
        """Test behavior when no session is started."""
        # Recording without session should be safe (no-op)
        CostTracker.record(
            CostEntry(
                timestamp=1.0,
                model="test",
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.001,
            )
        )
        assert CostTracker.get_session_costs() is None
        assert CostTracker.get_summary() == {}

    def test_start_session(self):
        """Test starting a session."""
        CostTracker.start_session("test-session-123")
        costs = CostTracker.get_session_costs()
        assert costs is not None
        assert costs.session_id == "test-session-123"
        assert costs.request_count == 0

    def test_record_entry(self):
        """Test recording an entry."""
        CostTracker.start_session("test-session")
        CostTracker.record(
            CostEntry(
                timestamp=1.0,
                model="claude-sonnet-4-5",
                input_tokens=1000,
                output_tokens=500,
                cache_read_tokens=800,
                cache_creation_tokens=200,
                cost=0.015,
            )
        )
        summary = CostTracker.get_summary()
        assert summary["total_cost"] == 0.015
        assert summary["total_input_tokens"] == 1000
        assert summary["request_count"] == 1

    def test_get_summary_format(self):
        """Test summary dict has expected keys."""
        CostTracker.start_session("test-session")
        CostTracker.record(
            CostEntry(
                timestamp=1.0,
                model="test",
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=80,
                cache_creation_tokens=20,
                cost=0.001,
            )
        )
        summary = CostTracker.get_summary()
        assert "session_id" in summary
        assert "total_cost" in summary
        assert "total_input_tokens" in summary
        assert "total_output_tokens" in summary
        assert "cache_read_tokens" in summary
        assert "cache_creation_tokens" in summary
        assert "cache_hit_rate" in summary
        assert "request_count" in summary
