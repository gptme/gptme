"""Tests for active_context hook."""

from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.hooks.active_context import (
    _SKIP_FILENAMES,
    _SKIP_SUFFIXES,
    _TOKEN_BUDGET_CHARS,
    context_hook,
)
from gptme.message import Message


def _get_messages(results: list) -> list[Message]:
    """Extract Message objects from hook results (filtering out StopPropagation)."""
    return [r for r in results if isinstance(r, Message)]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a workspace with some files."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "main.py").write_text("def main():\n    print('hello')\n")
    (ws / "utils.py").write_text("def helper():\n    return 42\n")
    (ws / "README.md").write_text("# Project\nA test project.\n")
    return ws


@pytest.fixture
def messages() -> list[Message]:
    """Create sample conversation messages."""
    return [
        Message("system", "You are an assistant."),
        Message("user", "Help me fix main.py"),
    ]


class TestSkipLists:
    """Tests for skip filename and suffix sets."""

    def test_skip_filenames_contains_lockfiles(self):
        """Common lockfiles should be in the skip list."""
        assert "package-lock.json" in _SKIP_FILENAMES
        assert "uv.lock" in _SKIP_FILENAMES
        assert "yarn.lock" in _SKIP_FILENAMES
        assert "Cargo.lock" in _SKIP_FILENAMES
        assert "poetry.lock" in _SKIP_FILENAMES

    def test_skip_suffixes_contains_minified(self):
        """Minified and compiled files should be in skip suffixes."""
        assert ".min.js" in _SKIP_SUFFIXES
        assert ".min.css" in _SKIP_SUFFIXES
        assert ".pyc" in _SKIP_SUFFIXES

    def test_token_budget_is_reasonable(self):
        """Token budget should be roughly 30k tokens (120k chars)."""
        assert _TOKEN_BUDGET_CHARS == 120_000


class TestContextHookGating:
    """Tests for the opt-in gating via use_fresh_context."""

    def test_disabled_by_default(self, workspace: Path, messages: list[Message]):
        """When fresh context is not enabled, hook should yield nothing."""
        msgs = list(context_hook(messages, workspace=workspace))
        assert len(msgs) == 0

    def test_no_workspace(self, messages: list[Message]):
        """When no workspace is provided, hook should yield nothing."""
        with patch("gptme.util.context.use_fresh_context", return_value=True):
            msgs = list(context_hook(messages))
            assert len(msgs) == 0

    def test_no_workspace_kwarg(self, messages: list[Message]):
        """When workspace kwarg is None, hook should yield nothing."""
        with patch("gptme.util.context.use_fresh_context", return_value=True):
            msgs = list(context_hook(messages, workspace=None))
            assert len(msgs) == 0


class TestContextHookFileSelection:
    """Tests for file selection and content injection."""

    def test_injects_context_when_enabled(
        self, workspace: Path, messages: list[Message]
    ):
        """When fresh context is enabled and files exist, inject context."""
        with (
            patch("gptme.util.context.use_fresh_context", return_value=True),
            patch(
                "gptme.hooks.active_context.select_relevant_files",
                return_value=[workspace / "main.py"],
            ),
            patch("gptme.hooks.active_context.git_status", return_value=None),
        ):
            msgs = list(context_hook(messages, workspace=workspace))
            assert len(msgs) == 1
            msg = msgs[0]
            assert isinstance(msg, Message)
            assert msg.role == "system"
            assert "Context" in msg.content
            assert "def main():" in msg.content

    def test_includes_git_status(self, workspace: Path, messages: list[Message]):
        """Git status should be included when available."""
        with (
            patch("gptme.util.context.use_fresh_context", return_value=True),
            patch(
                "gptme.hooks.active_context.select_relevant_files",
                return_value=[workspace / "main.py"],
            ),
            patch(
                "gptme.hooks.active_context.git_status",
                return_value="M  main.py",
            ),
        ):
            results = _get_messages(list(context_hook(messages, workspace=workspace)))
            assert len(results) == 1
            assert "M  main.py" in results[0].content

    def test_skips_lockfiles(self, workspace: Path, messages: list[Message]):
        """Lockfiles should be skipped even if returned by selector."""
        lockfile = workspace / "package-lock.json"
        lockfile.write_text('{"lockfileVersion": 3}')

        with (
            patch("gptme.util.context.use_fresh_context", return_value=True),
            patch(
                "gptme.hooks.active_context.select_relevant_files",
                return_value=[lockfile],
            ),
            patch("gptme.hooks.active_context.git_status", return_value=None),
        ):
            msgs = list(context_hook(messages, workspace=workspace))
            # Lockfile skipped, no other content → empty
            assert len(msgs) == 0

    def test_skips_minified_files(self, workspace: Path, messages: list[Message]):
        """Minified files should be skipped."""
        minified = workspace / "bundle.min.js"
        minified.write_text("function a(){}")

        with (
            patch("gptme.util.context.use_fresh_context", return_value=True),
            patch(
                "gptme.hooks.active_context.select_relevant_files",
                return_value=[minified],
            ),
            patch("gptme.hooks.active_context.git_status", return_value=None),
        ):
            msgs = list(context_hook(messages, workspace=workspace))
            assert len(msgs) == 0

    def test_skips_large_files(self, workspace: Path, messages: list[Message]):
        """Files over 100k chars should be skipped."""
        large_file = workspace / "big.txt"
        large_file.write_text("x" * 100_001)

        with (
            patch("gptme.util.context.use_fresh_context", return_value=True),
            patch(
                "gptme.hooks.active_context.select_relevant_files",
                return_value=[large_file],
            ),
            patch("gptme.hooks.active_context.git_status", return_value=None),
        ):
            msgs = list(context_hook(messages, workspace=workspace))
            assert len(msgs) == 0

    def test_respects_token_budget(self, workspace: Path, messages: list[Message]):
        """Files exceeding total token budget should be skipped."""
        # Create a file that's under the per-file limit but test budget enforcement
        big_file = workspace / "bigish.txt"
        big_file.write_text("y" * 99_000)
        another_file = workspace / "another.txt"
        another_file.write_text("z" * 99_000)

        with (
            patch("gptme.util.context.use_fresh_context", return_value=True),
            patch(
                "gptme.hooks.active_context.select_relevant_files",
                return_value=[big_file, another_file],
            ),
            patch("gptme.hooks.active_context.git_status", return_value=None),
        ):
            results = _get_messages(list(context_hook(messages, workspace=workspace)))
            if results:
                content = results[0].content
                # Both files together (198k) exceed budget (120k)
                # First file should be included, second should be skipped
                assert "y" * 100 in content  # First file present
                assert "z" * 100 not in content  # Second file skipped

    def test_handles_missing_files(self, workspace: Path, messages: list[Message]):
        """Non-existent files should be skipped gracefully."""
        missing = workspace / "nonexistent.py"

        with (
            patch("gptme.util.context.use_fresh_context", return_value=True),
            patch(
                "gptme.hooks.active_context.select_relevant_files",
                return_value=[missing],
            ),
            patch("gptme.hooks.active_context.git_status", return_value=None),
        ):
            msgs = list(context_hook(messages, workspace=workspace))
            # Missing file skipped, no content
            assert len(msgs) == 0

    def test_handles_binary_files(self, workspace: Path, messages: list[Message]):
        """Binary files should be handled gracefully."""
        binary_file = workspace / "image.bin"
        binary_file.write_bytes(b"\x00\x01\x02\xff\xfe")

        with (
            patch("gptme.util.context.use_fresh_context", return_value=True),
            patch(
                "gptme.hooks.active_context.select_relevant_files",
                return_value=[binary_file],
            ),
            patch("gptme.hooks.active_context.git_status", return_value=None),
        ):
            results = _get_messages(list(context_hook(messages, workspace=workspace)))
            if results:
                assert "binary file" in results[0].content.lower()

    def test_fallback_on_selector_error(self, workspace: Path, messages: list[Message]):
        """When selector raises, should fall back to mention-based selection."""
        with (
            patch("gptme.util.context.use_fresh_context", return_value=True),
            patch(
                "gptme.hooks.active_context.select_relevant_files",
                side_effect=Exception("selector broke"),
            ),
            patch(
                "gptme.hooks.active_context.get_mentioned_files",
                return_value={workspace / "main.py": 1},
            ),
            patch("gptme.hooks.active_context.git_status", return_value=None),
        ):
            results = _get_messages(list(context_hook(messages, workspace=workspace)))
            # Should fall back and still inject context
            assert len(results) == 1
            assert "def main():" in results[0].content

    def test_context_message_includes_working_dir(
        self, workspace: Path, messages: list[Message]
    ):
        """Injected context should mention the working directory."""
        with (
            patch("gptme.util.context.use_fresh_context", return_value=True),
            patch(
                "gptme.hooks.active_context.select_relevant_files",
                return_value=[workspace / "main.py"],
            ),
            patch("gptme.hooks.active_context.git_status", return_value=None),
        ):
            results = _get_messages(list(context_hook(messages, workspace=workspace)))
            assert len(results) == 1
            assert "Working directory" in results[0].content
