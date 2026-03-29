"""Tests for gptme/server/api_v2_common.py.

Covers _is_debug_errors_enabled, _abs_to_rel_workspace, and msg2dict.
"""

from pathlib import Path

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from gptme.message import Message
from gptme.server.api_v2_common import (
    _abs_to_rel_workspace,
    _is_debug_errors_enabled,
    msg2dict,
)
from gptme.util.uri import URI

# ---------------------------------------------------------------------------
# _is_debug_errors_enabled
# ---------------------------------------------------------------------------


class TestIsDebugErrorsEnabled:
    """Tests for _is_debug_errors_enabled()."""

    def test_disabled_by_default(self, monkeypatch):
        """Unset env var returns False."""
        monkeypatch.delenv("GPTME_DEBUG_ERRORS", raising=False)
        assert _is_debug_errors_enabled() is False

    def test_enabled_with_1(self, monkeypatch):
        """'1' enables debug errors."""
        monkeypatch.setenv("GPTME_DEBUG_ERRORS", "1")
        assert _is_debug_errors_enabled() is True

    def test_enabled_with_true_lowercase(self, monkeypatch):
        """'true' enables debug errors."""
        monkeypatch.setenv("GPTME_DEBUG_ERRORS", "true")
        assert _is_debug_errors_enabled() is True

    def test_enabled_with_true_uppercase(self, monkeypatch):
        """'TRUE' enables debug errors (case-insensitive)."""
        monkeypatch.setenv("GPTME_DEBUG_ERRORS", "TRUE")
        assert _is_debug_errors_enabled() is True

    def test_enabled_with_yes(self, monkeypatch):
        """'yes' enables debug errors."""
        monkeypatch.setenv("GPTME_DEBUG_ERRORS", "yes")
        assert _is_debug_errors_enabled() is True

    def test_enabled_with_yes_uppercase(self, monkeypatch):
        """'YES' enables debug errors (case-insensitive)."""
        monkeypatch.setenv("GPTME_DEBUG_ERRORS", "YES")
        assert _is_debug_errors_enabled() is True

    def test_disabled_with_0(self, monkeypatch):
        """'0' disables debug errors."""
        monkeypatch.setenv("GPTME_DEBUG_ERRORS", "0")
        assert _is_debug_errors_enabled() is False

    def test_disabled_with_false(self, monkeypatch):
        """'false' disables debug errors."""
        monkeypatch.setenv("GPTME_DEBUG_ERRORS", "false")
        assert _is_debug_errors_enabled() is False

    def test_disabled_with_no(self, monkeypatch):
        """'no' disables debug errors."""
        monkeypatch.setenv("GPTME_DEBUG_ERRORS", "no")
        assert _is_debug_errors_enabled() is False

    def test_disabled_with_empty_string(self, monkeypatch):
        """Empty string disables debug errors."""
        monkeypatch.setenv("GPTME_DEBUG_ERRORS", "")
        assert _is_debug_errors_enabled() is False

    def test_disabled_with_arbitrary_value(self, monkeypatch):
        """Arbitrary value disables debug errors."""
        monkeypatch.setenv("GPTME_DEBUG_ERRORS", "enabled")
        assert _is_debug_errors_enabled() is False


# ---------------------------------------------------------------------------
# _abs_to_rel_workspace
# ---------------------------------------------------------------------------


class TestAbsToRelWorkspace:
    """Tests for _abs_to_rel_workspace()."""

    def test_path_inside_workspace(self, tmp_path):
        """Path inside workspace is returned as relative."""
        workspace = tmp_path.resolve()  # resolve handles macOS /var -> /private/var
        file_path = workspace / "subdir" / "file.txt"
        result = _abs_to_rel_workspace(file_path, workspace)
        assert result == "subdir/file.txt"

    def test_path_directly_in_workspace(self, tmp_path):
        """Path directly in workspace root returns just the filename."""
        workspace = tmp_path.resolve()  # resolve handles macOS /var -> /private/var
        file_path = workspace / "file.txt"
        result = _abs_to_rel_workspace(file_path, workspace)
        assert result == "file.txt"

    def test_path_outside_workspace(self, tmp_path):
        """Path outside workspace is returned as absolute."""
        workspace = tmp_path / "workspace"
        outside = tmp_path / "other" / "file.txt"
        result = _abs_to_rel_workspace(outside, workspace)
        assert result == str(outside.resolve())

    def test_string_path_inside_workspace(self, tmp_path):
        """String path inside workspace is converted to relative."""
        workspace = tmp_path.resolve()  # resolve handles macOS /var -> /private/var
        file_str = str(workspace / "file.txt")
        result = _abs_to_rel_workspace(file_str, workspace)
        assert result == "file.txt"

    def test_uri_returned_as_is(self, tmp_path):
        """URI objects are returned as-is (not treated as paths)."""
        workspace = tmp_path
        uri = URI("https://example.com/image.png")
        result = _abs_to_rel_workspace(uri, workspace)
        assert result == "https://example.com/image.png"

    def test_uri_not_relativized(self, tmp_path):
        """URI is never made relative even if it looks like a sub-path."""
        workspace = Path("/workspace")
        uri = URI("memo://workspace/file.txt")
        result = _abs_to_rel_workspace(uri, workspace)
        assert result == "memo://workspace/file.txt"

    def test_deeply_nested_path(self, tmp_path):
        """Deeply nested path inside workspace uses forward slashes."""
        workspace = tmp_path.resolve()  # resolve handles macOS /var -> /private/var
        file_path = workspace / "a" / "b" / "c" / "file.py"
        result = _abs_to_rel_workspace(file_path, workspace)
        assert result == "a/b/c/file.py"


# ---------------------------------------------------------------------------
# msg2dict
# ---------------------------------------------------------------------------


class TestMsg2Dict:
    """Tests for msg2dict()."""

    def test_basic_user_message(self, tmp_path):
        """Basic user message serializes role, content, timestamp."""
        msg = Message(role="user", content="Hello!")
        result = msg2dict(msg, tmp_path)
        assert result["role"] == "user"
        assert result["content"] == "Hello!"
        assert "timestamp" in result
        # Timestamp is ISO 8601 format
        assert "T" in result["timestamp"] or "-" in result["timestamp"]

    def test_assistant_message(self, tmp_path):
        """Assistant role is preserved."""
        msg = Message(role="assistant", content="Hi there!")
        result = msg2dict(msg, tmp_path)
        assert result["role"] == "assistant"
        assert result["content"] == "Hi there!"

    def test_system_message(self, tmp_path):
        """System role is preserved."""
        msg = Message(role="system", content="You are helpful.")
        result = msg2dict(msg, tmp_path)
        assert result["role"] == "system"

    def test_message_without_files_has_no_files_key(self, tmp_path):
        """Message with no files omits the 'files' key."""
        msg = Message(role="user", content="No attachments")
        result = msg2dict(msg, tmp_path)
        assert "files" not in result

    def test_message_with_files_in_workspace(self, tmp_path):
        """Files inside workspace are converted to relative paths."""
        workspace = tmp_path.resolve()  # resolve handles macOS /var -> /private/var
        file_path = workspace / "image.png"
        file_path.touch()
        msg = Message(role="user", content="See this", files=[file_path])
        result = msg2dict(msg, workspace)
        assert "files" in result
        assert result["files"] == ["image.png"]

    def test_message_with_files_outside_workspace(self, tmp_path):
        """Files outside workspace keep absolute paths."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside_file = tmp_path / "external.png"
        outside_file.touch()
        msg = Message(role="user", content="External", files=[outside_file])
        result = msg2dict(msg, workspace)
        assert "files" in result
        assert result["files"] == [str(outside_file.resolve())]

    def test_message_with_uri_file(self, tmp_path):
        """URI files are returned as-is."""
        msg = Message(
            role="user", content="Remote", files=[URI("https://example.com/img.png")]
        )
        result = msg2dict(msg, tmp_path)
        assert "files" in result
        assert result["files"] == ["https://example.com/img.png"]

    def test_hide_true_included(self, tmp_path):
        """hide=True adds 'hide' key to output."""
        msg = Message(role="user", content="Hidden", hide=True)
        result = msg2dict(msg, tmp_path)
        assert result.get("hide") is True

    def test_hide_false_not_included(self, tmp_path):
        """hide=False omits 'hide' key from output."""
        msg = Message(role="user", content="Visible", hide=False)
        result = msg2dict(msg, tmp_path)
        assert "hide" not in result

    def test_timestamp_is_iso_format(self, tmp_path):
        """Timestamp is a valid ISO 8601 string."""
        from datetime import datetime, timezone

        ts = datetime(2025, 6, 15, 12, 30, 0, tzinfo=timezone.utc)
        msg = Message(role="user", content="Timed", timestamp=ts)
        result = msg2dict(msg, tmp_path)
        # Should contain the date portion
        assert "2025-06-15" in result["timestamp"]

    def test_multiple_files_all_converted(self, tmp_path):
        """Multiple files all get path conversion applied."""
        workspace = tmp_path
        f1 = workspace / "a.txt"
        f2 = workspace / "b.txt"
        f1.touch()
        f2.touch()
        msg = Message(role="user", content="Multi", files=[f1, f2])
        result = msg2dict(msg, workspace)
        assert result["files"] == ["a.txt", "b.txt"]
