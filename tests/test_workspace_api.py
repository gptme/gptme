"""Tests for workspace API endpoints, including file upload."""

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.server.workspace_api import safe_workspace_path


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
    """Mock LogManager.load to return a manager with a tmp workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    manager = MagicMock()
    manager.workspace = workspace

    with patch("gptme.server.workspace_api.LogManager") as mock_cls:
        mock_cls.load.return_value = manager
        yield manager, workspace


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
        _, workspace = mock_logmanager

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
        assert (workspace / "test.txt").read_text() == "hello world"

    def test_upload_multiple_files(self, app, mock_logmanager, mock_auth) -> None:
        _, workspace = mock_logmanager

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
        assert (workspace / "a.txt").read_text() == "content1"
        assert (workspace / "b.txt").read_text() == "content2"

    def test_upload_to_subdirectory(self, app, mock_logmanager, mock_auth) -> None:
        _, workspace = mock_logmanager

        with app.test_client() as client:
            data = {
                "file": (io.BytesIO(b"data"), "file.txt"),
                "path": "subdir",
            }
            resp = client.post(
                "/api/v2/conversations/test-conv/workspace/upload",
                data=data,
                content_type="multipart/form-data",
            )

        assert resp.status_code == 200
        result = resp.get_json()
        assert result["files"][0]["path"] == "subdir/file.txt"
        assert (workspace / "subdir" / "file.txt").read_bytes() == b"data"

    def test_upload_no_files(self, app, mock_logmanager, mock_auth) -> None:
        with app.test_client() as client:
            resp = client.post(
                "/api/v2/conversations/test-conv/workspace/upload",
                data={},
                content_type="multipart/form-data",
            )

        assert resp.status_code == 400
        assert "No files provided" in resp.get_json()["error"]

    def test_upload_rejects_path_traversal(
        self, app, mock_logmanager, mock_auth
    ) -> None:
        with app.test_client() as client:
            data = {
                "file": (io.BytesIO(b"evil"), "test.txt"),
                "path": "../../etc",
            }
            resp = client.post(
                "/api/v2/conversations/test-conv/workspace/upload",
                data=data,
                content_type="multipart/form-data",
            )

        assert resp.status_code == 400
        assert "escapes" in resp.get_json()["error"]

    def test_upload_sanitizes_filename(self, app, mock_logmanager, mock_auth) -> None:
        _, workspace = mock_logmanager

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
        # File should be in workspace root, not escaped
        assert (workspace / "evil.txt").exists()

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

    def test_upload_workspace_not_found(self, app, mock_auth) -> None:
        manager = MagicMock()
        manager.workspace = Path("/nonexistent")

        with patch("gptme.server.workspace_api.LogManager") as mock_cls:
            mock_cls.load.return_value = manager
            with app.test_client() as client:
                data = {"file": (io.BytesIO(b"data"), "test.txt")}
                resp = client.post(
                    "/api/v2/conversations/test-conv/workspace/upload",
                    data=data,
                    content_type="multipart/form-data",
                )

        assert resp.status_code == 404

    def test_upload_skips_hidden_files(self, app, mock_logmanager, mock_auth) -> None:
        _, workspace = mock_logmanager

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

    def test_upload_binary_file(self, app, mock_logmanager, mock_auth) -> None:
        _, workspace = mock_logmanager
        binary_content = bytes(range(256))

        with app.test_client() as client:
            data = {"file": (io.BytesIO(binary_content), "image.png")}
            resp = client.post(
                "/api/v2/conversations/test-conv/workspace/upload",
                data=data,
                content_type="multipart/form-data",
            )

        assert resp.status_code == 200
        assert (workspace / "image.png").read_bytes() == binary_content
