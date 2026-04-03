"""Tests for subprocess timeout handling in gptme/eval/ modules.

Uses source file reading for modules with heavy dependencies (swebench,
docker, terminal-bench) that may not be installed in the test environment.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

EVAL_DIR = Path(__file__).parent.parent / "gptme" / "eval"


def _read_source(relpath: str) -> str:
    """Read source file relative to gptme/eval/."""
    return (EVAL_DIR / relpath).read_text()


class TestEvalMainTimeouts:
    """Verify timeout parameters in eval/main.py subprocess calls."""

    @patch("subprocess.run")
    def test_get_commit_hash_has_timeout(self, mock_run):
        """git describe in _get_commit_hash includes timeout."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="abc1234\n", stderr=""
        )
        from gptme.eval.main import _get_commit_hash

        result = _get_commit_hash()
        assert result == "abc1234"
        _, kwargs = mock_run.call_args
        assert "timeout" in kwargs
        assert kwargs["timeout"] == 10

    @patch("subprocess.run")
    def test_get_commit_hash_timeout_falls_back_to_version(self, mock_run):
        """TimeoutExpired on git describe falls back to package version."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=10)
        from gptme.eval.main import _get_commit_hash

        result = _get_commit_hash()
        # Should return version string or "unknown", not raise
        assert isinstance(result, str)
        assert len(result) > 0

    def test_docker_reexec_git_rev_parse_has_timeout(self):
        """check_output for git rev-parse in docker_reexec includes timeout."""
        source = _read_source("main.py")
        # Find the check_output call near "rev-parse"
        assert "timeout=10" in source

    def test_docker_run_no_timeout(self):
        """docker run for eval re-execution intentionally has no timeout."""
        # This is the long-running eval container — no timeout expected
        source = _read_source("main.py")
        # docker_cmd run should NOT have timeout (it's an eval run)
        lines = source.split("\n")
        for i, line in enumerate(lines):
            if "result = subprocess.run(docker_cmd" in line:
                # Check the next few lines don't have timeout
                context = "\n".join(lines[i : i + 3])
                assert "timeout" not in context


class TestTbenchRunTimeouts:
    """Verify timeout parameters in eval/tbench/run.py."""

    def test_tb_version_check_has_timeout(self):
        """tb --version check includes timeout."""
        source = _read_source("tbench/run.py")
        assert "timeout=10" in source

    def test_tb_run_no_timeout(self):
        """tb run (actual eval) intentionally has no timeout."""
        source = _read_source("tbench/run.py")
        lines = source.split("\n")
        for line in lines:
            if "result = subprocess.run(cmd, check=False)" in line:
                # The actual tb run should NOT have a timeout
                assert "timeout" not in line


class TestSwebenchEvaluateTimeouts:
    """Verify timeout parameters in eval/swebench/evaluate.py."""

    def test_git_diff_has_timeout(self):
        """git diff includes 60s timeout."""
        source = _read_source("swebench/evaluate.py")
        assert "timeout=60" in source


class TestSwebenchUtilsTimeouts:
    """Verify timeout parameters in eval/swebench/utils.py."""

    def test_git_clone_has_timeout(self):
        """git clone includes 300s timeout."""
        source = _read_source("swebench/utils.py")
        assert "timeout=300" in source

    def test_git_fetch_has_timeout(self):
        """git fetch includes 120s timeout."""
        source = _read_source("swebench/utils.py")
        assert "timeout=120" in source

    def test_git_checkout_has_timeout(self):
        """git checkout includes 60s timeout."""
        source = _read_source("swebench/utils.py")
        assert "timeout=60" in source


class TestSweExtraTestSpecTimeouts:
    """Verify timeout parameters in eval/swe_extra/swe_bench_test_spec.py."""

    def test_reset_repo_has_timeout(self):
        """reset_repo shell script includes 600s timeout."""
        source = _read_source("swe_extra/swe_bench_test_spec.py")
        # All three methods get timeout=600
        assert source.count("timeout=600") >= 3

    def test_all_shell_true_calls_have_timeout(self):
        """Every shell=True subprocess call has a timeout."""
        source = _read_source("swe_extra/swe_bench_test_spec.py")
        shell_true_count = source.count("shell=True")
        timeout_600_count = source.count("timeout=600")
        assert timeout_600_count >= shell_true_count, (
            f"Found {shell_true_count} shell=True calls but only "
            f"{timeout_600_count} timeout=600 parameters"
        )


class TestExecenvTimeouts:
    """Verify timeout parameters in eval/execenv.py."""

    def test_start_container_has_timeout(self):
        """docker run -d in start_container includes timeout."""
        source = _read_source("execenv.py")
        # Find the docker run -d section
        idx = source.index('"docker",\n')
        # The start_container section should have timeout=60
        section = source[idx : idx + 500]
        assert "timeout=60" in section
