"""Tests for parallel tool execution."""

import os
import time
from unittest.mock import MagicMock, patch

import pytest

from gptme.message import Message
from gptme.tools.parallel import (
    execute_tools_parallel,
    is_parallel_enabled,
)


class TestIsParallelEnabled:
    """Tests for is_parallel_enabled function."""

    def test_disabled_by_default(self):
        """Parallel should be disabled by default."""
        # Clear any existing env var
        os.environ.pop("GPTME_TOOLUSE_PARALLEL", None)
        with patch("gptme.tools.parallel.get_config") as mock_config:
            mock_config.return_value.get_env_bool.return_value = False
            assert is_parallel_enabled() is False

    def test_enabled_via_env_warns_deprecation(self):
        """Parallel should warn about deprecation when enabled."""
        with patch("gptme.tools.parallel.get_config") as mock_config:
            mock_config.return_value.get_env_bool.return_value = True
            with pytest.warns(DeprecationWarning, match="GPTME_TOOLUSE_PARALLEL"):
                result = is_parallel_enabled()
            assert result is True


class TestExecuteToolsParallel:
    """Tests for parallel tool execution."""

    def test_empty_tooluses(self):
        """Empty list should return empty results."""
        results = execute_tools_parallel([], lambda _: True, None, None)
        assert results == []

    def test_single_tool(self):
        """Single tool should execute and return results."""
        mock_tooluse = MagicMock()
        mock_tooluse.tool = "test_tool"
        mock_tooluse.call_id = "test_id"
        mock_tooluse.execute.return_value = [Message("system", "result")]

        with patch("gptme.tools.parallel.set_config"):
            with patch("gptme.tools.init_tools"):
                results = execute_tools_parallel(
                    [mock_tooluse], lambda _: True, None, None
                )

        assert len(results) == 1
        assert results[0].content == "result"

    def test_multiple_tools_parallel(self):
        """Multiple tools should execute in parallel."""
        execution_times: list[float] = []

        def slow_execute(*args, **kwargs):
            start = time.time()
            time.sleep(0.1)  # 100ms delay
            execution_times.append(time.time() - start)
            return [Message("system", "done")]

        mock_tools = []
        for i in range(3):
            mock_tooluse = MagicMock()
            mock_tooluse.tool = f"tool_{i}"
            mock_tooluse.call_id = f"id_{i}"
            mock_tooluse.execute = slow_execute
            mock_tools.append(mock_tooluse)

        with patch("gptme.tools.parallel.set_config"):
            with patch("gptme.tools.init_tools"):
                start_time = time.time()
                results = execute_tools_parallel(
                    mock_tools, lambda _: True, None, None, max_workers=3
                )
                total_time = time.time() - start_time

        # Should have 3 results
        assert len(results) == 3

        # If parallel, total time should be ~100ms, not ~300ms
        # Allow some overhead, but should be significantly less than sequential
        assert total_time < 0.25, f"Expected parallel execution, took {total_time}s"

    def test_results_maintain_order(self):
        """Results should maintain the order of input tools."""
        mock_tools = []
        for i in range(3):
            mock_tooluse = MagicMock()
            mock_tooluse.tool = f"tool_{i}"
            mock_tooluse.call_id = f"id_{i}"
            mock_tooluse.execute.return_value = [Message("system", f"result_{i}")]
            mock_tools.append(mock_tooluse)

        with patch("gptme.tools.parallel.set_config"):
            with patch("gptme.tools.init_tools"):
                results = execute_tools_parallel(mock_tools, lambda _: True, None, None)

        assert len(results) == 3
        assert results[0].content == "result_0"
        assert results[1].content == "result_1"
        assert results[2].content == "result_2"

    def test_error_handling(self):
        """Errors in one tool should not affect others."""
        mock_tools = []

        # First tool succeeds
        mock_tool1 = MagicMock()
        mock_tool1.tool = "success_tool"
        mock_tool1.call_id = "id_1"
        mock_tool1.execute.return_value = [Message("system", "success")]
        mock_tools.append(mock_tool1)

        # Second tool raises exception
        mock_tool2 = MagicMock()
        mock_tool2.tool = "error_tool"
        mock_tool2.call_id = "id_2"
        mock_tool2.execute.side_effect = RuntimeError("Test error")
        mock_tools.append(mock_tool2)

        with patch("gptme.tools.parallel.set_config"):
            with patch("gptme.tools.init_tools"):
                results = execute_tools_parallel(mock_tools, lambda _: True, None, None)

        # Both tools should have results
        assert len(results) == 2
        assert results[0].content == "success"
        assert "Error" in results[1].content
