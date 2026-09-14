"""Tests for the cost tracking system."""

import threading
from pathlib import Path

import pytest

from gptme.util.cost_tracker import (
    CostEntry,
    CostTracker,
    SessionCosts,
    session_id_for_logdir,
)


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
        """Test cache hit rate calculation.

        Cache hit rate = cache_read / (input + cache_read + cache_creation)

        The denominator includes input_tokens because some content is intentionally
        not cached (like single-turn context from hooks that won't be sent again).
        This gives a more accurate picture of overall cache efficiency.
        """
        session = SessionCosts(session_id="test-session")
        session.entries.append(
            CostEntry(
                timestamp=1.0,
                model="claude-sonnet-4-5",
                input_tokens=200,  # Tokens not requested for caching (e.g., hook context)
                output_tokens=100,
                cache_read_tokens=800,  # Cache hits
                cache_creation_tokens=200,  # Cache misses (written to cache)
                cost=0.005,
            )
        )
        # Cache hit rate = cache_read / (input + cache_read + cache_creation)
        # = 800 / (200 + 800 + 200) = 800 / 1200 = 2/3 ≈ 0.6667
        expected = 800 / (200 + 800 + 200)
        assert abs(session.cache_hit_rate - expected) < 0.0001

    def test_record_extra_accumulates(self):
        """Test that record_extra accumulates plugin annotations."""
        session = SessionCosts(session_id="test-session")
        assert session.extras == {}

        session.record_extra(
            "headroom_compressor", compressed_count=3, chars_saved=5000
        )
        session.record_extra(
            "headroom_compressor", compressed_count=1, chars_saved=1200
        )

        assert "headroom_compressor" in session.extras
        entries = session.extras["headroom_compressor"]
        assert len(entries) == 2
        assert entries[0] == {"compressed_count": 3, "chars_saved": 5000}
        assert entries[1] == {"compressed_count": 1, "chars_saved": 1200}

    def test_record_extra_multiple_keys(self):
        """Different plugin keys are stored independently."""
        session = SessionCosts(session_id="test-session")
        session.record_extra("plugin_a", value=1)
        session.record_extra("plugin_b", value=2)

        assert set(session.extras.keys()) == {"plugin_a", "plugin_b"}
        assert session.extras["plugin_a"] == [{"value": 1}]
        assert session.extras["plugin_b"] == [{"value": 2}]


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
        assert CostTracker.get_summary() is None

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
        assert summary is not None
        assert summary.total_cost == 0.015
        assert summary.total_input_tokens == 1000
        assert summary.request_count == 1

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
        assert summary is not None
        # Check that CostSummary has expected attributes
        assert hasattr(summary, "session_id")
        assert hasattr(summary, "total_cost")
        assert hasattr(summary, "total_input_tokens")
        assert hasattr(summary, "total_output_tokens")
        assert hasattr(summary, "cache_read_tokens")
        assert hasattr(summary, "cache_creation_tokens")
        assert hasattr(summary, "cache_hit_rate")
        assert hasattr(summary, "request_count")
        # Verify it's a CostSummary instance
        from gptme.util.cost_tracker import CostSummary

        assert isinstance(summary, CostSummary)

    def test_ensure_session_rebinds_without_reset(self):
        CostTracker.start_session("conv-a")
        first = CostTracker.get_session_costs()
        assert first is not None
        CostTracker._session_costs_var.set(None)
        second = CostTracker.ensure_session("conv-a")
        assert second is first
        assert CostTracker.get_session_costs() is first

    def test_ensure_session_isolates_conversations(self):
        a = CostTracker.ensure_session("conv-a")
        b = CostTracker.ensure_session("conv-b")
        assert a is not b
        CostTracker.record(
            CostEntry(
                timestamp=1.0,
                model="test",
                input_tokens=1,
                output_tokens=1,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.1,
            )
        )
        assert b.request_count == 1
        assert a.request_count == 0
        CostTracker.ensure_session("conv-a")
        assert CostTracker.get_session_costs() is a
        assert a.request_count == 0

    def test_start_session_resets_tracking_id(self):
        CostTracker.start_session("conv-a")
        first = CostTracker.get_session_costs()
        assert first is not None
        CostTracker.start_session("conv-a")
        second = CostTracker.get_session_costs()
        assert second is not None
        assert second is not first
        assert second.tracking_id != first.tracking_id

    def test_replacement_worker_rebinds_same_window(self):
        owner = CostTracker.ensure_session("conv-a")
        seen: list[str] = []

        def worker() -> None:
            CostTracker._session_costs_var.set(None)
            rebound = CostTracker.ensure_session("conv-a")
            seen.append(rebound.tracking_id)
            CostTracker.record(
                CostEntry(
                    timestamp=1.0,
                    model="test",
                    input_tokens=1,
                    output_tokens=0,
                    cache_read_tokens=0,
                    cache_creation_tokens=0,
                    cost=0.0,
                )
            )

        first = threading.Thread(target=worker)
        second = threading.Thread(target=worker)
        first.start()
        first.join()
        second.start()
        second.join()
        assert seen == [owner.tracking_id, owner.tracking_id]
        assert owner.request_count == 2

    def test_end_session_drops_registry_so_recreate_is_fresh(self):
        first = CostTracker.ensure_session("conv-a")
        CostTracker.record(
            CostEntry(
                timestamp=1.0,
                model="test",
                input_tokens=4,
                output_tokens=1,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.25,
            )
        )
        CostTracker.end_session("conv-a")
        assert CostTracker.get_session_costs() is None
        second = CostTracker.ensure_session("conv-a")
        assert second is not first
        assert second.tracking_id != first.tracking_id
        assert second.request_count == 0

    def test_end_session_unknown_id_is_noop(self):
        owner = CostTracker.ensure_session("conv-a")
        CostTracker.end_session("missing")
        assert CostTracker.get_session_costs() is owner

    def test_record_ignores_ended_window_left_in_context(self):
        first = CostTracker.ensure_session("conv-a")
        CostTracker.record(
            CostEntry(
                timestamp=1.0,
                model="test",
                input_tokens=1,
                output_tokens=0,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.1,
            )
        )
        CostTracker.end_session("conv-a")
        CostTracker._session_costs_var.set(first)
        CostTracker.record(
            CostEntry(
                timestamp=2.0,
                model="test",
                input_tokens=9,
                output_tokens=0,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost=0.9,
            )
        )
        assert first.request_count == 1
        second = CostTracker.ensure_session("conv-a")
        assert second is not first
        assert second.request_count == 0

    def test_record_concurrent_with_end_session_does_not_land_on_recreate(self):
        first = CostTracker.ensure_session("conv-a")
        barrier = threading.Barrier(2)
        entry = CostEntry(
            timestamp=1.0,
            model="test",
            input_tokens=3,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost=0.3,
        )

        def worker() -> None:
            CostTracker.attach(first)
            barrier.wait()
            CostTracker.record(entry)

        thread = threading.Thread(target=worker)
        thread.start()
        barrier.wait()
        CostTracker.end_session("conv-a")
        thread.join()
        second = CostTracker.ensure_session("conv-a")
        assert second is not first
        assert second.request_count == 0
        assert first.request_count in (0, 1)

    def test_relative_logdir_identity_is_cwd_independent(self, tmp_path, monkeypatch):
        other = tmp_path / "workspace"
        other.mkdir()
        relative = Path("logs") / "conv-a"
        monkeypatch.chdir(tmp_path)
        first = session_id_for_logdir(relative)
        monkeypatch.chdir(other)
        second = session_id_for_logdir(relative)
        assert first == second == str(relative)

    def test_absolute_logdir_identity_canonicalizes(self, tmp_path):
        logdir = tmp_path / "logs" / "conv-a"
        logdir.mkdir(parents=True)
        assert session_id_for_logdir(logdir) == str(logdir.resolve())
        assert session_id_for_logdir(logdir / ".." / "conv-a") == str(logdir.resolve())

    def test_concurrent_record_keeps_all_entries(self):
        owner = CostTracker.ensure_session("conv-a")
        barrier = threading.Barrier(8)

        def worker() -> None:
            CostTracker.attach(owner)
            barrier.wait()
            CostTracker.record(
                CostEntry(
                    timestamp=1.0,
                    model="test",
                    input_tokens=1,
                    output_tokens=0,
                    cache_read_tokens=0,
                    cache_creation_tokens=0,
                    cost=0.0,
                )
            )

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert owner.request_count == 8
        assert len(owner.snapshot_entries()) == 8
