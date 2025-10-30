"""Tests for RAG cache integration."""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from gptme.config import RagConfig
from gptme.tools.cache import CacheKey
from gptme.tools.rag import (
    _get_cache,
    _get_index_mtime,
    _get_workspace_mtime,
    get_rag_context,
)


class TestRagCacheIntegration:
    """Test RAG cache integration."""

    def test_workspace_mtime_tracking(self, tmp_path):
        """Test workspace modification time tracking."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # Get initial mtime
        mtime1 = _get_workspace_mtime(str(workspace))
        assert mtime1 > 0

        # Modify workspace
        time.sleep(0.1)
        (workspace / "file.txt").write_text("test")

        # Mtime should be different (or same if directory mtime not updated)
        mtime2 = _get_workspace_mtime(str(workspace))
        assert mtime2 >= mtime1  # May be same on some filesystems

    def test_index_mtime_nonexistent(self, tmp_path):
        """Test index mtime for non-existent index."""
        workspace = tmp_path / "workspace"
        mtime = _get_index_mtime(str(workspace))
        assert mtime == 0.0

    def test_cache_key_from_rag_config(self):
        """Test cache key creation from RAG config."""
        config = RagConfig(
            enabled=True,
            workspace_only=True,
            max_tokens=5000,
            min_relevance=0.5,
        )

        key = CacheKey.from_search(
            query="test query",
            workspace_path="/tmp/test",
            workspace_only=config.workspace_only,
            max_tokens=config.max_tokens or 3000,
            min_relevance=config.min_relevance or 0.0,
        )

        assert key.query_text == "test query"
        assert key.workspace_only is True
        assert key.max_tokens == 5000
        assert key.min_relevance == 0.5

    @patch("gptme.tools.rag._run_rag_cmd")
    def test_get_rag_context_caching(self, mock_run_cmd):
        """Test that get_rag_context uses caching correctly."""
        # Setup mock
        mock_result = MagicMock()
        mock_result.stdout = "test result"
        mock_run_cmd.return_value = mock_result

        config = RagConfig(
            enabled=True,
            workspace_only=True,
            max_tokens=3000,
            min_relevance=0.0,
            post_process=False,
        )

        # Clear cache
        cache = _get_cache()
        cache.clear()

        # First call - cache miss
        msg1 = get_rag_context("test query", config, Path("/tmp/test"))
        assert mock_run_cmd.call_count == 1
        assert "test result" in msg1.content

        # Second call - cache hit
        msg2 = get_rag_context("test query", config, Path("/tmp/test"))
        assert mock_run_cmd.call_count == 1  # No additional call
        assert "test result" in msg2.content

        # Check cache stats
        stats = cache.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1

    @patch("gptme.tools.rag._run_rag_cmd")
    def test_cache_invalidation_on_config_change(self, mock_run_cmd):
        """Test that cache invalidates when config changes."""
        # Setup mock
        mock_result = MagicMock()
        mock_result.stdout = "test result"
        mock_run_cmd.return_value = mock_result

        config1 = RagConfig(
            enabled=True,
            workspace_only=True,
            max_tokens=3000,
            post_process=False,
        )

        config2 = RagConfig(
            enabled=True,
            workspace_only=True,
            max_tokens=5000,  # Different!
            post_process=False,
        )

        # Clear cache
        cache = _get_cache()
        cache.clear()

        # First call with config1
        get_rag_context("test query", config1, Path("/tmp/test"))
        assert mock_run_cmd.call_count == 1

        # Second call with config2 - should be cache miss due to different config
        get_rag_context("test query", config2, Path("/tmp/test"))
        assert mock_run_cmd.call_count == 2

        # Check cache has 2 entries (different keys)
        stats = cache.get_stats()
        assert stats["entries"] == 2

    @patch("gptme.tools.rag._run_rag_cmd")
    def test_cache_ttl_expiry(self, mock_run_cmd):
        """Test that cache expires after TTL."""
        # Setup mock
        mock_result = MagicMock()
        mock_result.stdout = "test result"
        mock_run_cmd.return_value = mock_result

        config = RagConfig(
            enabled=True,
            workspace_only=True,
            post_process=False,
        )

        # Clear cache and set short TTL
        cache = _get_cache()
        cache.clear()
        cache.ttl_seconds = 1  # 1 second TTL

        # First call
        get_rag_context("test query", config, Path("/tmp/test"))
        assert mock_run_cmd.call_count == 1

        # Wait for TTL to expire
        time.sleep(1.1)

        # Second call - should be cache miss due to expiry
        get_rag_context("test query", config, Path("/tmp/test"))
        assert mock_run_cmd.call_count == 2  # Re-executed
