"""Tests for path traversal validation on conversation_id and branch names.

Ensures all endpoints that accept conversation_id or branch parameters
reject path traversal attempts (CWE-22) before any file system operations occur.
"""

import base64

import pytest

# Minimal valid 1×1 white-pixel PNG, pre-computed so tests don't need PIL.
# Verified: `PIL.Image.open(io.BytesIO(_VALID_PNG)).format == "PNG"`
_VALID_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

# Skip if flask not installed
pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

pytestmark = [pytest.mark.timeout(10)]


# Payloads that bypass Flask's URL routing (no '/' so <string:> matches them)
# Payloads with '/' are already blocked by Flask routing (<string:> doesn't match '/')
TRAVERSAL_PAYLOADS = [
    "..",
    "test\\..\\..\\secret",
    "..\\..\\etc\\passwd",
]


def _assert_traversal_rejected(response):
    """Assert the response is a 400 with the path traversal error message."""
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert data["error"] == "Invalid conversation_id"


class TestSessionEndpointValidation:
    """Session API endpoints must reject path traversal in conversation_id."""

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_events_rejects_traversal(self, client: FlaskClient, payload: str):
        response = client.get(f"/api/v2/conversations/{payload}/events")
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_step_rejects_traversal(self, client: FlaskClient, payload: str):
        response = client.post(
            f"/api/v2/conversations/{payload}/step",
            json={"session_id": "fake"},
        )
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_tool_confirm_rejects_traversal(self, client: FlaskClient, payload: str):
        response = client.post(
            f"/api/v2/conversations/{payload}/tool/confirm",
            json={"tool_id": "fake", "action": "approve"},
        )
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_rerun_rejects_traversal(self, client: FlaskClient, payload: str):
        response = client.post(f"/api/v2/conversations/{payload}/rerun")
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_elicit_respond_rejects_traversal(self, client: FlaskClient, payload: str):
        response = client.post(
            f"/api/v2/conversations/{payload}/elicit/respond",
            json={"elicit_id": "fake", "response": "test"},
        )
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_interrupt_rejects_traversal(self, client: FlaskClient, payload: str):
        response = client.post(
            f"/api/v2/conversations/{payload}/interrupt",
            json={"session_id": "fake"},
        )
        _assert_traversal_rejected(response)


class TestWorkspaceEndpointValidation:
    """Workspace API endpoints must reject path traversal in conversation_id."""

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_browse_rejects_traversal(self, client: FlaskClient, payload: str):
        response = client.get(f"/api/v2/conversations/{payload}/workspace")
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_upload_rejects_traversal(self, client: FlaskClient, payload: str):
        response = client.post(f"/api/v2/conversations/{payload}/workspace/upload")
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_file_rejects_traversal(self, client: FlaskClient, payload: str):
        # Route is /files/<path:filepath>, not /workspace/file/...
        response = client.get(f"/api/v2/conversations/{payload}/files/test.py")
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_preview_rejects_traversal(self, client: FlaskClient, payload: str):
        # Route is /workspace/<path:filepath>/preview
        response = client.get(
            f"/api/v2/conversations/{payload}/workspace/test.py/preview"
        )
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_download_rejects_traversal(self, client: FlaskClient, payload: str):
        # Route is /workspace/<path:filepath>/download
        response = client.get(
            f"/api/v2/conversations/{payload}/workspace/test.py/download"
        )
        _assert_traversal_rejected(response)


class TestValidConversationIdAccepted:
    """Valid conversation_ids must not be rejected by path traversal checks."""

    def test_simple_name(self, client: FlaskClient):
        """A valid name passes validation (may fail later with 404/etc, not 400 traversal)."""
        response = client.get("/api/v2/conversations/valid-conv-name/workspace")
        # Should not be rejected as traversal — the error (if any) will be
        # about the conversation not existing, not about invalid ID
        if response.status_code == 400:
            data = response.get_json()
            assert data["error"] != "Invalid conversation_id"

    def test_name_with_numbers(self, client: FlaskClient):
        response = client.get("/api/v2/conversations/test-2026-04-03/workspace")
        if response.status_code == 400:
            data = response.get_json()
            assert data["error"] != "Invalid conversation_id"

    def test_name_with_dots_no_traversal(self, client: FlaskClient):
        """A single dot is fine (not '..')."""
        response = client.get("/api/v2/conversations/test.conv.name/workspace")
        if response.status_code == 400:
            data = response.get_json()
            assert data["error"] != "Invalid conversation_id"


BRANCH_TRAVERSAL_PAYLOADS = [
    "../../../etc/passwd",
    "..\\..\\secret",
    "..",
    "main/../../secret",
]


def _assert_branch_rejected(response):
    """Assert the response is a 400 with the branch validation error message."""
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert data["error"] == "Invalid branch name"


class TestBranchParameterValidation:
    """Branch parameters in request bodies must reject path traversal.

    Tested via the generate endpoint (POST /conversations/:id) which reaches
    branch validation after conversation_id validation. The step endpoint
    requires a live session, so branch validation there is tested via the
    unit test for _validate_branch below.
    """

    @pytest.mark.parametrize("payload", BRANCH_TRAVERSAL_PAYLOADS)
    def test_generate_rejects_branch_traversal(self, client: FlaskClient, payload: str):
        """POST /conversations/:id with traversal branch should be rejected."""
        response = client.post(
            "/api/v2/conversations/test-conv",
            json={
                "role": "user",
                "content": "hello",
                "branch": payload,
            },
        )
        _assert_branch_rejected(response)

    def test_valid_branch_accepted(self, client: FlaskClient):
        """Valid branch names like 'main' or 'feature-1' should not be rejected."""
        response = client.post(
            "/api/v2/conversations/test-conv",
            json={
                "role": "user",
                "content": "hello",
                "branch": "main",
            },
        )
        # Should not be rejected as traversal
        if response.status_code == 400:
            data = response.get_json()
            assert data["error"] != "Invalid branch name"


class TestAvatarPathSecurity:
    """Avatar endpoints must not serve non-image files."""

    def test_user_avatar_rejects_non_image_extension(
        self, client: FlaskClient, tmp_path, monkeypatch
    ):
        """User avatar path pointing to a non-image file must be rejected."""
        from unittest.mock import MagicMock

        fake_key = tmp_path / "id_rsa"
        fake_key.write_text("PRIVATE KEY")

        mock_config = MagicMock()
        mock_config.user.avatar = str(fake_key)

        monkeypatch.setattr("gptme.server.api_v2.load_user_config", lambda: mock_config)
        response = client.get("/api/v2/user/avatar")
        assert response.status_code == 400
        data = response.get_json()
        assert "image" in data["error"].lower()

    def test_user_avatar_accepts_image_extension(
        self, client: FlaskClient, tmp_path, monkeypatch
    ):
        """User avatar path pointing to a valid image file must be served."""
        from unittest.mock import MagicMock

        fake_img = tmp_path / "avatar.png"
        fake_img.write_bytes(_VALID_PNG)

        mock_config = MagicMock()
        mock_config.user.avatar = str(fake_img)

        monkeypatch.setattr("gptme.server.api_v2.load_user_config", lambda: mock_config)
        response = client.get("/api/v2/user/avatar")
        assert response.status_code == 200

    def test_user_avatar_rejects_dotfile_without_image_ext(
        self, client: FlaskClient, tmp_path, monkeypatch
    ):
        """Files like .env or .bashrc must be rejected even if they exist."""
        from unittest.mock import MagicMock

        fake_env = tmp_path / ".env"
        fake_env.write_text("SECRET=value")

        mock_config = MagicMock()
        mock_config.user.avatar = str(fake_env)

        monkeypatch.setattr("gptme.server.api_v2.load_user_config", lambda: mock_config)
        response = client.get("/api/v2/user/avatar")
        assert response.status_code == 400

    def test_agent_avatar_by_path_rejects_non_image_extension(
        self, client: FlaskClient, tmp_path, monkeypatch, auth_headers
    ):
        """Agent avatar endpoint must reject non-image files inside the workspace."""
        from unittest.mock import MagicMock

        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        fake_key = agent_dir / "id_rsa"
        fake_key.write_text("PRIVATE KEY")

        mock_config = MagicMock()
        mock_config.agent.avatar = "id_rsa"

        monkeypatch.setattr(
            "gptme.server.api_v2.get_project_config",
            lambda path, quiet=True: mock_config,
        )
        response = client.get(
            f"/api/v2/agents/avatar?path={agent_dir}",
            headers=auth_headers,
        )
        assert response.status_code == 400
        data = response.get_json()
        assert "image" in data["error"].lower()

    def test_agent_avatar_by_path_accepts_image_extension(
        self, client: FlaskClient, tmp_path, monkeypatch, auth_headers
    ):
        """Agent avatar endpoint should still serve valid image files."""
        from unittest.mock import MagicMock

        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        fake_img = agent_dir / "avatar.png"
        fake_img.write_bytes(_VALID_PNG)

        mock_config = MagicMock()
        mock_config.agent.avatar = "avatar.png"

        monkeypatch.setattr(
            "gptme.server.api_v2.get_project_config",
            lambda path, quiet=True: mock_config,
        )
        response = client.get(
            f"/api/v2/agents/avatar?path={agent_dir}",
            headers=auth_headers,
        )
        assert response.status_code == 200

    def test_conversation_agent_avatar_rejects_non_image_extension(
        self, client: FlaskClient, tmp_path, monkeypatch
    ):
        """Conversation agent avatar endpoint must reject non-image files."""
        from unittest.mock import MagicMock

        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        fake_key = agent_dir / "id_rsa"
        fake_key.write_text("PRIVATE KEY")

        mock_chat_config = MagicMock()
        mock_chat_config.agent_config.avatar = "id_rsa"
        mock_chat_config.agent = agent_dir

        monkeypatch.setattr(
            "gptme.server.api_v2.ChatConfig.load_or_create",
            lambda logdir, default: mock_chat_config,
        )
        response = client.get("/api/v2/conversations/test-conv/agent/avatar")
        assert response.status_code == 400
        data = response.get_json()
        assert "image" in data["error"].lower()

    def test_user_avatar_rejects_disguised_non_image(
        self, client: FlaskClient, tmp_path, monkeypatch
    ):
        """A file with an image extension but non-image content must be rejected.

        Extension checks alone can be bypassed by renaming a file (e.g.
        ``secrets.env`` → ``avatar.jpg``).  This test ensures content-based
        validation (Pillow magic-byte detection) catches such files.
        """
        from unittest.mock import MagicMock

        disguised = tmp_path / "avatar.jpg"
        disguised.write_text("PRIVATE KEY MATERIAL\nnot a JPEG at all")

        mock_config = MagicMock()
        mock_config.user.avatar = str(disguised)

        monkeypatch.setattr("gptme.server.api_v2.load_user_config", lambda: mock_config)
        response = client.get("/api/v2/user/avatar")
        assert response.status_code == 400
        data = response.get_json()
        assert "image" in data["error"].lower()


class TestAgentCreationPathValidation:
    """Agent creation endpoint must reject paths outside the server working directory."""

    def _put_agent(self, client: FlaskClient, path: str | None = None):
        """Helper to send an agent creation request with optional path."""
        payload = {
            "name": "test-agent",
            "template_repo": "https://github.com/gptme/gptme-agent-template",
            "template_branch": "master",
            "fork_command": "echo ok",
        }
        if path is not None:
            payload["path"] = path
        return client.put(
            "/api/v2/agents",
            json=payload,
            content_type="application/json",
        )

    def test_rejects_absolute_path_outside_cwd(
        self, client: FlaskClient, tmp_path, monkeypatch
    ):
        """Absolute paths outside server working directory must be rejected."""
        from gptme.server import api_v2_agents

        monkeypatch.setattr(
            api_v2_agents, "INITIAL_WORKING_DIRECTORY", tmp_path.resolve()
        )
        response = self._put_agent(client, path="/tmp/evil-agent")
        assert response.status_code == 400
        data = response.get_json()
        assert data["error"] == "Path must be within the server working directory"

    def test_rejects_home_directory_path(
        self, client: FlaskClient, tmp_path, monkeypatch
    ):
        """Paths under home directory but outside cwd must be rejected."""
        from gptme.server import api_v2_agents

        monkeypatch.setattr(
            api_v2_agents, "INITIAL_WORKING_DIRECTORY", tmp_path.resolve()
        )
        response = self._put_agent(client, path="~/sneaky-agent")
        assert response.status_code == 400
        data = response.get_json()
        assert data["error"] == "Path must be within the server working directory"

    def test_rejects_traversal_in_path(
        self, client: FlaskClient, tmp_path, monkeypatch
    ):
        """Path traversal via ../ must be rejected."""
        from gptme.server import api_v2_agents

        monkeypatch.setattr(
            api_v2_agents, "INITIAL_WORKING_DIRECTORY", tmp_path.resolve()
        )
        response = self._put_agent(client, path="../../../tmp/escape")
        assert response.status_code == 400
        data = response.get_json()
        assert data["error"] == "Path must be within the server working directory"

    def test_rejects_etc_path(self, client: FlaskClient, tmp_path, monkeypatch):
        """System directories must be rejected."""
        from gptme.server import api_v2_agents

        monkeypatch.setattr(
            api_v2_agents, "INITIAL_WORKING_DIRECTORY", tmp_path.resolve()
        )
        response = self._put_agent(client, path="/etc/gptme-agent")
        assert response.status_code == 400
        data = response.get_json()
        assert data["error"] == "Path must be within the server working directory"

    def test_rejects_name_that_slugifies_to_empty(
        self, client: FlaskClient, tmp_path, monkeypatch
    ):
        """Names that produce an empty slug must be rejected to prevent workspace-at-cwd."""
        from gptme.server import api_v2_agents

        monkeypatch.setattr(
            api_v2_agents, "INITIAL_WORKING_DIRECTORY", tmp_path.resolve()
        )
        # "@#$%" slugifies to "" — INITIAL_WORKING_DIRECTORY / "" == INITIAL_WORKING_DIRECTORY
        payload = {
            "name": "@#$%",
            "template_repo": "https://github.com/gptme/gptme-agent-template",
            "template_branch": "master",
            "fork_command": "echo ok",
        }
        response = client.put(
            "/api/v2/agents",
            json=payload,
            content_type="application/json",
        )
        assert response.status_code == 400
        data = response.get_json()
        assert "alphanumeric" in data["error"].lower()


class TestValidateBranchUnit:
    """Unit tests for _validate_branch function (requires Flask app context)."""

    def test_rejects_traversal_payloads(self):
        """All traversal payloads must be rejected by _validate_branch."""
        from gptme.server.api_v2_common import _validate_branch
        from gptme.server.app import create_app

        app = create_app()
        with app.app_context():
            for payload in BRANCH_TRAVERSAL_PAYLOADS:
                assert _validate_branch(payload) is not None, (
                    f"Should reject: {payload}"
                )

    def test_accepts_valid_names(self):
        """Valid branch names should pass validation."""
        from gptme.server.api_v2_common import _validate_branch
        from gptme.server.app import create_app

        app = create_app()
        with app.app_context():
            for name in ["main", "feature-1", "my_branch", "v2.0"]:
                assert _validate_branch(name) is None, f"Should accept: {name}"

    def test_rejects_null_branch(self):
        """A null branch value must be rejected with 400, not raise TypeError (500)."""
        from gptme.server.api_v2_common import _validate_branch
        from gptme.server.app import create_app

        app = create_app()
        with app.app_context():
            result = _validate_branch(None)
            assert result is not None, "Should reject None branch"
            _response, status_code = result
            assert status_code == 400

    def test_rejects_non_string_branch(self):
        """Non-string branch values (int, list) must be rejected with 400."""
        from gptme.server.api_v2_common import _validate_branch
        from gptme.server.app import create_app

        app = create_app()
        with app.app_context():
            for value in [123, [], {}, True]:
                result = _validate_branch(value)
                assert result is not None, f"Should reject non-string: {value!r}"
                _response, status_code = result
                assert status_code == 400
