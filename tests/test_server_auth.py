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


@pytest.mark.parametrize("source", ["bearer", "query", "cookie"])
def test_non_ascii_token_is_rejected_with_401(auth_token: str, source: str) -> None:
    from gptme.server.app import create_app
    from gptme.server.auth import AUTH_COOKIE_NAME

    app = create_app()
    with app.test_client() as client:
        headers = {"Authorization": "Bearer é"} if source == "bearer" else {}
        query = {"token": "é"} if source == "query" else {}
        if source == "cookie":
            client.set_cookie(AUTH_COOKIE_NAME, "é", path="/api/")

        response = client.get(
            "/api/v2/conversations", headers=headers, query_string=query
        )
        assert response.status_code == 401
        assert response.json is not None
        assert response.json["error"] == "Invalid or expired token"


def _configure_server_token(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    from gptme.server import auth

    monkeypatch.delenv("GPTME_DISABLE_AUTH", raising=False)
    monkeypatch.setenv("GPTME_SERVER_TOKEN", token)
    monkeypatch.setattr(auth, "_server_token", None)
    monkeypatch.setattr(auth, "_auth_enabled", auth._auth_enabled)


@pytest.mark.parametrize(
    ("token", "encoding"),
    [
        pytest.param("tökén-é", "utf-8", id="utf8-wire-latin1-token"),
        pytest.param("tökén-é", "latin-1", id="latin1-wire"),
        pytest.param("tok€n", "utf-8", id="utf8-wire-above-latin1"),
        pytest.param("tok€n", None, id="direct-unicode"),
    ],
)
def test_non_ascii_server_token_accepts_matching_bearer_header(
    monkeypatch: pytest.MonkeyPatch, token: str, encoding: str | None
) -> None:
    from gptme.server.app import create_app

    _configure_server_token(monkeypatch, token)
    app = create_app()
    with app.test_client() as client:
        for candidate, status in [(token, 200), (token + "x", 401)]:
            value = f"Bearer {candidate}"
            if encoding is not None:
                # WSGI exposes wire header bytes as Latin-1 text. The test
                # client passes strings directly to the WSGI environ.
                value = value.encode(encoding).decode("latin-1")
            response = client.get(
                "/api/v2/conversations", headers={"Authorization": value}
            )
            assert response.status_code == status


def test_non_ascii_server_token_accepts_query_and_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gptme.server.app import create_app

    token = "tökén-é"
    _configure_server_token(monkeypatch, token)
    app = create_app()
    with app.test_client() as client:
        wrong_token = {"token": "tökén-x"}
        assert (
            client.get("/api/v2/conversations", query_string=wrong_token).status_code
            == 401
        )
        assert (
            client.get(
                "/api/v2/conversations", query_string={"token": token}
            ).status_code
            == 200
        )
        response = client.post(
            "/api/v2/auth/cookie", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert (
            client.get("/api/v2/conversations", query_string=wrong_token).status_code
            == 200
        )


def test_empty_bearer_does_not_fall_back(auth_token: str) -> None:
    from gptme.server.app import create_app
    from gptme.server.auth import AUTH_COOKIE_NAME

    app = create_app()
    with app.test_client() as client:
        client.set_cookie(AUTH_COOKIE_NAME, auth_token, path="/api/")
        response = client.get(
            "/api/v2/conversations",
            headers={"Authorization": "Bearer "},
            query_string={"token": auth_token},
        )
        assert response.status_code == 401
        assert response.json is not None
        assert response.json["error"] == "Missing bearer credentials"
