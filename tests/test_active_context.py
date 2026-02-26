"""Tests for active context hook file filtering and token budget."""

from unittest.mock import patch

import pytest

from gptme.hooks.active_context import (
    _SKIP_FILENAMES,
    _SKIP_SUFFIXES,
    context_hook,
)
from gptme.message import Message


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with various file types."""
    # Normal source files
    (tmp_path / "main.py").write_text("print('hello')\n")
    (tmp_path / "utils.py").write_text("def helper(): pass\n")

    # Lockfiles (should be skipped)
    (tmp_path / "poetry.lock").write_text("[[package]]\nname = 'foo'\n" * 100)
    (tmp_path / "uv.lock").write_text("version = 1\n" * 100)
    (tmp_path / "package-lock.json").write_text('{"lockfileVersion": 3}\n')
    (tmp_path / "yarn.lock").write_text("# yarn lockfile v1\n")

    # Minified files (should be skipped)
    (tmp_path / "bundle.min.js").write_text("var a=1;var b=2;" * 1000)
    (tmp_path / "styles.min.css").write_text("body{margin:0}" * 500)

    # Large file (should be skipped by size limit)
    (tmp_path / "huge.txt").write_text("x" * 200_000)

    return tmp_path


def _run_hook(messages, workspace):
    """Helper to run the context hook and collect results."""
    with (
        patch("gptme.util.context.use_fresh_context", return_value=True),
        patch("gptme.hooks.active_context.git_status", return_value=""),
    ):
        results = list(context_hook(messages, workspace=workspace))
    return results


def _mock_select_files(file_list):
    """Create a mock for select_relevant_files that returns specific files."""
    return patch(
        "gptme.hooks.active_context.select_relevant_files",
        return_value=file_list,
    )


def test_skips_lockfiles(workspace):
    """Lockfiles should never be included in context."""
    files = [
        workspace / "main.py",
        workspace / "poetry.lock",
        workspace / "uv.lock",
        workspace / "package-lock.json",
    ]
    with _mock_select_files(files):
        results = _run_hook([Message("user", "test")], workspace)

    assert len(results) == 1
    content = results[0].content
    assert "main.py" in content
    assert "poetry.lock" not in content
    assert "uv.lock" not in content
    assert "package-lock.json" not in content


def test_skips_minified_files(workspace):
    """Minified JS/CSS should not be included."""
    files = [
        workspace / "main.py",
        workspace / "bundle.min.js",
        workspace / "styles.min.css",
    ]
    with _mock_select_files(files):
        results = _run_hook([Message("user", "test")], workspace)

    assert len(results) == 1
    content = results[0].content
    assert "main.py" in content
    assert "bundle.min.js" not in content
    assert "styles.min.css" not in content


def test_skips_large_files(workspace):
    """Files over 100KB should be skipped."""
    files = [workspace / "main.py", workspace / "huge.txt"]
    with _mock_select_files(files):
        results = _run_hook([Message("user", "test")], workspace)

    assert len(results) == 1
    content = results[0].content
    assert "main.py" in content
    assert "huge.txt" not in content


def test_token_budget_respected(workspace):
    """Should stop adding files once token budget is exhausted."""
    # Create two files that individually fit in the 100KB per-file limit
    # but together exceed the token budget.
    # Each file is ~80KB (under 100KB limit), but two together exceed 120KB budget.
    file_a = workspace / "file_a.py"
    file_a.write_text("a = 1\n" * 14_000)  # ~84KB

    file_b = workspace / "file_b.py"
    file_b.write_text("b = 2\n" * 14_000)  # ~84KB

    files = [file_a, file_b]
    with _mock_select_files(files):
        results = _run_hook([Message("user", "test")], workspace)

    assert len(results) == 1
    content = results[0].content
    # file_a should be included (fits in budget)
    assert "file_a.py" in content
    # file_b should be skipped (budget exhausted after file_a)
    assert "file_b.py" not in content


def test_normal_files_included(workspace):
    """Normal source files should be included."""
    files = [workspace / "main.py", workspace / "utils.py"]
    with _mock_select_files(files):
        results = _run_hook([Message("user", "test")], workspace)

    assert len(results) == 1
    content = results[0].content
    assert "main.py" in content
    assert "utils.py" in content
    assert "print('hello')" in content
    assert "def helper(): pass" in content


def test_skip_filenames_comprehensive():
    """Verify all expected lockfile names are in the skip list."""
    expected = {
        "poetry.lock",
        "uv.lock",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Cargo.lock",
        "Gemfile.lock",
        "composer.lock",
        "go.sum",
        "flake.lock",
        "Pipfile.lock",
    }
    assert expected == _SKIP_FILENAMES


def test_skip_suffixes_comprehensive():
    """Verify all expected skip suffixes are in the skip list."""
    expected = {".min.js", ".min.css", ".map", ".pyc", ".pyo", ".whl", ".egg-info"}
    assert expected == _SKIP_SUFFIXES


def test_nonexistent_files_handled(workspace):
    """Non-existent files should be silently skipped."""
    files = [workspace / "main.py", workspace / "does_not_exist.py"]
    with _mock_select_files(files):
        results = _run_hook([Message("user", "test")], workspace)

    assert len(results) == 1
    content = results[0].content
    assert "main.py" in content
    assert "does_not_exist" not in content


def test_binary_files_handled(workspace):
    """Binary files should get a placeholder."""
    binary = workspace / "data.bin"
    binary.write_bytes(b"\x00\x01\x02\xff" * 100)

    files = [workspace / "main.py", binary]
    with _mock_select_files(files):
        results = _run_hook([Message("user", "test")], workspace)

    assert len(results) == 1
    content = results[0].content
    assert "main.py" in content
    assert "<binary file>" in content
