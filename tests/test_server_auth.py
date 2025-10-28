"""
Simple authentication tests for gptme-server.

Tests bearer token authentication on API endpoints.
"""

import os

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)


@pytest.fixture
def auth_token():
    """Set up auth token for tests."""
    import gptme.server.auth

    # Save original token
    original_token = gptme.server.auth._server_token

    token = "test-token-12345"
    os.environ["GPTME_SERVER_TOKEN"] = token
    gptme.server.auth._server_token = None  # Force regeneration

    yield token

    # Cleanup
    os.environ.pop("GPTME_SERVER_TOKEN", None)
    gptme.server.auth._server_token = original_token


def test_auth_success(auth_token):
    """Test successful authentication with valid token."""
    from flask import Flask

    from gptme.server.auth import require_auth

    app = Flask(__name__)

    @app.route("/test")
    @require_auth
    def protected_endpoint():
        return {"success": True}

    with app.test_client() as client:
        response = client.get(
            "/test", headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200
        assert response.json is not None
        assert response.json["success"] is True


def test_auth_missing_token():
    """Test authentication failure when token is missing."""
    from flask import Flask

    from gptme.server.auth import require_auth

    app = Flask(__name__)

    @app.route("/test")
    @require_auth
    def protected_endpoint():
        return {"success": True}

    with app.test_client() as client:
        response = client.get("/test")
        assert response.status_code == 401
        assert response.json is not None
        assert "error" in response.json


def test_auth_invalid_token(auth_token):
    """Test authentication failure with invalid token."""
    from flask import Flask

    from gptme.server.auth import require_auth

    app = Flask(__name__)

    @app.route("/test")
    @require_auth
    def protected_endpoint():
        return {"success": True}

    with app.test_client() as client:
        response = client.get("/test", headers={"Authorization": "Bearer wrong-token"})
        assert response.status_code == 401
        assert response.json is not None
        assert "error" in response.json
