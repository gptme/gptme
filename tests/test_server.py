import copy
import random

import pytest

flask = pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip


@pytest.fixture
def conv(client: FlaskClient):
    convname = f"test-server-{random.randint(0, 1000000)}"
    response = client.put(f"/api/v2/conversations/{convname}", json={})
    assert response.status_code == 200
    return convname


def test_root(client: FlaskClient):
    response = client.get("/")
    assert response.status_code == 200


def test_api_root(client: FlaskClient):
    response = client.get("/api/v2")
    assert response.status_code == 200
    data = response.get_json()
    assert "message" in data


def test_api_config_no_project(client: FlaskClient, monkeypatch):
    """GET /api/v2/config returns empty agent dict when no gptme.toml is present."""
    import gptme.server.api_v2 as api_v2_module
    from gptme.config import get_config as original_get_config

    def mock_get_config_no_project():
        cfg = copy.copy(original_get_config())
        cfg.project = None
        return cfg

    monkeypatch.setattr(api_v2_module, "get_config", mock_get_config_no_project)

    response = client.get("/api/v2/config")
    assert response.status_code == 200
    data = response.get_json()
    assert "agent" in data
    # Without a workspace gptme.toml, agent info is empty
    assert data["agent"] == {}


def test_api_config_with_agent_urls(tmp_path, client: FlaskClient, monkeypatch):
    """GET /api/v2/config includes agent.urls when gptme.toml has [agent.urls]."""

    from gptme.config import get_project_config

    toml_content = """
[agent]
name = "testbot"

[agent.urls]
dashboard = "https://testbot.example.com/"
repo = "https://github.com/example/testbot"
"""
    toml_file = tmp_path / "gptme.toml"
    toml_file.write_text(toml_content)

    # Monkeypatch the get_config reference inside api_v2.py
    import gptme.server.api_v2 as api_v2_module
    from gptme.config import get_config as original_get_config

    def mock_get_config():
        cfg = copy.copy(original_get_config())
        cfg.project = get_project_config(tmp_path)
        return cfg

    monkeypatch.setattr(api_v2_module, "get_config", mock_get_config)

    response = client.get("/api/v2/config")
    assert response.status_code == 200
    data = response.get_json()
    assert data["agent"]["name"] == "testbot"
    assert data["agent"]["urls"]["dashboard"] == "https://testbot.example.com/"
    assert data["agent"]["urls"]["repo"] == "https://github.com/example/testbot"


def test_api_conversation_list(client: FlaskClient):
    response = client.get("/api/v2/conversations")
    assert response.status_code == 200


def test_api_conversation_get(conv, client: FlaskClient):
    response = client.get(f"/api/v2/conversations/{conv}")
    assert response.status_code == 200


def test_api_conversation_post(conv, client: FlaskClient):
    response = client.post(
        f"/api/v2/conversations/{conv}",
        json={"role": "user", "content": "hello"},
    )
    assert response.status_code == 200


def test_debug_errors_disabled(monkeypatch):
    """Test that debug errors are disabled by default."""
    from gptme.server.api_v2_common import _is_debug_errors_enabled

    # Clear the env var to test default behavior
    monkeypatch.delenv("GPTME_DEBUG_ERRORS", raising=False)
    assert _is_debug_errors_enabled() is False


def test_debug_errors_enabled(monkeypatch):
    """Test that debug errors can be enabled via environment variable."""
    from gptme.server.api_v2_common import _is_debug_errors_enabled

    # Test various truthy values
    for value in ["1", "true", "TRUE", "yes", "YES"]:
        monkeypatch.setenv("GPTME_DEBUG_ERRORS", value)
        assert _is_debug_errors_enabled() is True, f"Failed for value: {value}"

    # Test falsy values
    for value in ["0", "false", "no", ""]:
        monkeypatch.setenv("GPTME_DEBUG_ERRORS", value)
        assert _is_debug_errors_enabled() is False, (
            f"Should be False for value: {value}"
        )


def test_default_model_propagation():
    """Test that the server's default model is propagated to request contexts.

    This tests the before_request hook that propagates the default model
    from the startup context to each request context (ContextVar fix).
    """
    # Set a default model before creating the app (simulates server startup with --model)
    # Use a mock model object that matches what get_default_model returns
    from gptme.llm.models import ModelMeta, set_default_model
    from gptme.server.app import create_app

    test_model = ModelMeta(
        provider="openai",
        model="gpt-4",
        context=8192,
        max_output=4096,
    )
    set_default_model(test_model)

    try:
        # Create the app - this should capture the default model
        app = create_app()

        # Verify the model was stored in app config
        assert "SERVER_DEFAULT_MODEL" in app.config
        assert app.config["SERVER_DEFAULT_MODEL"] == test_model

        # Make a request - the before_request hook should propagate the model
        with app.test_client() as client:
            # The models endpoint returns the default model
            response = client.get("/api/v2/models")
            assert response.status_code == 200
            data = response.get_json()
            # Verify the default model is returned (not None)
            assert data.get("default") is not None
            assert "gpt-4" in data.get("default", "")
    finally:
        # Clean up - reset the default model by using the ContextVar directly
        from gptme.llm.models import _default_model_var

        _default_model_var.set(None)


def test_api_v2_commands(client: FlaskClient):
    """Test the /api/v2/commands endpoint returns available commands."""
    response = client.get("/api/v2/commands")
    assert response.status_code == 200
    data = response.get_json()
    assert "commands" in data
    commands = data["commands"]
    assert isinstance(commands, list)
    # Core commands should always be registered
    assert "/help" in commands
    assert "/exit" in commands
    assert "/model" in commands


def test_api_v2_conversation_command(conv, client: FlaskClient):
    """Test that slash commands are detected and executed when posted."""
    # /help prints to stdout (doesn't yield Messages), so responses=0
    # but the command flag should be set
    response = client.post(
        f"/api/v2/conversations/{conv}",
        json={"role": "user", "content": "/help"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data.get("command") is True


def test_api_v2_conversation_command_undo(conv, client: FlaskClient):
    """Test /undo command removes the last message."""
    # First, add a message
    client.post(
        f"/api/v2/conversations/{conv}",
        json={"role": "user", "content": "test message"},
    )
    # Then undo it via slash command
    response = client.post(
        f"/api/v2/conversations/{conv}",
        json={"role": "user", "content": "/undo"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data.get("command") is True


def test_api_v2_conversation_not_command(conv, client: FlaskClient):
    """Test that regular messages are not treated as commands."""
    response = client.post(
        f"/api/v2/conversations/{conv}",
        json={"role": "user", "content": "hello world"},
    )
    assert response.status_code == 200
    data = response.get_json()
    # Regular messages should not have the "command" flag
    assert "command" not in data


def test_api_v2_conversation_path_not_command(conv, client: FlaskClient):
    """Test that file paths starting with / are not treated as commands."""
    response = client.post(
        f"/api/v2/conversations/{conv}",
        json={"role": "user", "content": "/path/to/file.md"},
    )
    assert response.status_code == 200
    data = response.get_json()
    # File paths should not be treated as commands
    assert "command" not in data
