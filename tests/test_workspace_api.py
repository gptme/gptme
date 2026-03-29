"""Tests for workspace API endpoints, including file upload."""

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Skip if flask not installed - MUST be before any imports that use flask
pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from gptme.server.workspace_api import allocate_attachment_path, safe_workspace_path


class TestSafeWorkspacePath:
    """Tests for safe_workspace_path security."""

    def test_resolves_within_workspace(self, tmp_path: Path) -> None:
        result = safe_workspace_path(tmp_path, "subdir/file.txt")
        assert result == tmp_path / "subdir" / "file.txt"

    def test_no_path_returns_workspace(self, tmp_path: Path) -> None:
        result = safe_workspace_path(tmp_path)
        assert result == tmp_path

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="escapes workspace"):
            safe_workspace_path(tmp_path, "../../etc/passwd")

    def test_rejects_absolute_path_outside(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="escapes workspace"):
            safe_workspace_path(tmp_path, "/etc/passwd")


class TestAllocateAttachmentPath:
    """Tests for attachment path collision handling."""

    def test_returns_original_name_when_available(self, tmp_path: Path) -> None:
        result = allocate_attachment_path(tmp_path, "report.pdf")
        assert result == tmp_path / "report.pdf"

    def test_suffixes_name_when_reserved(self, tmp_path: Path) -> None:
        result = allocate_attachment_path(tmp_path, "report.pdf", {"report.pdf"})
        assert result == tmp_path / "report-1.pdf"

    def test_suffixes_name_when_file_already_exists(self, tmp_path: Path) -> None:
        existing = tmp_path / "report.pdf"
        existing.write_text("existing")

        result = allocate_attachment_path(tmp_path, "report.pdf")

        assert result == tmp_path / "report-1.pdf"


@pytest.fixture
def app():
    """Create a minimal Flask app with the workspace API blueprint."""
    import flask

    from gptme.server.workspace_api import workspace_api

    app = flask.Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(workspace_api)
    return app


@pytest.fixture
def mock_logmanager(tmp_path: Path):
    """Mock LogManager.load to return a manager with a tmp logdir."""
    logdir = tmp_path / "conv-id"
    logdir.mkdir()

    manager = MagicMock()
    manager.logdir = logdir

    with patch("gptme.server.workspace_api.LogManager") as mock_cls:
        mock_cls.load.return_value = manager
        yield manager, logdir


@pytest.fixture
def mock_auth():
    """Disable auth for testing."""
    import gptme.server.auth as auth_mod

    original = auth_mod._auth_enabled
    auth_mod._auth_enabled = False
    yield
    auth_mod._auth_enabled = original


class TestUploadEndpoint:
    """Tests for the file upload endpoint."""

    def test_upload_single_file(self, app, mock_logmanager, mock_auth) -> None:
        _, logdir = mock_logmanager
        attachments_dir = logdir / "attachments"

        with app.test_client() as client:
            data = {"file": (io.BytesIO(b"hello world"), "test.txt")}
            resp = client.post(
                "/api/v2/conversations/test-conv/workspace/upload",
                data=data,
                content_type="multipart/form-data",
            )

        assert resp.status_code == 200
        result = resp.get_json()
        assert len(result["files"]) == 1
        assert result["files"][0]["name"] == "test.txt"
        # path is logdir-relative (e.g. "attachments/test.txt"), not absolute
        assert result["files"][0]["path"] == "attachments/test.txt"
        assert (attachments_dir / "test.txt").read_text() == "hello world"

    def test_upload_multiple_files(self, app, mock_logmanager, mock_auth) -> None:
        _, logdir = mock_logmanager
        attachments_dir = logdir / "attachments"

        with app.test_client() as client:
            data = {
                "file1": (io.BytesIO(b"content1"), "a.txt"),
                "file2": (io.BytesIO(b"content2"), "b.txt"),
            }
            resp = client.post(
                "/api/v2/conversations/test-conv/workspace/upload",
                data=data,
                content_type="multipart/form-data",
            )

        assert resp.status_code == 200
        result = resp.get_json()
        assert len(result["files"]) == 2
        assert (attachments_dir / "a.txt").read_text() == "content1"
        assert (attachments_dir / "b.txt").read_text() == "content2"

    def test_upload_no_files(self, app, mock_logmanager, mock_auth) -> None:
        with app.test_client() as client:
            resp = client.post(
                "/api/v2/conversations/test-conv/workspace/upload",
                data={},
                content_type="multipart/form-data",
            )

        assert resp.status_code == 400
        assert "No files provided" in resp.get_json()["error"]

    def test_upload_sanitizes_filename(self, app, mock_logmanager, mock_auth) -> None:
        _, logdir = mock_logmanager
        attachments_dir = logdir / "attachments"

        with app.test_client() as client:
            # Filename with path components should be stripped to just the name
            data = {"file": (io.BytesIO(b"content"), "../../evil.txt")}
            resp = client.post(
                "/api/v2/conversations/test-conv/workspace/upload",
                data=data,
                content_type="multipart/form-data",
            )

        assert resp.status_code == 200
        result = resp.get_json()
        assert result["files"][0]["name"] == "evil.txt"
        # File should be in attachments dir, not escaped
        assert (attachments_dir / "evil.txt").exists()

    def test_upload_rejects_oversized_file(
        self, app, mock_logmanager, mock_auth
    ) -> None:
        with app.test_client() as client:
            # 51MB file
            big_content = b"x" * (51 * 1024 * 1024)
            data = {"file": (io.BytesIO(big_content), "big.bin")}
            resp = client.post(
                "/api/v2/conversations/test-conv/workspace/upload",
                data=data,
                content_type="multipart/form-data",
            )

        assert resp.status_code == 413
        assert "exceeds 50MB" in resp.get_json()["error"]

    def test_upload_rollback_on_oversized_file(
        self, app, mock_logmanager, mock_auth
    ) -> None:
        """No files should be written if any file in the batch exceeds the size limit."""
        _, logdir = mock_logmanager
        attachments_dir = logdir / "attachments"

        small_content = b"small file"
        big_content = b"x" * (51 * 1024 * 1024)
        data = {
            "file1": (io.BytesIO(small_content), "small.txt"),
            "file2": (io.BytesIO(big_content), "big.bin"),
        }
        with app.test_client() as client:
            resp = client.post(
                "/api/v2/conversations/test-conv/workspace/upload",
                data=data,
                content_type="multipart/form-data",
            )

        assert resp.status_code == 413
        # small.txt must NOT have been written to disk
        assert not (attachments_dir / "small.txt").exists()

    def test_upload_skips_hidden_files(self, app, mock_logmanager, mock_auth) -> None:
        with app.test_client() as client:
            data = {"file": (io.BytesIO(b"hidden"), ".hidden")}
            resp = client.post(
                "/api/v2/conversations/test-conv/workspace/upload",
                data=data,
                content_type="multipart/form-data",
            )

        # Hidden files are skipped, so no valid files uploaded
        assert resp.status_code == 400
        assert "No valid files" in resp.get_json()["error"]

    def test_upload_returns_404_for_missing_conversation(self, app, mock_auth) -> None:
        with patch("gptme.server.workspace_api.LogManager") as mock_cls:
            mock_cls.load.side_effect = FileNotFoundError("missing conversation")

            with app.test_client() as client:
                data = {"file": (io.BytesIO(b"hello world"), "test.txt")}
                resp = client.post(
                    "/api/v2/conversations/missing/workspace/upload",
                    data=data,
                    content_type="multipart/form-data",
                )

        assert resp.status_code == 404
        assert resp.get_json() == {"error": "Conversation not found"}

    def test_upload_binary_file(self, app, mock_logmanager, mock_auth) -> None:
        _, logdir = mock_logmanager
        attachments_dir = logdir / "attachments"
        binary_content = bytes(range(256))

        with app.test_client() as client:
            data = {"file": (io.BytesIO(binary_content), "image.png")}
            resp = client.post(
                "/api/v2/conversations/test-conv/workspace/upload",
                data=data,
                content_type="multipart/form-data",
            )

        assert resp.status_code == 200
        assert (attachments_dir / "image.png").read_bytes() == binary_content

    def test_upload_deduplicates_colliding_sanitized_names(
        self, app, mock_logmanager, mock_auth
    ) -> None:
        _, logdir = mock_logmanager
        attachments_dir = logdir / "attachments"

        with app.test_client() as client:
            data = {
                "file1": (io.BytesIO(b"first"), "docs/report.pdf"),
                "file2": (io.BytesIO(b"second"), "archive/report.pdf"),
            }
            resp = client.post(
                "/api/v2/conversations/test-conv/workspace/upload",
                data=data,
                content_type="multipart/form-data",
            )

        assert resp.status_code == 200
        result = resp.get_json()
        assert [file["name"] for file in result["files"]] == [
            "report.pdf",
            "report-1.pdf",
        ]
        assert (attachments_dir / "report.pdf").read_text() == "first"
        assert (attachments_dir / "report-1.pdf").read_text() == "second"

    def test_upload_deduplicates_against_existing_attachments(
        self, app, mock_logmanager, mock_auth
    ) -> None:
        _, logdir = mock_logmanager
        attachments_dir = logdir / "attachments"
        attachments_dir.mkdir()
        (attachments_dir / "report.pdf").write_text("existing")

        with app.test_client() as client:
            data = {"file": (io.BytesIO(b"new"), "report.pdf")}
            resp = client.post(
                "/api/v2/conversations/test-conv/workspace/upload",
                data=data,
                content_type="multipart/form-data",
            )

        assert resp.status_code == 200
        result = resp.get_json()
        assert result["files"][0]["name"] == "report-1.pdf"
        assert (attachments_dir / "report.pdf").read_text() == "existing"
        assert (attachments_dir / "report-1.pdf").read_text() == "new"


@pytest.fixture
def mock_workspace(tmp_path: Path):
    """Mock LogManager.load to return a manager with a workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    manager = MagicMock()
    manager.workspace = workspace

    with patch("gptme.server.workspace_api.LogManager") as mock_cls:
        mock_cls.load.return_value = manager
        yield manager, workspace


class TestDownloadEndpoint:
    """Tests for the file download endpoint."""

    def test_download_text_file(self, app, mock_workspace, mock_auth) -> None:
        _, workspace = mock_workspace
        (workspace / "readme.txt").write_text("hello world")

        with app.test_client() as client:
            resp = client.get(
                "/api/v2/conversations/test-conv/workspace/readme.txt/download"
            )

        assert resp.status_code == 200
        assert resp.data == b"hello world"
        assert resp.headers["Content-Disposition"].startswith("attachment")
        assert "readme.txt" in resp.headers["Content-Disposition"]

    def test_download_binary_file(self, app, mock_workspace, mock_auth) -> None:
        _, workspace = mock_workspace
        content = bytes(range(256))
        (workspace / "data.bin").write_bytes(content)

        with app.test_client() as client:
            resp = client.get(
                "/api/v2/conversations/test-conv/workspace/data.bin/download"
            )

        assert resp.status_code == 200
        assert resp.data == content

    def test_download_file_not_found(self, app, mock_workspace, mock_auth) -> None:
        with app.test_client() as client:
            resp = client.get(
                "/api/v2/conversations/test-conv/workspace/missing.txt/download"
            )

        assert resp.status_code == 404

    def test_download_rejects_path_traversal(
        self, app, mock_workspace, mock_auth
    ) -> None:
        with app.test_client() as client:
            resp = client.get(
                "/api/v2/conversations/test-conv/workspace/..%2F..%2Fetc%2Fpasswd/download"
            )

        assert resp.status_code == 400

    def test_download_nested_file(self, app, mock_workspace, mock_auth) -> None:
        _, workspace = mock_workspace
        subdir = workspace / "src"
        subdir.mkdir()
        (subdir / "main.py").write_text("print('hi')")

        with app.test_client() as client:
            resp = client.get(
                "/api/v2/conversations/test-conv/workspace/src/main.py/download"
            )

        assert resp.status_code == 200
        assert resp.data == b"print('hi')"


@pytest.fixture
def mock_logmanager_full(tmp_path: Path):
    """Mock LogManager with both logdir and workspace (for serve endpoint tests)."""
    logdir = tmp_path / "conv-id"
    logdir.mkdir()
    workspace = logdir / "workspace"
    workspace.mkdir()

    manager = MagicMock()
    manager.logdir = logdir
    manager.workspace = workspace

    with patch("gptme.server.workspace_api.LogManager") as mock_cls:
        mock_cls.load.return_value = manager
        yield manager, logdir, workspace


class TestServeConversationFileEndpoint:
    """Tests for the /api/v2/conversations/<id>/files/<path> endpoint."""

    def test_serve_attachment_file(self, app, mock_logmanager_full, mock_auth) -> None:
        _, logdir, _ = mock_logmanager_full
        attachments_dir = logdir / "attachments"
        attachments_dir.mkdir()
        (attachments_dir / "photo.jpg").write_bytes(b"\xff\xd8\xff")  # minimal JPEG

        with app.test_client() as client:
            resp = client.get(
                "/api/v2/conversations/test-conv/files/attachments/photo.jpg"
            )

        assert resp.status_code == 200
        assert resp.data == b"\xff\xd8\xff"

    def test_serve_workspace_file(self, app, mock_logmanager_full, mock_auth) -> None:
        _, logdir, workspace = mock_logmanager_full
        (workspace / "script.py").write_text("print('hello')")

        with app.test_client() as client:
            resp = client.get(
                "/api/v2/conversations/test-conv/files/workspace/script.py"
            )

        assert resp.status_code == 200
        assert resp.data == b"print('hello')"

    def test_serve_returns_404_for_missing_file(
        self, app, mock_logmanager_full, mock_auth
    ) -> None:
        with app.test_client() as client:
            resp = client.get(
                "/api/v2/conversations/test-conv/files/attachments/missing.txt"
            )

        assert resp.status_code == 404

    def test_serve_rejects_path_traversal(
        self, app, mock_logmanager_full, mock_auth
    ) -> None:
        with app.test_client() as client:
            resp = client.get(
                "/api/v2/conversations/test-conv/files/..%2F..%2Fetc%2Fpasswd"
            )

        assert resp.status_code == 400

    def test_serve_text_file_with_correct_mime(
        self, app, mock_logmanager_full, mock_auth
    ) -> None:
        _, logdir, _ = mock_logmanager_full
        attachments_dir = logdir / "attachments"
        attachments_dir.mkdir()
        (attachments_dir / "readme.txt").write_text("hello")

        with app.test_client() as client:
            resp = client.get(
                "/api/v2/conversations/test-conv/files/attachments/readme.txt"
            )

        assert resp.status_code == 200
        assert "text/plain" in resp.content_type
