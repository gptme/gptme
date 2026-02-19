"""Tests for gptme.util.tree — workspace file listing with fallback methods."""

from unittest.mock import MagicMock, patch

from gptme.util.tree import _reduce_tree_output_by_depth, get_tree_output


class TestReduceTreeOutputByDepth:
    """Tests for the pure _reduce_tree_output_by_depth function."""

    def test_empty_output(self):
        """Empty input returns empty output."""
        assert _reduce_tree_output_by_depth("") == ""

    def test_single_line(self):
        """Single line (no slashes) is always within budget."""
        assert _reduce_tree_output_by_depth("README.md") == "README.md"

    def test_small_output_returned_as_is(self):
        """Output already within budget is returned unchanged."""
        output = "src/main.py\nsrc/util.py\nREADME.md"
        assert _reduce_tree_output_by_depth(output, budget=1000) == output

    def test_filters_by_depth(self):
        """Deep paths are filtered out when output exceeds budget."""
        lines = [
            "README.md",  # depth 0
            "src/main.py",  # depth 1
            "src/util/helpers.py",  # depth 2
            "src/util/deep/nested.py",  # depth 3
        ]
        output = "\n".join(lines)

        # Budget that fits depth 0+1 but not all
        result = _reduce_tree_output_by_depth(output, budget=30)
        assert result is not None
        assert "README.md" in result
        assert "src/main.py" in result
        assert "src/util/deep/nested.py" not in result

    def test_returns_none_when_depth_zero_exceeds_budget(self):
        """Returns None when even depth-0 files exceed budget."""
        # Many depth-0 files that exceed a tiny budget
        lines = [f"file_{i}.txt" for i in range(100)]
        output = "\n".join(lines)
        result = _reduce_tree_output_by_depth(output, budget=10)
        assert result is None

    def test_progressive_depth_reduction(self):
        """Verifies progressive reduction keeps shallowest files."""
        lines = [
            "a.txt",  # depth 0
            "b.txt",  # depth 0
            "d1/c.txt",  # depth 1
            "d1/d2/d.txt",  # depth 2
            "d1/d2/d3/e.txt",  # depth 3
        ]
        output = "\n".join(lines)

        # Budget that fits exactly depth 0+1 lines
        depth01 = "a.txt\nb.txt\nd1/c.txt"
        budget = len(depth01) + 1
        result = _reduce_tree_output_by_depth(output, budget=budget)
        assert result is not None
        assert "a.txt" in result
        assert "b.txt" in result
        assert "d1/c.txt" in result
        assert "d1/d2/d.txt" not in result

    def test_default_budget_is_20000(self):
        """Default budget allows reasonable-sized output through."""
        # Generate output just under 20000 chars
        lines = [f"src/module_{i}/file.py" for i in range(500)]
        output = "\n".join(lines)
        assert len(output) < 20000
        result = _reduce_tree_output_by_depth(output)
        assert result == output


class TestGetTreeOutput:
    """Tests for get_tree_output with mocked subprocess calls."""

    @patch("gptme.util.tree.get_config")
    def test_disabled_by_config(self, mock_config):
        """Returns None when GPTME_CONTEXT_TREE is disabled."""
        mock_cfg = MagicMock()
        mock_cfg.get_env_bool.return_value = False
        mock_config.return_value = mock_cfg

        from pathlib import Path

        result = get_tree_output(Path("/tmp"))
        assert result is None

    @patch("gptme.util.tree.subprocess.run")
    @patch("gptme.util.tree.get_config")
    def test_git_method_in_git_repo(self, mock_config, mock_run):
        """Uses git ls-files in a git repository."""
        mock_cfg = MagicMock()
        mock_cfg.get_env_bool.return_value = True
        mock_config.return_value = mock_cfg

        # First call: git rev-parse (in git repo)
        # Second call: git ls-files (the actual listing)
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="true", stderr=""),
            MagicMock(returncode=0, stdout="README.md\nsrc/main.py", stderr=""),
        ]

        from pathlib import Path

        result = get_tree_output(Path("/tmp/repo"), method="git")
        assert result == "README.md\nsrc/main.py"
        # Verify git ls-files was called
        assert "git" in mock_run.call_args_list[1].args[0][0]

    @patch("gptme.util.tree.subprocess.run")
    @patch("gptme.util.tree.get_config")
    def test_fallback_to_ls_outside_git(self, mock_config, mock_run):
        """Falls back to ls when not in a git repository."""
        mock_cfg = MagicMock()
        mock_cfg.get_env_bool.return_value = True
        mock_config.return_value = mock_cfg

        # First call: git rev-parse (not in git repo)
        # Second call: ls -R (fallback)
        mock_run.side_effect = [
            MagicMock(returncode=128, stdout="", stderr="not a git repo"),
            MagicMock(returncode=0, stdout="file1.txt\nfile2.txt", stderr=""),
        ]

        from pathlib import Path

        result = get_tree_output(Path("/tmp/non-repo"), method="git")
        assert result == "file1.txt\nfile2.txt"

    @patch("gptme.util.tree.subprocess.run")
    @patch("gptme.util.tree.get_config")
    def test_fallback_on_method_failure(self, mock_config, mock_run):
        """Falls back to next method when preferred method fails."""
        mock_cfg = MagicMock()
        mock_cfg.get_env_bool.return_value = True
        mock_config.return_value = mock_cfg

        # First call: git rev-parse (in git repo)
        # Second call: git ls-files fails
        # Third call: tree fails
        # Fourth call: ls succeeds
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="true", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="git error"),
            MagicMock(returncode=1, stdout="", stderr="tree error"),
            MagicMock(returncode=0, stdout="fallback.txt", stderr=""),
        ]

        from pathlib import Path

        result = get_tree_output(Path("/tmp/repo"), method="git")
        assert result == "fallback.txt"

    @patch("gptme.util.tree.subprocess.run")
    @patch("gptme.util.tree.get_config")
    def test_timeout_triggers_fallback(self, mock_config, mock_run):
        """Timeout on one method triggers fallback to next."""
        mock_cfg = MagicMock()
        mock_cfg.get_env_bool.return_value = True
        mock_config.return_value = mock_cfg

        import subprocess as sp

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="true", stderr=""),  # git check
            sp.TimeoutExpired(cmd="git ls-files", timeout=5),  # git timeout
            MagicMock(returncode=0, stdout="tree_output.py", stderr=""),  # tree works
        ]

        from pathlib import Path

        result = get_tree_output(Path("/tmp/repo"), method="git")
        assert result == "tree_output.py"

    @patch("gptme.util.tree.subprocess.run")
    @patch("gptme.util.tree.get_config")
    def test_large_output_gets_filtered(self, mock_config, mock_run):
        """Large output is filtered by depth reduction."""
        mock_cfg = MagicMock()
        mock_cfg.get_env_bool.return_value = True
        mock_config.return_value = mock_cfg

        # Generate output > 20000 chars with varying depths
        shallow_lines = [f"file_{i}.txt" for i in range(10)]
        deep_lines = [f"a/b/c/d/e/f/g/file_{i}.txt" for i in range(2000)]
        big_output = "\n".join(shallow_lines + deep_lines)
        assert len(big_output) > 20000

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="true", stderr=""),
            MagicMock(returncode=0, stdout=big_output, stderr=""),
        ]

        from pathlib import Path

        result = get_tree_output(Path("/tmp/repo"), method="git")
        assert result is not None
        # Shallow files should be included
        assert "file_0.txt" in result
        # Should be under budget
        assert len(result) <= 20000

    @patch("gptme.util.tree.subprocess.run")
    @patch("gptme.util.tree.get_config")
    def test_all_methods_fail_returns_none(self, mock_config, mock_run):
        """Returns None when all methods fail."""
        mock_cfg = MagicMock()
        mock_cfg.get_env_bool.return_value = True
        mock_config.return_value = mock_cfg

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="true", stderr=""),  # in git repo
            MagicMock(returncode=1, stdout="", stderr="git failed"),
            MagicMock(returncode=1, stdout="", stderr="tree failed"),
            MagicMock(returncode=1, stdout="", stderr="ls failed"),
        ]

        from pathlib import Path

        result = get_tree_output(Path("/tmp/repo"), method="git")
        assert result is None
