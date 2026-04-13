"""Tests for the workspace API endpoints.

Tests browse_workspace and preview_file endpoints, including:
- Directory listing with hidden files
- File metadata
- File preview (text, binary, images)
- Path traversal protection
- Error handling
"""

from pathlib import Path
from uuid import uuid4

import pytest

# Skip if flask not installed
pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

from gptme.server.workspace_api import (  # fmt: skip
    WorkspaceFile,
    list_directory,
    safe_workspace_path,
)

# Mark tests that require the server and add timeouts
pytestmark = [pytest.mark.timeout(10)]


# ============================================================
# Unit tests for helper functions
# ============================================================


class TestSafeWorkspacePath:
    """Tests for the safe_workspace_path function."""

    def test_no_subpath_returns_workspace(self, tmp_path: Path):
        """With no subpath, returns the workspace root."""
        result = safe_workspace_path(tmp_path)
        assert result == tmp_path.resolve()

    def test_none_subpath_returns_workspace(self, tmp_path: Path):
        """None subpath returns the workspace root."""
        result = safe_workspace_path(tmp_path, None)
        assert result == tmp_path.resolve()

    def test_valid_subpath(self, tmp_path: Path):
        """Valid subpath resolves within workspace."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        result = safe_workspace_path(tmp_path, "subdir")
        assert result == subdir.resolve()

    def test_nested_subpath(self, tmp_path: Path):
        """Nested subpath resolves correctly."""
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        result = safe_workspace_path(tmp_path, "a/b/c")
        assert result == nested.resolve()

    def test_path_traversal_blocked(self, tmp_path: Path):
        """Path traversal with .. is blocked."""
        with pytest.raises(ValueError, match="Path escapes workspace"):
            safe_workspace_path(tmp_path, "../../../etc/passwd")

    def test_path_traversal_mixed(self, tmp_path: Path):
        """Mixed path with traversal that escapes is blocked."""
        with pytest.raises(ValueError, match="Path escapes workspace"):
            safe_workspace_path(tmp_path, "subdir/../../..")

    def test_path_traversal_within_workspace_ok(self, tmp_path: Path):
        """Path with .. that stays within workspace is allowed."""
        subdir = tmp_path / "a" / "b"
        subdir.mkdir(parents=True)
        # a/b/../b stays within workspace
        result = safe_workspace_path(tmp_path, "a/b/../b")
        assert result == subdir.resolve()

    def test_absolute_path_in_subpath(self, tmp_path: Path):
        """Absolute path in subpath resolves relative to workspace."""
        # Path("/foo") joined with workspace still resolves
        # The behavior depends on pathlib: (workspace / "/foo") = Path("/foo")
        # which would escape workspace
        with pytest.raises(ValueError, match="Path escapes workspace"):
            safe_workspace_path(tmp_path, "/etc/passwd")

    def test_empty_string_subpath(self, tmp_path: Path):
        """Empty string subpath returns workspace root."""
        result = safe_workspace_path(tmp_path, "")
        assert result == tmp_path.resolve()

    def test_symlink_escape_blocked(self, tmp_path: Path):
        """Symlink that escapes workspace is blocked."""
        # Create a symlink pointing outside workspace
        link = tmp_path / "escape_link"
        link.symlink_to("/tmp")
        with pytest.raises(ValueError, match="Path escapes workspace"):
            safe_workspace_path(tmp_path, "escape_link")


class TestWorkspaceFile:
    """Tests for the WorkspaceFile dataclass."""

    def test_is_dir(self, tmp_path: Path):
        """Test is_dir property."""
        subdir = tmp_path / "mydir"
        subdir.mkdir()
        wf = WorkspaceFile(subdir, tmp_path)
        assert wf.is_dir is True

    def test_is_not_dir(self, tmp_path: Path):
        """Test is_dir property for regular file."""
        f = tmp_path / "file.txt"
        f.write_text("hello")
        wf = WorkspaceFile(f, tmp_path)
        assert wf.is_dir is False

    def test_is_hidden(self, tmp_path: Path):
        """Test hidden file detection."""
        hidden = tmp_path / ".hidden"
        hidden.write_text("secret")
        wf = WorkspaceFile(hidden, tmp_path)
        assert wf.is_hidden is True

    def test_is_not_hidden(self, tmp_path: Path):
        """Test non-hidden file detection."""
        visible = tmp_path / "visible.txt"
        visible.write_text("hello")
        wf = WorkspaceFile(visible, tmp_path)
        assert wf.is_hidden is False

    def test_relative_path(self, tmp_path: Path):
        """Test relative_path property."""
        nested = tmp_path / "a" / "b" / "file.txt"
        nested.parent.mkdir(parents=True)
        nested.write_text("content")
        wf = WorkspaceFile(nested, tmp_path)
        assert wf.relative_path == "a/b/file.txt"

    def test_relative_path_root(self, tmp_path: Path):
        """Test relative_path for file at workspace root."""
        f = tmp_path / "root.txt"
        f.write_text("content")
        wf = WorkspaceFile(f, tmp_path)
        assert wf.relative_path == "root.txt"

    def test_mime_type_text(self, tmp_path: Path):
        """Test MIME type for text file."""
        f = tmp_path / "file.txt"
        f.write_text("hello")
        wf = WorkspaceFile(f, tmp_path)
        assert wf.mime_type is not None
        assert "text" in wf.mime_type

    def test_mime_type_python(self, tmp_path: Path):
        """Test MIME type for Python file."""
        f = tmp_path / "script.py"
        f.write_text("print('hello')")
        wf = WorkspaceFile(f, tmp_path)
        # Python files may return text/x-python or similar
        assert wf.mime_type is not None

    def test_mime_type_json(self, tmp_path: Path):
        """Test MIME type for JSON file."""
        f = tmp_path / "data.json"
        f.write_text('{"key": "value"}')
        wf = WorkspaceFile(f, tmp_path)
        assert wf.mime_type == "application/json"

    def test_mime_type_directory(self, tmp_path: Path):
        """Test MIME type is None for directory."""
        d = tmp_path / "subdir"
        d.mkdir()
        wf = WorkspaceFile(d, tmp_path)
        assert wf.mime_type is None

    def test_mime_type_unknown(self, tmp_path: Path):
        """Test MIME type for unknown extension."""
        f = tmp_path / "file.xyz123"
        f.write_text("data")
        wf = WorkspaceFile(f, tmp_path)
        # Unknown extensions return None
        assert wf.mime_type is None

    def test_is_text_for_text_file(self, tmp_path: Path):
        """Test is_text for a text file."""
        f = tmp_path / "file.txt"
        f.write_text("hello world")
        wf = WorkspaceFile(f, tmp_path)
        assert wf.is_text is True

    def test_is_text_for_json(self, tmp_path: Path):
        """Test is_text for a JSON file."""
        f = tmp_path / "data.json"
        f.write_text('{"a": 1}')
        wf = WorkspaceFile(f, tmp_path)
        assert wf.is_text is True

    def test_is_text_for_binary(self, tmp_path: Path):
        """Test is_text for a binary file."""
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x02\xff\xfe\x80\x81")
        wf = WorkspaceFile(f, tmp_path)
        assert wf.is_text is False

    def test_is_text_for_utf8_without_mime(self, tmp_path: Path):
        """Test is_text fallback to content detection for unknown extension."""
        f = tmp_path / "data.customext"
        f.write_text("readable text content")
        wf = WorkspaceFile(f, tmp_path)
        assert wf.is_text is True

    def test_to_dict(self, tmp_path: Path):
        """Test to_dict returns expected structure."""
        f = tmp_path / "file.txt"
        f.write_text("hello")
        wf = WorkspaceFile(f, tmp_path)
        d = wf.to_dict()
        assert d["name"] == "file.txt"
        assert d["path"] == "file.txt"
        assert d["type"] == "file"
        assert d["size"] == 5
        assert "modified" in d
        assert "T" in d["modified"]  # ISO format

    def test_to_dict_directory(self, tmp_path: Path):
        """Test to_dict for a directory."""
        d = tmp_path / "subdir"
        d.mkdir()
        wf = WorkspaceFile(d, tmp_path)
        result = wf.to_dict()
        assert result["name"] == "subdir"
        assert result["type"] == "directory"
        assert result["mime_type"] is None


class TestListDirectory:
    """Tests for the list_directory function."""

    def test_list_empty_dir(self, tmp_path: Path):
        """Test listing an empty directory."""
        empty = tmp_path / "empty"
        empty.mkdir()
        result = list_directory(empty, tmp_path)
        assert result == []

    def test_list_files_and_dirs(self, tmp_path: Path):
        """Test listing a directory with files and subdirectories."""
        (tmp_path / "file_b.txt").write_text("b")
        (tmp_path / "file_a.txt").write_text("a")
        (tmp_path / "subdir").mkdir()
        result = list_directory(tmp_path, tmp_path)
        # Directories should come first, then files, both sorted by name
        assert result[0]["name"] == "subdir"
        assert result[0]["type"] == "directory"
        assert result[1]["name"] == "file_a.txt"
        assert result[2]["name"] == "file_b.txt"

    def test_hidden_files_excluded_by_default(self, tmp_path: Path):
        """Test that hidden files are excluded by default."""
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "visible.txt").write_text("hello")
        result = list_directory(tmp_path, tmp_path, show_hidden=False)
        names = [f["name"] for f in result]
        assert ".hidden" not in names
        assert "visible.txt" in names

    def test_hidden_files_included_when_requested(self, tmp_path: Path):
        """Test that hidden files are included when show_hidden=True."""
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "visible.txt").write_text("hello")
        result = list_directory(tmp_path, tmp_path, show_hidden=True)
        names = [f["name"] for f in result]
        assert ".hidden" in names
        assert "visible.txt" in names

    def test_sort_order_dirs_first(self, tmp_path: Path):
        """Test sorting: directories before files."""
        (tmp_path / "z_file.txt").write_text("z")
        (tmp_path / "a_dir").mkdir()
        (tmp_path / "m_file.txt").write_text("m")
        (tmp_path / "b_dir").mkdir()
        result = list_directory(tmp_path, tmp_path)
        types = [f["type"] for f in result]
        # All directories should come before all files
        dir_end = max(i for i, t in enumerate(types) if t == "directory")
        file_start = min(i for i, t in enumerate(types) if t == "file")
        assert dir_end < file_start

    def test_not_a_directory_raises(self, tmp_path: Path):
        """Test that listing a file raises ValueError."""
        f = tmp_path / "file.txt"
        f.write_text("hello")
        with pytest.raises(ValueError, match="not a directory"):
            list_directory(f, tmp_path)


# ============================================================
# Integration tests for API endpoints
# ============================================================


@pytest.fixture
def workspace_conv(client: FlaskClient, tmp_path: Path):
    """Create a conversation with a workspace containing test files."""
    convname = f"test-workspace-{uuid4().hex[:8]}"

    # Create conversation
    response = client.put(
        f"/api/v2/conversations/{convname}",
        json={"prompt": "Test workspace."},
    )
    assert response.status_code == 200

    # Get the conversation's log directory and create workspace symlink
    from gptme.logmanager import LogManager

    manager = LogManager.load(convname, lock=False)

    # Create a temp workspace with test files
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Create test files
    (workspace / "readme.txt").write_text("Hello World")
    (workspace / "script.py").write_text("print('hello')")
    (workspace / "data.json").write_text('{"key": "value"}')
    (workspace / ".hidden").write_text("secret")

    # Create subdirectory with files
    subdir = workspace / "src"
    subdir.mkdir()
    (subdir / "main.py").write_text("def main(): pass")

    # Create binary file
    (workspace / "binary.bin").write_bytes(b"\x00\x01\x02\xff" * 100)

    # Create image file (minimal valid PNG)
    png_header = (
        b"\x89PNG\r\n\x1a\n"  # PNG signature
        b"\x00\x00\x00\rIHDR"  # IHDR chunk
        b"\x00\x00\x00\x01"  # width 1
        b"\x00\x00\x00\x01"  # height 1
        b"\x08\x02"  # bit depth 8, color type RGB
        b"\x00\x00\x00"  # compression, filter, interlace
        b"\x90wS\xde"  # CRC
        b"\x00\x00\x00\x0cIDATx"  # IDAT chunk
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"
        b"\x18\xd8N"  # CRC
        b"\x00\x00\x00\x00IEND"  # IEND chunk
        b"\xaeB`\x82"  # CRC
    )
    (workspace / "image.png").write_bytes(png_header)

    # Symlink the workspace directory
    workspace_link = manager.logdir / "workspace"
    if workspace_link.exists() or workspace_link.is_symlink():
        workspace_link.unlink()
    workspace_link.symlink_to(workspace)

    yield {
        "conversation_id": convname,
        "workspace": workspace,
    }

    # Cleanup: remove the conversation log directory
    import shutil

    if manager.logdir.exists():
        shutil.rmtree(manager.logdir)


class TestBrowseWorkspaceEndpoint:
    """Tests for the browse_workspace API endpoint."""

    def test_list_workspace_root(self, client: FlaskClient, workspace_conv):
        """Test listing workspace root directory."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(f"/api/v2/conversations/{conv_id}/workspace")
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        names = [f["name"] for f in data]
        assert "readme.txt" in names
        assert "script.py" in names
        assert "src" in names
        # Hidden files excluded by default
        assert ".hidden" not in names

    def test_list_workspace_with_hidden(self, client: FlaskClient, workspace_conv):
        """Test listing workspace with hidden files."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace?show_hidden=true"
        )
        assert response.status_code == 200
        data = response.get_json()
        names = [f["name"] for f in data]
        assert ".hidden" in names

    def test_list_subdirectory(self, client: FlaskClient, workspace_conv):
        """Test listing a subdirectory."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(f"/api/v2/conversations/{conv_id}/workspace/src")
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        names = [f["name"] for f in data]
        assert "main.py" in names

    def test_get_single_file_metadata(self, client: FlaskClient, workspace_conv):
        """Test getting metadata for a single file."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(f"/api/v2/conversations/{conv_id}/workspace/readme.txt")
        assert response.status_code == 200
        data = response.get_json()
        assert data["name"] == "readme.txt"
        assert data["type"] == "file"
        assert data["size"] == len("Hello World")

    def test_path_traversal_blocked(self, client: FlaskClient, workspace_conv):
        """Test that path traversal is blocked."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/../../../etc/passwd"
        )
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data

    def test_nonexistent_conversation(self, client: FlaskClient):
        """Test browsing workspace of nonexistent conversation."""
        response = client.get("/api/v2/conversations/nonexistent-conv-999/workspace")
        assert response.status_code == 404

    def test_file_metadata_fields(self, client: FlaskClient, workspace_conv):
        """Test that file metadata contains all expected fields."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(f"/api/v2/conversations/{conv_id}/workspace/data.json")
        assert response.status_code == 200
        data = response.get_json()
        assert "name" in data
        assert "path" in data
        assert "type" in data
        assert "size" in data
        assert "modified" in data
        assert "mime_type" in data
        assert data["mime_type"] == "application/json"

    def test_directory_listing_sorted(self, client: FlaskClient, workspace_conv):
        """Test that directory listing is sorted: dirs first, then files."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(f"/api/v2/conversations/{conv_id}/workspace")
        assert response.status_code == 200
        data = response.get_json()
        types = [f["type"] for f in data]
        # Find boundary between dirs and files
        dirs = [i for i, t in enumerate(types) if t == "directory"]
        files = [i for i, t in enumerate(types) if t == "file"]
        if dirs and files:
            assert max(dirs) < min(files)

    def test_nested_file_metadata(self, client: FlaskClient, workspace_conv):
        """Test metadata for a file in a subdirectory."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(f"/api/v2/conversations/{conv_id}/workspace/src/main.py")
        assert response.status_code == 200
        data = response.get_json()
        assert data["name"] == "main.py"
        assert data["path"] == "src/main.py"


class TestPreviewFileEndpoint:
    """Tests for the preview_file API endpoint."""

    def test_preview_text_file(self, client: FlaskClient, workspace_conv):
        """Test previewing a text file."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/readme.txt/preview"
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["type"] == "text"
        assert data["content"] == "Hello World"

    def test_preview_python_file(self, client: FlaskClient, workspace_conv):
        """Test previewing a Python file."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/script.py/preview"
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["type"] == "text"
        assert "print" in data["content"]

    def test_preview_json_file(self, client: FlaskClient, workspace_conv):
        """Test previewing a JSON file."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/data.json/preview"
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["type"] == "text"
        assert '"key"' in data["content"]

    def test_preview_binary_file(self, client: FlaskClient, workspace_conv):
        """Test previewing a binary file returns metadata only."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/binary.bin/preview"
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["type"] == "binary"
        assert "metadata" in data
        assert data["metadata"]["name"] == "binary.bin"

    def test_preview_image_file(self, client: FlaskClient, workspace_conv):
        """Test previewing an image returns binary data."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/image.png/preview"
        )
        assert response.status_code == 200
        # Image files are returned as binary via send_file
        assert response.content_type.startswith("image/png")

    def test_preview_nonexistent_file(self, client: FlaskClient, workspace_conv):
        """Test previewing a file that doesn't exist."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/nonexistent.txt/preview"
        )
        assert response.status_code == 404
        data = response.get_json()
        assert "error" in data

    def test_preview_directory_returns_404(self, client: FlaskClient, workspace_conv):
        """Test that previewing a directory returns 404."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(f"/api/v2/conversations/{conv_id}/workspace/src/preview")
        assert response.status_code == 404

    def test_preview_path_traversal_blocked(self, client: FlaskClient, workspace_conv):
        """Test that path traversal is blocked in preview."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/../../../etc/passwd/preview"
        )
        assert response.status_code == 400

    def test_preview_nested_file(self, client: FlaskClient, workspace_conv):
        """Test previewing a file in a subdirectory."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/src/main.py/preview"
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["type"] == "text"
        assert "def main" in data["content"]

    def test_preview_nonexistent_conversation(self, client: FlaskClient):
        """Test previewing file in nonexistent conversation."""
        response = client.get(
            "/api/v2/conversations/nonexistent-conv-999/workspace/file.txt/preview"
        )
        assert response.status_code == 404

    def test_preview_hidden_file(self, client: FlaskClient, workspace_conv):
        """Test previewing a hidden file (should work - preview doesn't filter)."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/.hidden/preview"
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["type"] == "text"
        assert data["content"] == "secret"


class TestDownloadFileEndpoint:
    """Tests for the download_file API endpoint."""

    def test_download_text_file(self, client: FlaskClient, workspace_conv):
        """Test downloading a text file returns raw content."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/readme.txt/download"
        )
        assert response.status_code == 200
        assert response.data == b"Hello World"
        assert "text" in response.content_type
        assert "attachment" in response.headers.get("Content-Disposition", "")
        assert "readme.txt" in response.headers.get("Content-Disposition", "")

    def test_download_binary_file(self, client: FlaskClient, workspace_conv):
        """Test downloading a binary file returns raw bytes."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/binary.bin/download"
        )
        assert response.status_code == 200
        assert response.data == b"\x00\x01\x02\xff" * 100
        assert "application/octet-stream" in response.content_type
        assert "binary.bin" in response.headers.get("Content-Disposition", "")

    def test_download_image_file(self, client: FlaskClient, workspace_conv):
        """Test downloading an image file."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/image.png/download"
        )
        assert response.status_code == 200
        assert response.content_type.startswith("image/png")
        assert response.data[:4] == b"\x89PNG"
        assert "image.png" in response.headers.get("Content-Disposition", "")

    def test_download_json_file(self, client: FlaskClient, workspace_conv):
        """Test downloading a JSON file returns raw JSON content."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/data.json/download"
        )
        assert response.status_code == 200
        assert response.data == b'{"key": "value"}'
        assert "application/json" in response.content_type

    def test_download_nested_file(self, client: FlaskClient, workspace_conv):
        """Test downloading a file from a subdirectory."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/src/main.py/download"
        )
        assert response.status_code == 200
        assert b"def main" in response.data
        assert "main.py" in response.headers.get("Content-Disposition", "")

    def test_download_nonexistent_file(self, client: FlaskClient, workspace_conv):
        """Test downloading a file that doesn't exist returns 404."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/nonexistent.txt/download"
        )
        assert response.status_code == 404

    def test_download_path_traversal_blocked(self, client: FlaskClient, workspace_conv):
        """Test that path traversal is blocked in downloads."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/../../../etc/passwd/download"
        )
        assert response.status_code == 400

    def test_download_nonexistent_conversation(self, client: FlaskClient):
        """Test downloading from nonexistent conversation returns 404."""
        response = client.get(
            "/api/v2/conversations/nonexistent-conv-999/workspace/file.txt/download"
        )
        assert response.status_code == 404

    def test_download_directory_returns_404(self, client: FlaskClient, workspace_conv):
        """Test that downloading a directory returns 404."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(f"/api/v2/conversations/{conv_id}/workspace/src/download")
        assert response.status_code == 404


class TestWorkspaceEdgeCases:
    """Edge cases and error handling tests."""

    def test_empty_workspace(self, client: FlaskClient, tmp_path: Path):
        """Test browsing an empty workspace."""
        convname = f"test-workspace-empty-{uuid4().hex[:8]}"
        response = client.put(
            f"/api/v2/conversations/{convname}",
            json={"prompt": "Test."},
        )
        assert response.status_code == 200

        from gptme.logmanager import LogManager

        manager = LogManager.load(convname, lock=False)
        # Create an empty temp dir and symlink as workspace
        empty_ws = tmp_path / "empty_workspace"
        empty_ws.mkdir()
        workspace_link = manager.logdir / "workspace"
        if workspace_link.exists() or workspace_link.is_symlink():
            workspace_link.unlink()
        workspace_link.symlink_to(empty_ws)

        response = client.get(f"/api/v2/conversations/{convname}/workspace")
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_large_file_preview(self, client: FlaskClient, workspace_conv):
        """Test previewing a large text file."""
        workspace = workspace_conv["workspace"]
        large_file = workspace / "large.txt"
        large_file.write_text("x" * 100_000)

        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/large.txt/preview"
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["type"] == "text"
        assert len(data["content"]) == 100_000

    def test_large_file_truncated(
        self, client: FlaskClient, workspace_conv, monkeypatch
    ):
        """Test that files exceeding the preview limit are truncated."""
        import gptme.server.workspace_api as ws_mod

        # Use a tiny limit so we can test truncation without huge files
        monkeypatch.setattr(ws_mod, "MAX_PREVIEW_BYTES", 50)

        workspace = workspace_conv["workspace"]
        large_file = workspace / "huge.txt"
        large_file.write_text("a" * 200)

        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/huge.txt/preview"
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["type"] == "text"
        assert len(data["content"]) == 50
        assert data["truncated"] is True
        assert data["total_size"] == 200

    def test_small_file_not_truncated(self, client: FlaskClient, workspace_conv):
        """Test that small files have no truncation metadata."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/readme.txt/preview"
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["type"] == "text"
        assert "truncated" not in data

    def test_special_characters_in_filename(self, client: FlaskClient, workspace_conv):
        """Test files with special characters in names."""
        workspace = workspace_conv["workspace"]
        special = workspace / "file with spaces.txt"
        special.write_text("content")

        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/file with spaces.txt"
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["name"] == "file with spaces.txt"

    def test_unicode_filename(self, client: FlaskClient, workspace_conv):
        """Test files with unicode characters."""
        workspace = workspace_conv["workspace"]
        (workspace / "日本語.txt").write_text("content")

        conv_id = workspace_conv["conversation_id"]
        response = client.get(f"/api/v2/conversations/{conv_id}/workspace/日本語.txt")
        assert response.status_code == 200
        data = response.get_json()
        assert data["name"] == "日本語.txt"

    def test_empty_file_preview(self, client: FlaskClient, workspace_conv):
        """Test previewing an empty file."""
        workspace = workspace_conv["workspace"]
        (workspace / "empty.txt").write_text("")

        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace/empty.txt/preview"
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["type"] == "text"
        assert data["content"] == ""

    def test_show_hidden_false_explicit(self, client: FlaskClient, workspace_conv):
        """Test explicit show_hidden=false query parameter."""
        conv_id = workspace_conv["conversation_id"]
        response = client.get(
            f"/api/v2/conversations/{conv_id}/workspace?show_hidden=false"
        )
        assert response.status_code == 200
        data = response.get_json()
        names = [f["name"] for f in data]
        assert ".hidden" not in names

    def test_symlink_within_workspace(self, client: FlaskClient, workspace_conv):
        """Test that symlinks within workspace are followed."""
        workspace = workspace_conv["workspace"]
        link = workspace / "link_to_src"
        link.symlink_to(workspace / "src")

        conv_id = workspace_conv["conversation_id"]
        response = client.get(f"/api/v2/conversations/{conv_id}/workspace/link_to_src")
        assert response.status_code == 200
        data = response.get_json()
        # Should list contents of src through symlink
        names = [f["name"] for f in data]
        assert "main.py" in names

    def test_no_workspace_symlink_falls_back(self, client: FlaskClient):
        """Test browsing workspace when no symlink exists (falls back to logdir)."""
        convname = f"test-no-workspace-{uuid4().hex[:8]}"
        response = client.put(
            f"/api/v2/conversations/{convname}",
            json={"prompt": "Test."},
        )
        assert response.status_code == 200

        # Without explicit workspace symlink, workspace resolves to a default
        # path (usually the log dir). This should return 200 with a listing,
        # not 404, since the resolved path is a valid directory.
        response = client.get(f"/api/v2/conversations/{convname}/workspace")
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
