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

    # Save original state
    original_token = gptme.server.auth._server_token
    original_auth_enabled = gptme.server.auth._auth_enabled
    original_disable_auth = os.environ.pop("GPTME_DISABLE_AUTH", None)

    token = "test-token-12345"
    os.environ["GPTME_SERVER_TOKEN"] = token
    gptme.server.auth._server_token = None  # Force regeneration

    # Enable auth for tests.
    gptme.server.auth.init_auth("0.0.0.0")

    yield token

    # Cleanup
    os.environ.pop("GPTME_SERVER_TOKEN", None)
    if original_disable_auth is not None:
        os.environ["GPTME_DISABLE_AUTH"] = original_disable_auth
    gptme.server.auth._server_token = original_token
    gptme.server.auth._auth_enabled = original_auth_enabled


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


def test_auth_missing_token(auth_token):
    """Test authentication failure when request is missing token.

    Server has a token configured, but the request doesn't include it.
    """
    from flask import Flask

    from gptme.server.auth import require_auth

    app = Flask(__name__)

    @app.route("/test")
    @require_auth
    def protected_endpoint():
        return {"success": True}

    with app.test_client() as client:
        response = client.get("/test")  # No Authorization header
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


def test_auth_disabled_via_env():
    """Test that authentication can be disabled via GPTME_DISABLE_AUTH."""
    from flask import Flask

    import gptme.server.auth

    # Save original state
    original_auth_enabled = gptme.server.auth._auth_enabled
    original_env = os.environ.get("GPTME_DISABLE_AUTH")

    try:
        # Set environment variable to disable auth
        os.environ["GPTME_DISABLE_AUTH"] = "true"

        # Re-initialize auth with an explicit disable override.
        gptme.server.auth._auth_enabled = True  # Reset state
        gptme.server.auth.init_auth("0.0.0.0", display=False)

        app = Flask(__name__)

        @app.route("/test")
        @gptme.server.auth.require_auth
        def protected_endpoint():
            return {"success": True}

        with app.test_client() as client:
            # Should succeed without token since auth is disabled
            response = client.get("/test")
            assert response.status_code == 200
            assert response.json is not None
            assert response.json["success"] is True

    finally:
        # Cleanup
        if original_env is not None:
            os.environ["GPTME_DISABLE_AUTH"] = original_env
        else:
            os.environ.pop("GPTME_DISABLE_AUTH", None)
        gptme.server.auth._auth_enabled = original_auth_enabled


def test_health_endpoint_unauthenticated(auth_token):
    """Health endpoint is reachable without credentials even when auth is on.

    Liveness/readiness probes and monitoring tools cannot carry bearer tokens
    (gptme#3701). Auth-enabled server must still return 200 on
    /api/v2/server/health, while other API endpoints stay 401.
    """
    from gptme.server.app import create_app

    app = create_app()

    with app.test_client() as client:
        health = client.get("/api/v2/server/health")
        assert health.status_code == 200
        assert health.json is not None
        assert "health" in health.json

        # Sanity: a non-health API endpoint still requires auth.
        protected = client.get("/api/v2/conversations")
        assert protected.status_code == 401


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"headers": {"Authorization": "Bearer é"}},
        {"query_string": {"token": "é"}},
    ],
    ids=["bearer", "query"],
)
def test_non_ascii_token_is_rejected_with_401(auth_token, request_kwargs):
    """A non-ASCII token must be a plain 401, not a TypeError from compare_digest (500)."""
    from gptme.server.app import create_app

    app = create_app()
    with app.test_client() as client:
        response = client.get("/api/v2/conversations", **request_kwargs)
        assert response.status_code == 401


def test_non_ascii_cookie_token_is_rejected_with_401(auth_token):
    from gptme.server.app import create_app

    app = create_app()
    with app.test_client() as client:
        client.set_cookie("gptme_auth", "é", path="/api/")
        response = client.get("/api/v2/conversations")
        assert response.status_code == 401


def test_non_ascii_server_token_accepts_matching_token(monkeypatch):
    """A non-ASCII server token still authenticates a matching request token."""
    import gptme.server.auth
    from gptme.server.app import create_app

    monkeypatch.delenv("GPTME_DISABLE_AUTH", raising=False)
    monkeypatch.setenv("GPTME_SERVER_TOKEN", "tökén-é")
    monkeypatch.setattr(gptme.server.auth, "_server_token", None)
    # Re-set to itself so monkeypatch restores it after init_auth() changes it.
    monkeypatch.setattr(
        gptme.server.auth, "_auth_enabled", gptme.server.auth._auth_enabled
    )
    gptme.server.auth.init_auth("0.0.0.0")

    app = create_app()
    with app.test_client() as client:
        ok = client.get("/api/v2/conversations", query_string={"token": "tökén-é"})
        assert ok.status_code == 200
        wrong = client.get("/api/v2/conversations", query_string={"token": "tökén-x"})
        assert wrong.status_code == 401


def test_non_ascii_server_token_accepts_matching_bearer_header(monkeypatch):
    """A non-ASCII server token authenticates via the Authorization header.

    Under PEP 3333 a WSGI server puts header values in the environ as the raw
    bytes decoded as latin-1, so a client sending ``Bearer tökén-é`` as UTF-8
    reaches the app as mojibake. The werkzeug test client stores header strings
    verbatim, so build the environ value the way a real server would.
    """
    import gptme.server.auth
    from gptme.server.app import create_app

    monkeypatch.delenv("GPTME_DISABLE_AUTH", raising=False)
    monkeypatch.setenv("GPTME_SERVER_TOKEN", "tökén-é")
    monkeypatch.setattr(gptme.server.auth, "_server_token", None)
    monkeypatch.setattr(
        gptme.server.auth, "_auth_enabled", gptme.server.auth._auth_enabled
    )
    gptme.server.auth.init_auth("0.0.0.0")

    def wsgi_header(value: str) -> str:
        return value.encode("utf-8").decode("latin-1")

    app = create_app()
    with app.test_client() as client:
        ok = client.get(
            "/api/v2/conversations",
            headers={"Authorization": wsgi_header("Bearer tökén-é")},
        )
        assert ok.status_code == 200
        wrong = client.get(
            "/api/v2/conversations",
            headers={"Authorization": wsgi_header("Bearer tökén-x")},
        )
        assert wrong.status_code == 401
