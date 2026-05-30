"""Tests for the gptme-server CORS origin configuration.

Regression coverage for gptme/gptme#2226: the Tauri sidecar passes a
comma-separated list of origins so the desktop app works regardless of
which origin (tauri://localhost, http://tauri.localhost, https://tauri.localhost)
the platform's webview actually sends.
"""

import pytest

flask = pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)
pytest.importorskip(
    "flask_cors", reason="flask-cors not installed, install server extras (-E server)"
)


def _api_response_with_origin(app, origin: str):
    with app.test_client() as client:
        return client.get("/api/v2", headers={"Origin": origin})


def test_cors_single_origin_allows_match():
    from gptme.server.app import create_app

    app = create_app(cors_origin="tauri://localhost")
    resp = _api_response_with_origin(app, "tauri://localhost")
    assert resp.status_code == 200
    assert resp.headers.get("Access-Control-Allow-Origin") == "tauri://localhost"


def test_cors_single_origin_rejects_mismatch():
    from gptme.server.app import create_app

    app = create_app(cors_origin="tauri://localhost")
    resp = _api_response_with_origin(app, "http://tauri.localhost")
    # The request still succeeds at the Flask level, but Flask-CORS does not
    # echo the Allow-Origin header back when the origin does not match.
    assert resp.status_code == 200
    assert "Access-Control-Allow-Origin" not in resp.headers


def test_cors_comma_separated_allows_each_origin():
    """Regression for gptme/gptme#2226 — sidecar must accept all platform webview origins."""
    from gptme.server.app import create_app

    app = create_app(
        cors_origin="tauri://localhost,http://tauri.localhost,https://tauri.localhost"
    )

    for origin in (
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    ):
        resp = _api_response_with_origin(app, origin)
        assert resp.status_code == 200, origin
        assert resp.headers.get("Access-Control-Allow-Origin") == origin, origin


def test_cors_comma_separated_rejects_unlisted_origin():
    from gptme.server.app import create_app

    app = create_app(cors_origin="tauri://localhost,http://tauri.localhost")
    resp = _api_response_with_origin(app, "https://evil.example")
    assert resp.status_code == 200
    assert "Access-Control-Allow-Origin" not in resp.headers


def test_cors_comma_separated_tolerates_whitespace():
    from gptme.server.app import create_app

    app = create_app(cors_origin=" tauri://localhost , http://tauri.localhost ")
    resp = _api_response_with_origin(app, "http://tauri.localhost")
    assert resp.status_code == 200
    assert resp.headers.get("Access-Control-Allow-Origin") == "http://tauri.localhost"


def test_cors_degenerate_input_ignored():
    """Degenerate cors_origin values that produce no valid entries are ignored (no crash)."""
    from gptme.server.app import create_app

    for bad_input in (",", " , ", "  "):
        app = create_app(cors_origin=bad_input)
        resp = _api_response_with_origin(app, "tauri://localhost")
        assert resp.status_code == 200
        assert "Access-Control-Allow-Origin" not in resp.headers


def test_cors_wildcard_disables_credentials():
    """Browsers reject credentials with Access-Control-Allow-Origin: *,
    so create_app must not advertise credentials when '*' is in the list."""
    from gptme.server.app import create_app

    app = create_app(cors_origin="*")
    resp = _api_response_with_origin(app, "https://example.com")
    assert resp.status_code == 200
    # No credentials header should be set when wildcard is in use
    assert resp.headers.get("Access-Control-Allow-Credentials") != "true"


def test_cors_wildcard_disables_private_network():
    """Wildcard CORS must NOT opt in to Private Network Access.
    Enabling PNA for '*' would let any HTTPS page reach the local server,
    defeating Chrome's loopback protection."""
    from gptme.server.app import create_app

    app = create_app(cors_origin="*")
    with app.test_client() as client:
        resp = client.options(
            "/api/v2",
            headers={
                "Origin": "https://attacker.example.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
        )
    assert resp.headers.get("Access-Control-Allow-Private-Network") != "true"


def test_cors_preflight_allows_private_network():
    """A public https origin (e.g. https://chat.gptme.org) reaching the local
    server over loopback triggers Chrome's Private Network Access preflight.
    The server must answer Access-Control-Allow-Private-Network: true, or the
    browser blocks the request even though the origin is allowed."""
    from gptme.server.app import create_app

    app = create_app(cors_origin="https://chat.gptme.org")
    with app.test_client() as client:
        resp = client.options(
            "/api/v2",
            headers={
                "Origin": "https://chat.gptme.org",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
        )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("Access-Control-Allow-Origin") == "https://chat.gptme.org"
    assert resp.headers.get("Access-Control-Allow-Private-Network") == "true"
