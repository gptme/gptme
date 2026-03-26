"""Tests for the morph tool — AI-powered fast file editing.

Tests cover:
- preview_morph: diff generation for previews
- execute_morph_impl: file writing with concurrent modification detection
- execute_morph: end-to-end with API mocking
- is_openrouter_available: provider detection
- examples: output format
- tool spec: registration and parameters
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# File permission tests are meaningless when running as root (root bypasses chmod)
skip_if_root = pytest.mark.skipif(
    sys.platform != "win32" and os.getuid() == 0,
    reason="File permission tests are not meaningful when running as root",
)

from gptme.message import Message
from gptme.tools.morph import (
    execute_morph,
    execute_morph_impl,
    is_openrouter_available,
    preview_morph,
    tool,
)

# ── preview_morph ──────────────────────────────────────────────────────


class TestPreviewMorph:
    """Tests for preview_morph — generates diff previews."""

    def test_returns_diff_for_changed_content(self, tmp_path: Path):
        """Preview shows unified diff when content differs from file."""
        f = tmp_path / "hello.py"
        f.write_text("print('hello')\n")
        result = preview_morph("print('world')\n", f)
        assert result is not None
        assert "---" in result
        assert "+++" in result
        assert "-print('hello')" in result
        assert "+print('world')" in result

    def test_returns_no_changes_for_identical_content(self, tmp_path: Path):
        """Preview says 'No changes' when content matches file."""
        f = tmp_path / "same.py"
        f.write_text("unchanged\n")
        result = preview_morph("unchanged\n", f)
        assert result == "No changes would be made"

    def test_returns_error_for_missing_file(self, tmp_path: Path):
        """Preview returns error when file doesn't exist."""
        result = preview_morph("content", tmp_path / "nope.py")
        assert result == "File does not exist"

    def test_returns_error_for_none_path(self):
        """Preview returns error when path is None."""
        result = preview_morph("content", None)
        assert result == "File does not exist"

    def test_multiline_diff(self, tmp_path: Path):
        """Preview correctly shows multi-line diffs."""
        f = tmp_path / "multi.py"
        f.write_text("line1\nline2\nline3\n")
        result = preview_morph("line1\nchanged\nline3\n", f)
        assert result is not None
        assert "-line2" in result
        assert "+changed" in result

    def test_addition_diff(self, tmp_path: Path):
        """Preview shows added lines."""
        f = tmp_path / "add.py"
        f.write_text("line1\nline2\n")
        result = preview_morph("line1\nline2\nline3\n", f)
        assert result is not None
        assert "+line3" in result

    def test_deletion_diff(self, tmp_path: Path):
        """Preview shows removed lines."""
        f = tmp_path / "del.py"
        f.write_text("line1\nline2\nline3\n")
        result = preview_morph("line1\nline3\n", f)
        assert result is not None
        assert "-line2" in result

    @skip_if_root
    def test_exception_during_read(self, tmp_path: Path):
        """Preview handles read errors gracefully."""
        f = tmp_path / "unreadable.py"
        f.write_text("content")
        f.chmod(0o000)
        try:
            result = preview_morph("new content", f)
            assert result is not None
            assert "Preview failed:" in result
        finally:
            f.chmod(0o644)


# ── execute_morph_impl ─────────────────────────────────────────────────


class TestExecuteMorphImpl:
    """Tests for execute_morph_impl — writes edits with safety checks."""

    def test_successful_edit(self, tmp_path: Path):
        """Writes new content and returns diff message."""
        f = tmp_path / "target.py"
        original = "def foo():\n    pass\n"
        f.write_text(original)

        msgs = list(execute_morph_impl("def foo():\n    return 42\n", f, original))
        assert len(msgs) == 1
        assert "Edit successfully applied" in msgs[0].content
        assert "-    pass" in msgs[0].content
        assert "+    return 42" in msgs[0].content
        assert f.read_text() == "def foo():\n    return 42\n"

    def test_concurrent_modification_detected(self, tmp_path: Path):
        """Refuses edit when file was modified since patch generation."""
        f = tmp_path / "race.py"
        f.write_text("original content")

        # Pass stale expected content — simulates another process editing
        msgs = list(execute_morph_impl("new content", f, "stale expected content"))
        assert len(msgs) == 1
        assert "has been modified since" in msgs[0].content
        # File should NOT have been overwritten
        assert f.read_text() == "original content"

    def test_no_change_edit(self, tmp_path: Path):
        """Warns when edit results in identical content."""
        f = tmp_path / "nochange.py"
        content = "same content\n"
        f.write_text(content)

        msgs = list(execute_morph_impl(content, f, content))
        assert len(msgs) == 1
        assert "no changes" in msgs[0].content.lower()
        assert f.read_text() == content

    def test_no_path_raises(self):
        """Raises ValueError when path is None."""
        with pytest.raises(ValueError, match="No file path"):
            list(execute_morph_impl("content", None, "original"))

    def test_file_not_found_raises(self, tmp_path: Path):
        """Raises ValueError for missing file."""
        with pytest.raises(ValueError, match="No such file"):
            list(execute_morph_impl("content", tmp_path / "missing.py", "original"))

    @skip_if_root
    def test_permission_denied_raises(self, tmp_path: Path):
        """Raises ValueError when file is not writable."""
        f = tmp_path / "readonly.py"
        f.write_text("original")
        f.chmod(0o444)
        try:
            with pytest.raises(ValueError, match="Permission denied"):
                list(execute_morph_impl("new content", f, "original"))
        finally:
            f.chmod(0o644)

    def test_large_edit(self, tmp_path: Path):
        """Handles large file edits correctly."""
        f = tmp_path / "large.py"
        original = "\n".join(f"line{i}" for i in range(1000)) + "\n"
        f.write_text(original)
        edited = original.replace("line500", "CHANGED_LINE")

        msgs = list(execute_morph_impl(edited, f, original))
        assert len(msgs) == 1
        assert "Edit successfully applied" in msgs[0].content
        assert "CHANGED_LINE" in f.read_text()

    def test_empty_file_edit(self, tmp_path: Path):
        """Handles editing an empty file."""
        f = tmp_path / "empty.py"
        f.write_text("")
        msgs = list(execute_morph_impl("new content\n", f, ""))
        assert len(msgs) == 1
        assert "Edit successfully applied" in msgs[0].content
        assert f.read_text() == "new content\n"

    def test_edit_to_empty(self, tmp_path: Path):
        """Handles editing file to empty content."""
        f = tmp_path / "clear.py"
        original = "some content\n"
        f.write_text(original)
        msgs = list(execute_morph_impl("", f, original))
        assert len(msgs) == 1
        assert "Edit successfully applied" in msgs[0].content
        assert f.read_text() == ""


# ── execute_morph (end-to-end with mocks) ──────────────────────────────


class TestExecuteMorph:
    """Tests for execute_morph — full pipeline with API mocking."""

    def test_no_code_returns_error(self):
        """Returns error when no edit instructions provided."""
        msgs = list(execute_morph(None, ["file.py"], None))
        assert len(msgs) == 1
        assert "No edit instructions" in msgs[0].content

    def test_empty_code_returns_error(self):
        """Returns error when code is empty string."""
        msgs = list(execute_morph("", ["file.py"], None))
        assert len(msgs) == 1
        assert "No edit instructions" in msgs[0].content

    def test_no_file_path_raises(self):
        """Raises ValueError when no file path can be determined."""
        # get_path raises ValueError("No filename provided") with None args
        with pytest.raises(ValueError, match="No filename"):
            list(execute_morph("some edit", None, None))

    def test_file_not_found_returns_error(self, tmp_path: Path):
        """Returns error when target file doesn't exist."""
        missing = tmp_path / "doesnt_exist.py"
        msgs = list(execute_morph("edit", [str(missing)], None))
        assert len(msgs) == 1
        assert "File not found" in msgs[0].content

    def test_path_traversal_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Rejects paths that escape current directory."""
        target = tmp_path / "legit.py"
        target.write_text("content")
        # Set cwd to a subdirectory so the file path escapes it
        sub = tmp_path / "subdir"
        sub.mkdir()
        monkeypatch.chdir(sub)
        with pytest.raises(ValueError, match="Path traversal"):
            list(execute_morph("edit", ["../legit.py"], None))

    @patch("gptme.tools.morph._chat_complete")
    def test_api_failure_returns_error(self, mock_chat: MagicMock, tmp_path: Path):
        """Returns error when Morph API call fails."""
        f = tmp_path / "api_fail.py"
        f.write_text("original content")
        mock_chat.side_effect = RuntimeError("API connection failed")

        msgs = list(execute_morph("edit instructions", [str(f)], None))
        assert len(msgs) == 1
        assert "failed Morph API call" in msgs[0].content

    @patch("gptme.tools.morph._chat_complete")
    def test_empty_api_response_returns_error(
        self, mock_chat: MagicMock, tmp_path: Path
    ):
        """Returns error when Morph API returns empty content."""
        f = tmp_path / "empty_resp.py"
        f.write_text("original content")
        mock_chat.return_value = ("", {})

        msgs = list(execute_morph("edit instructions", [str(f)], None))
        assert len(msgs) == 1
        assert "empty content" in msgs[0].content

    @patch("gptme.tools.morph._chat_complete")
    def test_whitespace_only_api_response_returns_error(
        self, mock_chat: MagicMock, tmp_path: Path
    ):
        """Returns error when Morph API returns only whitespace."""
        f = tmp_path / "ws_resp.py"
        f.write_text("original content")
        mock_chat.return_value = ("   \n  ", {})

        msgs = list(execute_morph("edit instructions", [str(f)], None))
        assert len(msgs) == 1
        assert "empty content" in msgs[0].content

    @patch("gptme.tools.morph.execute_with_confirmation")
    @patch("gptme.tools.morph._chat_complete")
    def test_successful_api_call_passes_to_confirmation(
        self,
        mock_chat: MagicMock,
        mock_confirm: MagicMock,
        tmp_path: Path,
    ):
        """Successful API call forwards edited content to confirmation flow."""
        f = tmp_path / "success.py"
        f.write_text("original content")
        mock_chat.return_value = ("edited content", {})
        mock_confirm.return_value = iter([Message("system", "Applied")])

        list(execute_morph("edit instructions", [str(f)], None))
        assert mock_confirm.called
        call_kwargs = mock_confirm.call_args
        # First positional arg should be the edited content
        assert call_kwargs[0][0] == "edited content"

    @patch("gptme.tools.morph._chat_complete")
    def test_api_receives_correct_prompt_format(
        self, mock_chat: MagicMock, tmp_path: Path
    ):
        """Verifies the prompt sent to Morph uses <code>...<update> format."""
        f = tmp_path / "prompt_fmt.py"
        f.write_text("file content here")
        mock_chat.return_value = ("result", {})

        # Need to also mock confirmation to avoid interactive prompt
        with patch("gptme.tools.morph.execute_with_confirmation") as mock_confirm:
            mock_confirm.return_value = iter([])
            list(execute_morph("my edit", [str(f)], None))

        # Check the messages sent to API
        call_args = mock_chat.call_args
        messages = call_args[0][0]
        assert len(messages) == 1
        assert "<code>file content here</code>" in messages[0].content
        assert "<update>my edit</update>" in messages[0].content

    @patch("gptme.tools.morph._chat_complete")
    def test_api_uses_morph_model(self, mock_chat: MagicMock, tmp_path: Path):
        """Verifies the correct Morph model is used."""
        f = tmp_path / "model.py"
        f.write_text("content")
        mock_chat.return_value = ("result", {})

        with patch("gptme.tools.morph.execute_with_confirmation") as mock_confirm:
            mock_confirm.return_value = iter([])
            list(execute_morph("edit", [str(f)], None))

        call_args = mock_chat.call_args
        assert call_args[0][1] == "openrouter/morph/morph-v3-fast"

    def test_code_from_kwargs(self, tmp_path: Path):
        """Extracts edit content from kwargs when code is None."""
        f = tmp_path / "kwargs.py"
        f.write_text("content")

        with patch("gptme.tools.morph._chat_complete") as mock_chat:
            mock_chat.return_value = ("result", {})
            with patch("gptme.tools.morph.execute_with_confirmation") as mock_confirm:
                mock_confirm.return_value = iter([])
                list(execute_morph(None, [str(f)], {"edit": "from kwargs"}))

        messages = mock_chat.call_args[0][0]
        assert "<update>from kwargs</update>" in messages[0].content

    @skip_if_root
    def test_permission_denied_returns_error(self, tmp_path: Path):
        """Returns error when file can't be read."""
        f = tmp_path / "noperm.py"
        f.write_text("content")
        f.chmod(0o000)
        try:
            msgs = list(execute_morph("edit", [str(f)], None))
            assert len(msgs) == 1
            assert "Permission denied" in msgs[0].content
        finally:
            f.chmod(0o644)

    def test_absolute_path_allowed(self, tmp_path: Path):
        """Absolute paths are accepted without path traversal check."""
        f = tmp_path / "absolute.py"
        f.write_text("content")

        with patch("gptme.tools.morph._chat_complete") as mock_chat:
            mock_chat.return_value = ("result", {})
            with patch("gptme.tools.morph.execute_with_confirmation") as mock_confirm:
                mock_confirm.return_value = iter([])
                # Should not raise — absolute paths skip traversal check
                list(execute_morph("edit", [str(f)], None))
        assert mock_chat.called


# ── is_openrouter_available ────────────────────────────────────────────


class TestIsOpenrouterAvailable:
    """Tests for provider availability check."""

    @patch("gptme.tools.morph.list_available_providers")
    def test_available_when_openrouter_present(self, mock_providers: MagicMock):
        """Returns True when OpenRouter is in the provider list."""
        mock_providers.return_value = [("anthropic", "key"), ("openrouter", "key")]
        assert is_openrouter_available() is True

    @patch("gptme.tools.morph.list_available_providers")
    def test_unavailable_when_openrouter_absent(self, mock_providers: MagicMock):
        """Returns False when OpenRouter is not in the provider list."""
        mock_providers.return_value = [("anthropic", "key"), ("openai", "key")]
        assert is_openrouter_available() is False

    @patch("gptme.tools.morph.list_available_providers")
    def test_unavailable_with_empty_providers(self, mock_providers: MagicMock):
        """Returns False when no providers are available."""
        mock_providers.return_value = []
        assert is_openrouter_available() is False


# ── examples ───────────────────────────────────────────────────────────


class TestExamples:
    """Tests for example output generation."""

    def test_examples_markdown_format(self):
        """Examples generate valid output for markdown format."""
        from gptme.tools.morph import examples

        result = examples("markdown")
        assert "morph" in result
        assert "existing code" in result
        assert "example.py" in result

    def test_examples_xml_format(self):
        """Examples generate valid output for XML format."""
        from gptme.tools.morph import examples

        result = examples("xml")
        assert "morph" in result
        assert "existing code" in result


# ── tool spec ──────────────────────────────────────────────────────────


class TestToolSpec:
    """Tests for morph tool registration."""

    def test_tool_name(self):
        assert tool.name == "morph"

    def test_tool_has_parameters(self):
        assert tool.parameters is not None
        assert len(tool.parameters) == 2
        names = [p.name for p in tool.parameters]
        assert "path" in names
        assert "edit" in names

    def test_path_parameter_required(self):
        path_param = next(p for p in tool.parameters if p.name == "path")
        assert path_param.required is True

    def test_edit_parameter_required(self):
        edit_param = next(p for p in tool.parameters if p.name == "edit")
        assert edit_param.required is True

    def test_tool_has_execute(self):
        assert tool.execute is not None

    def test_tool_block_types(self):
        assert "morph" in tool.block_types

    def test_tool_has_instructions(self):
        assert tool.instructions is not None
        assert "existing code" in tool.instructions

    def test_tool_has_description(self):
        assert tool.desc is not None
        assert "Morph" in tool.desc
