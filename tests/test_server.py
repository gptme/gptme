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


# --- Cookie-based authentication tests ---


@pytest.fixture
def auth_app():
    """Create an app with auth enabled (network binding)."""
    from gptme.server.app import create_app
    from gptme.server.auth import (
        AUTH_COOKIE_NAME,
        init_auth,
        set_server_token,
    )

    app = create_app(host="0.0.0.0")
    # Force auth enabled and set a known token
    test_token = "test-token-for-cookie-auth"
    set_server_token(test_token)
    init_auth(host="0.0.0.0", display=False)

    yield app, test_token, AUTH_COOKIE_NAME

    # Reset auth state for other tests
    init_auth(host="127.0.0.1", display=False)


@pytest.fixture
def auth_client(auth_app):
    """Test client for auth-enabled app."""
    app, token, cookie_name = auth_app
    with app.test_client() as test_client:
        yield test_client, token, cookie_name


def test_auth_cookie_set(auth_client):
    """POST /api/v2/auth/cookie sets HttpOnly auth cookie."""
    client, token, cookie_name = auth_client

    response = client.post(
        "/api/v2/auth/cookie",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True

    # Verify Set-Cookie header properties
    set_cookie = response.headers.get("Set-Cookie", "")
    assert cookie_name in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Path=/api/" in set_cookie


def test_auth_cookie_rejected_without_token(auth_client):
    """POST /api/v2/auth/cookie without Bearer token returns 401."""
    client, token, cookie_name = auth_client

    response = client.post("/api/v2/auth/cookie")
    assert response.status_code == 401


def test_auth_cookie_rejected_with_bad_token(auth_client):
    """POST /api/v2/auth/cookie with wrong token returns 401."""
    client, token, cookie_name = auth_client

    response = client.post(
        "/api/v2/auth/cookie",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401


def test_auth_cookie_used_for_api(auth_client):
    """API requests authenticate via cookie when no header is present."""
    client, token, cookie_name = auth_client

    # Verify protected endpoint rejects without any auth
    response = client.get("/api/v2/conversations")
    assert response.status_code == 401

    # Set the cookie
    client.post(
        "/api/v2/auth/cookie",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Now make an API request without Authorization header — cookie should work
    response = client.get("/api/v2/conversations")
    assert response.status_code == 200


def test_auth_cookie_clear(auth_client):
    """DELETE /api/v2/auth/cookie returns success and sets expired cookie."""
    client, token, cookie_name = auth_client

    # Set cookie first
    client.post(
        "/api/v2/auth/cookie",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Clear it
    response = client.delete("/api/v2/auth/cookie")
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True

    # Verify the Set-Cookie header expires the cookie
    set_cookie = response.headers.get("Set-Cookie", "")
    assert cookie_name in set_cookie
    # Expired cookies have Expires in the past or Max-Age=0
    assert "Expires=" in set_cookie or "Max-Age=0" in set_cookie

    # Verify cookie-only requests are rejected after clearing
    # Use a fresh client (no cookie jar) to simulate browser honoring Max-Age=0
    app = client.application
    with app.test_client() as fresh_client:
        response = fresh_client.get("/api/v2/conversations")
        assert response.status_code == 401


def test_auth_header_takes_priority_over_cookie(auth_client):
    """Authorization header is preferred over cookie."""
    client, token, cookie_name = auth_client

    # Set a valid cookie
    client.post(
        "/api/v2/auth/cookie",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Request with valid header should work even if we clear cookie
    response = client.get(
        "/api/v2/conversations",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


def test_auth_query_param_still_works(auth_client):
    """Query parameter auth still works as fallback (backward compat)."""
    client, token, cookie_name = auth_client

    response = client.get(f"/api/v2/conversations?token={token}")
    assert response.status_code == 200
