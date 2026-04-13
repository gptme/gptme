"""Tests for the V2 agents API endpoint.

Tests agent creation validation, slugification, workspace setup (mocked),
and error handling paths.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Skip if flask not installed
pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

from gptme.server.api_v2_agents import slugify_name  # fmt: skip

pytestmark = [pytest.mark.timeout(10)]


# --- Unit tests for slugify_name ---


class TestSlugifyName:
    """Test the slugify_name utility function."""

    def test_simple_name(self):
        assert slugify_name("Bob") == "bob"

    def test_name_with_spaces(self):
        assert slugify_name("My Agent") == "my-agent"

    def test_name_with_special_chars(self):
        assert slugify_name("Agent@v2!") == "agentv2"

    def test_name_with_multiple_spaces(self):
        assert slugify_name("My  Cool  Agent") == "my-cool-agent"

    def test_name_with_hyphens(self):
        assert slugify_name("my-agent-name") == "my-agent-name"

    def test_name_with_underscores(self):
        assert slugify_name("my_agent_name") == "my_agent_name"

    def test_name_with_leading_trailing_hyphens(self):
        assert slugify_name("-agent-") == "agent"

    def test_name_with_mixed_special_chars(self):
        assert slugify_name("Agent #1 (test)") == "agent-1-test"

    def test_empty_string(self):
        assert slugify_name("") == ""

    def test_all_special_chars(self):
        assert slugify_name("@#$%") == ""

    def test_unicode_letters(self):
        # Unicode word chars should be preserved by \w regex
        result = slugify_name("agënt")
        assert "ag" in result


# --- Integration tests for agents PUT endpoint ---


class TestAgentsPutEndpoint:
    """Test the PUT /api/v2/agents endpoint."""

    def _make_agent_request(
        self,
        client: FlaskClient,
        name: str = "test-agent",
        template_repo: str = "https://github.com/gptme/gptme-agent-template",
        template_branch: str = "master",
        fork_command: str = "echo fork",
        path: str | None = None,
    ) -> dict:
        """Helper to construct agent creation request body."""
        body: dict = {
            "name": name,
            "template_repo": template_repo,
            "template_branch": template_branch,
            "fork_command": fork_command,
        }
        if path is not None:
            body["path"] = path
        return body

    def test_missing_json_body(self, client: FlaskClient):
        """PUT without JSON body returns 400."""
        response = client.put(
            "/api/v2/agents",
            content_type="application/json",
            data="",
        )
        assert response.status_code == 400

    def test_missing_name(self, client: FlaskClient):
        """PUT without name field returns 400."""
        response = client.put(
            "/api/v2/agents",
            json={
                "template_repo": "https://example.com/repo",
                "template_branch": "main",
                "fork_command": "echo fork",
            },
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "name" in data["error"].lower()

    def test_missing_template_repo(self, client: FlaskClient):
        """PUT without template_repo returns 400."""
        response = client.put(
            "/api/v2/agents",
            json={
                "name": "test-agent",
                "template_branch": "main",
                "fork_command": "echo fork",
            },
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "template_repo" in data["error"]

    def test_missing_template_branch(self, client: FlaskClient):
        """PUT without template_branch returns 400."""
        response = client.put(
            "/api/v2/agents",
            json={
                "name": "test-agent",
                "template_repo": "https://example.com/repo",
                "fork_command": "echo fork",
            },
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "template_branch" in data["error"]

    def test_missing_fork_command(self, client: FlaskClient):
        """PUT without fork_command returns 400."""
        response = client.put(
            "/api/v2/agents",
            json={
                "name": "test-agent",
                "template_repo": "https://example.com/repo",
                "template_branch": "main",
            },
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "fork_command" in data["error"]

    @patch("gptme.server.api_v2_agents.create_workspace_from_template")
    @patch("gptme.server.api_v2_agents.init_conversation")
    def test_successful_creation(
        self,
        mock_init_conv: MagicMock,
        mock_create_workspace: MagicMock,
        client: FlaskClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Successful agent creation with all required fields."""
        import gptme.server.api_v2_agents as agents_module

        monkeypatch.setattr(agents_module, "INITIAL_WORKING_DIRECTORY", tmp_path)
        mock_init_conv.return_value = "test-conversation-id"
        agent_path = str(tmp_path / "my-agent")

        response = client.put(
            "/api/v2/agents",
            json=self._make_agent_request(client, name="My Agent", path=agent_path),
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data is not None
        assert data["status"] == "ok"
        assert data["message"] == "Agent created"
        assert data["initial_conversation_id"] == "test-conversation-id"
        assert data["agent_path"] == str(Path(agent_path).resolve())

        mock_create_workspace.assert_called_once()
        mock_init_conv.assert_called_once()

    @patch("gptme.server.api_v2_agents.create_workspace_from_template")
    @patch("gptme.server.api_v2_agents.init_conversation")
    def test_auto_generated_path(
        self,
        mock_init_conv: MagicMock,
        mock_create_workspace: MagicMock,
        client: FlaskClient,
    ):
        """When no path provided, auto-generates from INITIAL_WORKING_DIRECTORY + slugified name."""
        mock_init_conv.return_value = "conv-123"

        response = client.put(
            "/api/v2/agents",
            json=self._make_agent_request(client, name="Cool Agent"),
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data is not None
        # Path should end with the slugified name
        assert data["agent_path"].endswith("cool-agent")

    @patch("gptme.server.api_v2_agents.create_workspace_from_template")
    def test_workspace_already_exists(
        self,
        mock_create_workspace: MagicMock,
        client: FlaskClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """WorkspaceError with 'already exists' returns 400."""
        import gptme.server.api_v2_agents as agents_module

        monkeypatch.setattr(agents_module, "INITIAL_WORKING_DIRECTORY", tmp_path)
        from gptme.agent.workspace import WorkspaceError

        mock_create_workspace.side_effect = WorkspaceError(
            "Workspace already exists at /some/path"
        )

        response = client.put(
            "/api/v2/agents",
            json=self._make_agent_request(client, path=str(tmp_path / "existing")),
        )

        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "already exists" in data["error"].lower()

    @patch("gptme.server.api_v2_agents.create_workspace_from_template")
    def test_workspace_creation_timeout(
        self,
        mock_create_workspace: MagicMock,
        client: FlaskClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """WorkspaceError with 'timed out' returns 504."""
        import gptme.server.api_v2_agents as agents_module

        monkeypatch.setattr(agents_module, "INITIAL_WORKING_DIRECTORY", tmp_path)
        from gptme.agent.workspace import WorkspaceError

        mock_create_workspace.side_effect = WorkspaceError(
            "Git clone timed out after 300 seconds"
        )

        response = client.put(
            "/api/v2/agents",
            json=self._make_agent_request(client, path=str(tmp_path / "timeout")),
        )

        assert response.status_code == 504
        data = response.get_json()
        assert data is not None
        assert "timed out" in data["error"].lower()

    @patch("gptme.server.api_v2_agents.create_workspace_from_template")
    def test_workspace_creation_generic_error(
        self,
        mock_create_workspace: MagicMock,
        client: FlaskClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """WorkspaceError with generic message returns 500."""
        import gptme.server.api_v2_agents as agents_module

        monkeypatch.setattr(agents_module, "INITIAL_WORKING_DIRECTORY", tmp_path)
        from gptme.agent.workspace import WorkspaceError

        mock_create_workspace.side_effect = WorkspaceError("Something went wrong")

        response = client.put(
            "/api/v2/agents",
            json=self._make_agent_request(client, path=str(tmp_path / "broken")),
        )

        assert response.status_code == 500
        data = response.get_json()
        assert data is not None
        assert "something went wrong" in data["error"].lower()

    @patch("gptme.server.api_v2_agents.create_workspace_from_template")
    @patch("gptme.server.api_v2_agents.init_conversation")
    def test_conversation_init_failure(
        self,
        mock_init_conv: MagicMock,
        mock_create_workspace: MagicMock,
        client: FlaskClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Failure in init_conversation returns 500."""
        import gptme.server.api_v2_agents as agents_module

        monkeypatch.setattr(agents_module, "INITIAL_WORKING_DIRECTORY", tmp_path)
        mock_init_conv.side_effect = RuntimeError("Conversation init failed")

        response = client.put(
            "/api/v2/agents",
            json=self._make_agent_request(client, path=str(tmp_path / "init-fail")),
        )

        assert response.status_code == 500
        data = response.get_json()
        assert data is not None
        assert "failed to initialize" in data["error"].lower()

    @patch("gptme.server.api_v2_agents.create_workspace_from_template")
    @patch("gptme.server.api_v2_agents.init_conversation")
    def test_path_expansion(
        self,
        mock_init_conv: MagicMock,
        mock_create_workspace: MagicMock,
        client: FlaskClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Path with ~ gets expanded and resolved."""
        import gptme.server.api_v2_agents as agents_module

        monkeypatch.setattr(agents_module, "INITIAL_WORKING_DIRECTORY", Path.home())
        mock_init_conv.return_value = "conv-456"

        response = client.put(
            "/api/v2/agents",
            json=self._make_agent_request(client, path="~/my-agents/test"),
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data is not None
        # Path should be resolved (no ~)
        assert "~" not in data["agent_path"]
        assert data["agent_path"].startswith("/")
