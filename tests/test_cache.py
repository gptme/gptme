"""Unit tests for RAG caching system."""

import time
from datetime import datetime, timedelta

from gptme.tools.cache import CacheEntry, CacheKey, SmartRAGCache


class TestCacheKey:
    """Tests for CacheKey class."""

    def test_basic_creation(self):
        """Test basic cache key creation."""
        key = CacheKey.from_search(
            query="test query",
            workspace_path="/home/user/project",
        )

        assert key.query_text == "test query"
        assert key.workspace_path == "/home/user/project"
        assert key.workspace_only is True
        assert key.embedding_model == "modernbert"
        assert len(key.query_hash) == 32  # MD5 hash length

    def test_query_normalization(self):
        """Test that similar queries produce same hash."""
        key1 = CacheKey.from_search("How to X?")
        key2 = CacheKey.from_search("how to x?")
        key3 = CacheKey.from_search("  how to x?  ")

        # All should have same hash due to normalization
        assert key1.query_hash == key2.query_hash == key3.query_hash

    def test_different_configs_different_keys(self):
        """Test that different configurations produce different keys."""
        key1 = CacheKey.from_search("query", workspace_path="/path1")
        key2 = CacheKey.from_search("query", workspace_path="/path2")

        assert key1 != key2
        assert hash(key1) != hash(key2)

    def test_hashable(self):
        """Test that CacheKey can be used as dict key."""
        key1 = CacheKey.from_search("test")
        key2 = CacheKey.from_search("test")

        # Same query/config should produce equal keys
        assert key1 == key2
        assert hash(key1) == hash(key2)

        # Can use as dict key
        cache_dict = {key1: "value"}
        assert cache_dict[key2] == "value"

    def test_workspace_only_flag(self):
        """Test workspace_only parameter."""
        key1 = CacheKey.from_search("query", workspace_only=True)
        key2 = CacheKey.from_search("query", workspace_only=False)

        assert key1.workspace_only is True
        assert key2.workspace_only is False
        assert key1 != key2


class TestCacheEntry:
    """Tests for CacheEntry class."""

    def test_basic_creation(self):
        """Test basic cache entry creation."""
        now = datetime.now()
        entry = CacheEntry(
            document_ids=["doc1.md", "doc2.md"],
            relevance_scores=[0.9, 0.8],
            created_at=now,
            last_accessed=now,
            access_count=1,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=2,
        )

        assert len(entry.document_ids) == 2
        assert len(entry.relevance_scores) == 2
        assert entry.access_count == 1
        assert entry.result_count == 2

    def test_is_fresh_new_entry(self):
        """Test that new entries are fresh."""
        entry = CacheEntry(
            document_ids=["doc1.md"],
            relevance_scores=[0.9],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=1,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=1,
        )

        assert entry.is_fresh(ttl_seconds=300)

    def test_is_fresh_old_entry(self):
        """Test that old entries are not fresh."""
        old_time = datetime.now() - timedelta(minutes=10)
        entry = CacheEntry(
            document_ids=["doc1.md"],
            relevance_scores=[0.9],
            created_at=old_time,
            last_accessed=old_time,
            access_count=1,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=1,
        )

        # 10 minutes old, TTL is 5 minutes
        assert not entry.is_fresh(ttl_seconds=300)

    def test_is_hot(self):
        """Test hot entry detection."""
        now = datetime.now()

        # Not hot: low access count
        entry_cold = CacheEntry(
            document_ids=["doc1.md"],
            relevance_scores=[0.9],
            created_at=now,
            last_accessed=now,
            access_count=2,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=1,
        )
        assert not entry_cold.is_hot(threshold=5)

        # Hot: high access count
        entry_hot = CacheEntry(
            document_ids=["doc1.md"],
            relevance_scores=[0.9],
            created_at=now,
            last_accessed=now,
            access_count=10,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=1,
        )
        assert entry_hot.is_hot(threshold=5)

    def test_size_bytes(self):
        """Test memory size estimation."""
        entry = CacheEntry(
            document_ids=["doc1.md", "doc2.md", "doc3.md"],
            relevance_scores=[0.9, 0.8, 0.7],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=1,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=3,
        )

        size = entry.size_bytes()
        assert size > 0
        # Should include space for 3 document IDs plus overhead
        assert size > 200  # At least metadata overhead


class TestSmartRAGCache:
    """Tests for SmartRAGCache class."""

    def test_basic_get_put(self):
        """Test basic cache get/put operations."""
        cache = SmartRAGCache(ttl_seconds=300)

        key = CacheKey.from_search("test query")
        entry = CacheEntry(
            document_ids=["doc1.md"],
            relevance_scores=[0.9],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=0,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=1,
        )

        # Initially, cache miss
        assert cache.get(key) is None

        # Put entry
        cache.put(key, entry)

        # Now should hit
        result = cache.get(key)
        assert result is not None
        assert result.document_ids == ["doc1.md"]

    def test_ttl_expiry(self):
        """Test that entries expire after TTL."""
        cache = SmartRAGCache(ttl_seconds=1)  # 1 second TTL

        key = CacheKey.from_search("test query")
        old_time = datetime.now() - timedelta(seconds=2)
        entry = CacheEntry(
            document_ids=["doc1.md"],
            relevance_scores=[0.9],
            created_at=old_time,  # Created 2 seconds ago
            last_accessed=old_time,
            access_count=0,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=1,
        )

        cache.put(key, entry)

        # Should be expired (created 2s ago, TTL is 1s)
        result = cache.get(key)
        assert result is None

        # Check stats
        stats = cache.get_stats()
        assert stats["ttl_evictions"] == 1
        assert stats["misses"] == 1

    def test_lru_eviction(self):
        """Test LRU eviction when memory limit exceeded."""
        # Small cache: 1KB limit
        cache = SmartRAGCache(
            ttl_seconds=300,
            max_memory_bytes=1024,
        )

        # Add entries until memory limit exceeded
        keys = []
        for i in range(10):
            key = CacheKey.from_search(f"query {i}")
            keys.append(key)

            entry = CacheEntry(
                document_ids=[f"doc{i}.md"] * 10,  # Make it take space
                relevance_scores=[0.9] * 10,
                created_at=datetime.now(),
                last_accessed=datetime.now(),
                access_count=0,
                workspace_mtime=time.time(),
                index_mtime=time.time(),
                embedding_time_ms=50.0,
                result_count=10,
            )
            cache.put(key, entry)

        # Should have evicted some entries
        stats = cache.get_stats()
        assert stats["memory_evictions"] > 0
        assert stats["entries"] < 10  # Not all 10 entries fit

    def test_lru_ordering(self):
        """Test that least recently used entries are evicted first."""
        cache = SmartRAGCache(
            ttl_seconds=300,
            max_memory_bytes=4096,  # Larger limit to fit 2-3 entries
        )

        # Add first entry
        key1 = CacheKey.from_search("query 1")
        entry1 = CacheEntry(
            document_ids=["doc1.md"] * 20,
            relevance_scores=[0.9] * 20,
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=0,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=20,
        )
        cache.put(key1, entry1)

        # Add second entry
        key2 = CacheKey.from_search("query 2")
        entry2 = CacheEntry(
            document_ids=["doc2.md"] * 20,
            relevance_scores=[0.8] * 20,
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=0,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=20,
        )
        cache.put(key2, entry2)

        # Access first entry (makes it recently used)
        cache.get(key1)

        # Add third entry (should evict key2, not key1)
        key3 = CacheKey.from_search("query 3")
        entry3 = CacheEntry(
            document_ids=["doc3.md"] * 20,
            relevance_scores=[0.7] * 20,
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=0,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=20,
        )
        cache.put(key3, entry3)

        # key1 should still be in cache (was accessed)
        assert cache.get(key1) is not None

    def test_stats_tracking(self):
        """Test cache statistics tracking."""
        cache = SmartRAGCache()

        key = CacheKey.from_search("test")
        entry = CacheEntry(
            document_ids=["doc1.md"],
            relevance_scores=[0.9],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=0,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=1,
        )

        # Miss
        cache.get(key)

        # Put
        cache.put(key, entry)

        # Hit
        cache.get(key)
        cache.get(key)

        stats = cache.get_stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["entries"] == 1
        assert stats["hit_rate"] == 2 / 3  # 2 hits out of 3 requests

    def test_clear(self):
        """Test cache clearing."""
        cache = SmartRAGCache()

        # Add entry
        key = CacheKey.from_search("test")
        entry = CacheEntry(
            document_ids=["doc1.md"],
            relevance_scores=[0.9],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=0,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=1,
        )
        cache.put(key, entry)

        assert cache.get(key) is not None

        # Clear
        cache.clear()

        assert cache.get(key) is None
        stats = cache.get_stats()
        assert stats["entries"] == 0
        assert stats["total_size_bytes"] == 0

    def test_get_hot_keys(self):
        """Test hot key detection."""
        cache = SmartRAGCache()

        # Add cold entry
        key_cold = CacheKey.from_search("cold query")
        entry_cold = CacheEntry(
            document_ids=["doc1.md"],
            relevance_scores=[0.9],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=2,  # Below threshold
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=1,
        )
        cache.put(key_cold, entry_cold)

        # Add hot entry
        key_hot = CacheKey.from_search("hot query")
        entry_hot = CacheEntry(
            document_ids=["doc2.md"],
            relevance_scores=[0.8],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=10,  # Above threshold
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=1,
        )
        cache.put(key_hot, entry_hot)

        hot_keys = cache.get_hot_keys(threshold=5)
        assert len(hot_keys) == 1
        assert hot_keys[0] == key_hot

    def test_access_count_increment(self):
        """Test that access count increments on each get."""
        cache = SmartRAGCache()

        key = CacheKey.from_search("test")
        entry = CacheEntry(
            document_ids=["doc1.md"],
            relevance_scores=[0.9],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=0,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=1,
        )
        cache.put(key, entry)

        # Get multiple times
        result1 = cache.get(key)
        assert result1 is not None
        assert result1.access_count == 1

        result2 = cache.get(key)
        assert result2 is not None
        assert result2.access_count == 2

        result3 = cache.get(key)
        assert result3 is not None
        assert result3.access_count == 3

    def test_invalidate_workspace(self):
        """Test invalidating all entries for a workspace."""
        cache = SmartRAGCache(ttl_seconds=300)

        # Create entries for multiple workspaces
        workspace1 = "/home/user/project1"
        workspace2 = "/home/user/project2"

        key1 = CacheKey.from_search(
            query="test query 1",
            workspace_path=workspace1,
            workspace_only=True,
            max_tokens=1000,
            min_relevance=0.7,
        )
        key2 = CacheKey.from_search(
            query="test query 2",
            workspace_path=workspace1,
            workspace_only=True,
            max_tokens=1000,
            min_relevance=0.7,
        )
        key3 = CacheKey.from_search(
            query="test query 3",
            workspace_path=workspace2,
            workspace_only=True,
            max_tokens=1000,
            min_relevance=0.7,
        )

        entry = CacheEntry(
            document_ids=["doc1"],
            relevance_scores=[0.9],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=1,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=1,
        )

        cache.put(key1, entry)
        cache.put(key2, entry)
        cache.put(key3, entry)

        assert len(cache.cache) == 3

        # Invalidate workspace1
        removed = cache.invalidate_workspace(workspace1)
        assert removed == 2
        assert len(cache.cache) == 1

        # Only workspace2 entry remains
        assert cache.get(key3) is not None
        assert cache.get(key1) is None
        assert cache.get(key2) is None

    def test_invalidate_file_with_git(self, tmp_path):
        """Test invalidate_file when .git directory exists."""
        cache = SmartRAGCache(ttl_seconds=300)

        # Create a fake git workspace
        workspace = tmp_path / "project"
        workspace.mkdir()
        (workspace / ".git").mkdir()

        # Create a file in the workspace
        file_path = workspace / "src" / "main.py"
        file_path.parent.mkdir()
        file_path.touch()

        # Add cache entries for this workspace
        key = CacheKey.from_search(
            query="test query",
            workspace_path=str(workspace),
            workspace_only=True,
            max_tokens=1000,
            min_relevance=0.7,
        )

        entry = CacheEntry(
            document_ids=["doc1"],
            relevance_scores=[0.9],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=1,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=1,
        )

        cache.put(key, entry)
        assert len(cache.cache) == 1

        # Invalidate by file path
        removed = cache.invalidate_file(str(file_path))
        assert removed == 1
        assert len(cache.cache) == 0

    def test_invalidate_file_fallback(self):
        """Test invalidate_file fallback when no .git directory."""
        cache = SmartRAGCache(ttl_seconds=300)

        workspace = "/home/user/project"
        file_path = f"{workspace}/src/main.py"

        key = CacheKey.from_search(
            query="test query",
            workspace_path=workspace,
            workspace_only=True,
            max_tokens=1000,
            min_relevance=0.7,
        )

        entry = CacheEntry(
            document_ids=["doc1"],
            relevance_scores=[0.9],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=1,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=1,
        )

        cache.put(key, entry)
        assert len(cache.cache) == 1

        # Invalidate by file path (no .git, should use fallback)
        removed = cache.invalidate_file(file_path)
        assert removed == 1
        assert len(cache.cache) == 0

    def test_invalidate_empty(self):
        """Test invalidation with no matching entries."""
        cache = SmartRAGCache(ttl_seconds=300)

        # Add entry for workspace1
        key = CacheKey.from_search(
            query="test query",
            workspace_path="/home/user/workspace1",
            workspace_only=True,
            max_tokens=1000,
            min_relevance=0.7,
        )

        entry = CacheEntry(
            document_ids=["doc1"],
            relevance_scores=[0.9],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=1,
            workspace_mtime=time.time(),
            index_mtime=time.time(),
            embedding_time_ms=50.0,
            result_count=1,
        )

        cache.put(key, entry)

        # Try to invalidate different workspace
        removed = cache.invalidate_workspace("/home/user/workspace2")
        assert removed == 0
        assert len(cache.cache) == 1  # Original entry still there

        # Try to invalidate file in different workspace
        removed = cache.invalidate_file("/home/user/workspace2/file.py")
        assert removed == 0
        assert len(cache.cache) == 1  # Original entry still there
