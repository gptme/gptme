"""Tests for scripts/treeofthoughts.py core logic.

Tests only the utility functions (run_eval, _fmt_history, _changed_files) which
are pure-Python and don't require a live LLM or gptme init. The full tree_search()
loop requires a model and is exercised manually / in integration tests.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Shell commands in tests (exit, echo, printf, test) are POSIX-only",
)

# ---------------------------------------------------------------------------
# Load the treeofthoughts module without executing main()
# ---------------------------------------------------------------------------

_SCRIPT = Path(__file__).parents[1] / "scripts" / "treeofthoughts.py"


@pytest.fixture(scope="module")
def tot():
    """Import scripts/treeofthoughts.py as a module."""
    spec = importlib.util.spec_from_file_location("treeofthoughts", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("treeofthoughts", mod)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# run_eval
# ---------------------------------------------------------------------------


class TestRunEval:
    def test_pass_exit_zero(self, tot, tmp_path):
        passed, score, output = tot.run_eval("exit 0", cwd=tmp_path)
        assert passed is True
        assert score == 1.0

    def test_fail_nonzero(self, tot, tmp_path):
        passed, score, output = tot.run_eval("exit 1", cwd=tmp_path)
        assert passed is False
        assert score == 0.0

    def test_numeric_score_from_stdout(self, tot, tmp_path):
        # A command that prints a numeric score on its last line.
        passed, score, output = tot.run_eval("echo 0.75", cwd=tmp_path)
        assert abs(score - 0.75) < 1e-9

    def test_score_is_last_numeric_line(self, tot, tmp_path):
        # Only the last numeric line should be used.
        cmd = "printf '0.5\\n0.9\\n'"
        passed, score, output = tot.run_eval(cmd, cwd=tmp_path)
        assert abs(score - 0.9) < 1e-9

    def test_non_numeric_stdout_falls_back_to_exit_code(self, tot, tmp_path):
        passed, score, output = tot.run_eval("echo hello", cwd=tmp_path)
        # 'hello' is not numeric; exit 0 → score = 1.0
        assert abs(score - 1.0) < 1e-9
        assert passed is True

    def test_output_captures_stderr(self, tot, tmp_path):
        _, _, output = tot.run_eval("echo err >&2", cwd=tmp_path)
        assert "err" in output

    def test_cwd_is_respected(self, tot, tmp_path):
        (tmp_path / "marker.txt").write_text("hi")
        # command succeeds only if cwd is correct
        passed, _, _ = tot.run_eval("test -f marker.txt", cwd=tmp_path)
        assert passed is True

        # Same command from a different directory should fail.
        other = tmp_path.parent
        passed2, _, _ = tot.run_eval("test -f marker.txt", cwd=other)
        assert passed2 is False


# ---------------------------------------------------------------------------
# _fmt_history
# ---------------------------------------------------------------------------


class TestFmtHistory:
    def test_empty(self, tot):
        out = tot._fmt_history([])
        assert "no prior attempts" in out

    def test_single_kept(self, tot):
        attempts = [
            {
                "iteration": 1,
                "score_before": 0.5,
                "score_after": 0.8,
                "kept": True,
                "files_changed": ["foo.py"],
                "diagnosis": "",
            }
        ]
        out = tot._fmt_history(attempts)
        assert "✓ kept" in out
        assert "0.500" in out or "0.5" in out
        assert "foo.py" in out

    def test_single_reverted(self, tot):
        attempts = [
            {
                "iteration": 1,
                "score_before": 0.9,
                "score_after": 0.3,
                "kept": False,
                "files_changed": [],
                "diagnosis": "broke imports",
            }
        ]
        out = tot._fmt_history(attempts)
        assert "✗ reverted" in out
        assert "broke imports" in out

    def test_multiple_attempts(self, tot):
        attempts = [
            {
                "iteration": 1,
                "score_before": 0.5,
                "score_after": 0.6,
                "kept": True,
                "files_changed": [],
                "diagnosis": "",
            },
            {
                "iteration": 2,
                "score_before": 0.6,
                "score_after": 0.4,
                "kept": False,
                "files_changed": ["x.py"],
                "diagnosis": "wrong approach",
            },
        ]
        out = tot._fmt_history(attempts)
        assert "Attempt 1" in out
        assert "Attempt 2" in out
        assert "wrong approach" in out


# ---------------------------------------------------------------------------
# _file_hash
# ---------------------------------------------------------------------------


class TestFileHash:
    def test_returns_hex_string(self, tot, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hello")
        h = tot._file_hash(f)
        assert len(h) == 32  # MD5 hex digest

    def test_different_content_different_hash(self, tot, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hello")
        h1 = tot._file_hash(f)
        f.write_text("world")
        h2 = tot._file_hash(f)
        assert h1 != h2

    def test_same_content_same_hash(self, tot, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("stable")
        assert tot._file_hash(f) == tot._file_hash(f)

    def test_missing_file_returns_empty_string(self, tot, tmp_path):
        h = tot._file_hash(tmp_path / "nonexistent.py")
        assert h == ""
