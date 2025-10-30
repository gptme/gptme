"""Integration tests for BackgroundRefresher."""

import time
from datetime import datetime

from gptme.tools.cache import BackgroundRefresher, CacheEntry, CacheKey, SmartRAGCache


class TestBackgroundRefresher:
    """Tests for background refresh functionality."""

    def test_basic_start_stop(self):
        """Test starting and stopping the background refresher."""
        cache = SmartRAGCache()

        # Mock refresh callback
        def mock_refresh(key: CacheKey) -> tuple[list[str], list[float]]:
            return (["doc1"], [0.9])

        refresher = BackgroundRefresher(
            cache, mock_refresh, refresh_interval_seconds=60
        )

        # Start refresher
        refresher.start()
        assert refresher._thread is not None
        assert refresher._thread.is_alive()

        # Stop refresher
        refresher.stop(timeout=2.0)
        assert refresher._thread is None or not refresher._thread.is_alive()

    def test_hot_query_refresh(self):
        """Test that hot queries get refreshed."""
        cache = SmartRAGCache()

        # Track refresh calls
        refresh_calls = []

        def mock_refresh(key: CacheKey) -> tuple[list[str], list[float]]:
            refresh_calls.append(key.query_text)
            return ([f"refreshed_{key.query_text}"], [0.95])

        # Create hot query (5+ accesses)
        key = CacheKey.from_search("hot query", "/tmp")
        entry = CacheEntry(
            document_ids=["doc1"],
            relevance_scores=[0.8],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=10,  # Hot query
            workspace_mtime=0.0,
            index_mtime=0.0,
            embedding_time_ms=100.0,
            result_count=1,
        )
        cache.put(key, entry)

        # Start refresher with short interval
        refresher = BackgroundRefresher(
            cache, mock_refresh, refresh_interval_seconds=1, hot_threshold=5
        )
        refresher.start()

        # Wait for refresh cycle
        time.sleep(1.5)

        # Stop refresher
        refresher.stop(timeout=2.0)

        # Verify refresh was called
        assert len(refresh_calls) >= 1
        assert "hot query" in refresh_calls

        # Verify cache was updated
        refreshed_entry = cache.get(key)
        assert refreshed_entry is not None
        assert refreshed_entry.document_ids == ["refreshed_hot query"]

    def test_non_hot_query_not_refreshed(self):
        """Test that non-hot queries are not refreshed."""
        cache = SmartRAGCache()

        # Track refresh calls
        refresh_calls = []

        def mock_refresh(key: CacheKey) -> tuple[list[str], list[float]]:
            refresh_calls.append(key.query_text)
            return (["refreshed"], [0.95])

        # Create non-hot query (below threshold)
        key = CacheKey.from_search("cold query", "/tmp")
        entry = CacheEntry(
            document_ids=["doc1"],
            relevance_scores=[0.8],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=2,  # Not hot
            workspace_mtime=0.0,
            index_mtime=0.0,
            embedding_time_ms=100.0,
            result_count=1,
        )
        cache.put(key, entry)

        # Start refresher
        refresher = BackgroundRefresher(
            cache, mock_refresh, refresh_interval_seconds=1, hot_threshold=5
        )
        refresher.start()

        # Wait for refresh cycle
        time.sleep(1.5)

        # Stop refresher
        refresher.stop(timeout=2.0)

        # Verify refresh was NOT called
        assert len(refresh_calls) == 0

    def test_multiple_hot_queries(self):
        """Test refreshing multiple hot queries."""
        cache = SmartRAGCache()

        # Track refresh calls
        refresh_calls = []

        def mock_refresh(key: CacheKey) -> tuple[list[str], list[float]]:
            refresh_calls.append(key.query_text)
            return ([f"refreshed_{key.query_text}"], [0.95])

        # Create multiple hot queries
        for i in range(3):
            key = CacheKey.from_search(f"hot query {i}", "/tmp")
            entry = CacheEntry(
                document_ids=[f"doc{i}"],
                relevance_scores=[0.8],
                created_at=datetime.now(),
                last_accessed=datetime.now(),
                access_count=10,  # Hot
                workspace_mtime=0.0,
                index_mtime=0.0,
                embedding_time_ms=100.0,
                result_count=1,
            )
            cache.put(key, entry)

        # Start refresher
        refresher = BackgroundRefresher(
            cache, mock_refresh, refresh_interval_seconds=1, hot_threshold=5
        )
        refresher.start()

        # Wait for refresh cycle
        time.sleep(1.5)

        # Stop refresher
        refresher.stop(timeout=2.0)

        # Verify all hot queries were refreshed
        assert len(refresh_calls) >= 3
        for i in range(3):
            assert f"hot query {i}" in refresh_calls

    def test_refresh_error_handling(self):
        """Test that errors during refresh don't crash the thread."""
        cache = SmartRAGCache()

        # Mock refresh callback that raises error
        call_count = [0]

        def failing_refresh(key: CacheKey) -> tuple[list[str], list[float]]:
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("Simulated refresh error")
            return (["doc1"], [0.9])

        # Create hot query
        key = CacheKey.from_search("query", "/tmp")
        entry = CacheEntry(
            document_ids=["doc1"],
            relevance_scores=[0.8],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=10,
            workspace_mtime=0.0,
            index_mtime=0.0,
            embedding_time_ms=100.0,
            result_count=1,
        )
        cache.put(key, entry)

        # Start refresher
        refresher = BackgroundRefresher(
            cache, failing_refresh, refresh_interval_seconds=1, hot_threshold=5
        )
        refresher.start()

        # Wait for multiple refresh cycles
        time.sleep(2.0)

        # Stop refresher
        refresher.stop(timeout=2.0)

        # Thread should still be stopped cleanly despite error
        assert refresher._thread is None or not refresher._thread.is_alive()
        # Should have attempted refresh at least twice
        assert call_count[0] >= 2

    def test_graceful_shutdown_during_refresh(self):
        """Test that stop() works even during active refresh."""
        cache = SmartRAGCache()

        # Mock slow refresh
        def slow_refresh(key: CacheKey) -> tuple[list[str], list[float]]:
            time.sleep(2.0)  # Slow operation
            return (["doc1"], [0.9])

        # Create hot query
        key = CacheKey.from_search("query", "/tmp")
        entry = CacheEntry(
            document_ids=["doc1"],
            relevance_scores=[0.8],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=10,
            workspace_mtime=0.0,
            index_mtime=0.0,
            embedding_time_ms=100.0,
            result_count=1,
        )
        cache.put(key, entry)

        # Start refresher with short interval
        refresher = BackgroundRefresher(
            cache, slow_refresh, refresh_interval_seconds=1, hot_threshold=5
        )
        refresher.start()

        # Wait a bit then stop immediately
        time.sleep(0.2)
        refresher.stop(timeout=3.0)

        # Should stop cleanly
        assert refresher._thread is None or not refresher._thread.is_alive()

    def test_thread_safety(self):
        """Test thread-safe operation with concurrent access."""
        cache = SmartRAGCache()

        # Track refresh calls
        refresh_calls = []

        def mock_refresh(key: CacheKey) -> tuple[list[str], list[float]]:
            refresh_calls.append(key.query_text)
            time.sleep(0.1)  # Simulate work
            return ([f"refreshed_{key.query_text}"], [0.95])

        # Create hot query
        key = CacheKey.from_search("query", "/tmp")
        entry = CacheEntry(
            document_ids=["doc1"],
            relevance_scores=[0.8],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=10,
            workspace_mtime=0.0,
            index_mtime=0.0,
            embedding_time_ms=100.0,
            result_count=1,
        )
        cache.put(key, entry)

        # Start refresher
        refresher = BackgroundRefresher(
            cache, mock_refresh, refresh_interval_seconds=1, hot_threshold=5
        )
        refresher.start()

        # Simulate concurrent cache access
        for _ in range(10):
            cache.get(key)
            time.sleep(0.05)

        # Stop refresher
        refresher.stop(timeout=2.0)

        # No crashes = thread-safe ✓
        assert refresher._thread is None or not refresher._thread.is_alive()
