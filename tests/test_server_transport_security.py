"""Transport security: origin checks, cookie transport, unsafe defaults.

Regression tests for gptme/gptme#5241. Run with::

    uv run pytest tests/test_server_transport_security.py -x -q

Scope: ``gptme/server/`` transport concerns (CORS/origin, cookie transport,
third-party content on cookie-scoped origins, framing protection).  The
documented threat model in ``docs/server.rst`` is the baseline: bearer auth is
required for capability routes, and browser-originating attacks (CSRF / DNS
rebinding) are explicitly claimed to be blocked.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)


@pytest.fixture(autouse=True)
def init_(monkeypatch, isolate_user_config):
    """Override conftest's init locally, without mutating collection-time env."""
    from gptme.init import init
    from gptme.lessons.index import LessonIndex

    monkeypatch.setenv("MODEL", "local/test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:666")
    with monkeypatch.context() as m:
        m.setattr(LessonIndex, "_default_dirs", staticmethod(lambda: []))
        init(
            "local/test", interactive=False, tool_allowlist=None, tool_format="markdown"
        )


TOKEN = "security-hunt-token"

# A same-site but cross-port origin: cookies ignore ports, so a page served by
# any other loopback listener (e.g. an agent-started dev server the user opened
# at http://127.0.0.1:8888) is *same-site* with gptme-server and is sent the
# SameSite=Lax ``gptme_auth`` cookie.
FOREIGN_SAME_SITE_ORIGIN = "http://127.0.0.1:8888"


def _make_app(monkeypatch, **kwargs):
    """Real app with a deterministic token, auth enabled, host checks off."""
    import gptme.server.auth as auth_mod

    monkeypatch.setenv("GPTME_SERVER_TOKEN", TOKEN)
    monkeypatch.delenv("GPTME_DISABLE_AUTH", raising=False)
    monkeypatch.setattr(auth_mod, "_server_token", None)
    monkeypatch.setattr(auth_mod, "_auth_enabled", True)

    from gptme.server.app import create_app

    return create_app(host="127.0.0.1", **kwargs)


@pytest.fixture
def app(monkeypatch):
    return _make_app(monkeypatch)


def _client_with_auth_cookie(app, path: str = "/api/"):
    """Test client holding only the HttpOnly auth cookie (no bearer header).

    This is the browser state after ``POST /api/v2/auth/cookie``: the webui
    keeps no Authorization header for cookie-mode requests, so a forged
    cross-origin request only carries the cookie.
    """
    client = app.test_client()
    client.set_cookie("gptme_auth", TOKEN, domain="localhost", path=path)
    return client


# ---------------------------------------------------------------------------
# Finding 1: no Origin/CSRF validation on cookie-authenticated multipart routes
# ---------------------------------------------------------------------------


def test_upload_rejects_foreign_origin_cookie_request(app, tmp_path, monkeypatch):
    """A cross-origin page must not be able to write files via the auth cookie.

    ``POST .../workspace/upload`` accepts ``multipart/form-data``, a
    CORS-safelisted content type, so no preflight is required.  ``gptme_auth``
    is ``SameSite=Lax``, which does *not* constrain same-site origins, and
    ``gptme/server`` never inspects ``Origin``/``Referer`` for state-changing
    requests.  A page on any other loopback port can therefore drop arbitrary
    files into ``<logdir>/attachments/`` (here: an HTML payload that the
    conversation-file route then serves inline as ``text/html``).
    """
    import gptme.server.workspace_api as workspace_api

    logdir = tmp_path / "conv-abc"
    (logdir / "attachments").mkdir(parents=True)
    manager = MagicMock()
    manager.logdir = logdir
    monkeypatch.setattr(workspace_api.LogManager, "load", lambda *a, **k: manager)

    client = _client_with_auth_cookie(app)
    resp = client.post(
        "/api/v2/conversations/conv-abc/workspace/upload",
        data={"file": (io.BytesIO(b"<script>alert(1)</script>"), "report.html")},
        content_type="multipart/form-data",
        headers={"Origin": FOREIGN_SAME_SITE_ORIGIN},
    )

    # Desired: state-changing cookie-auth requests from a non-allowed Origin are
    # refused (403).  Actual: 200 and the file lands on disk.
    assert resp.status_code == 403, (
        f"upload accepted a cross-origin cookie request: {resp.status_code} "
        f"{resp.get_json()!r}; wrote={(logdir / 'attachments' / 'report.html').exists()}"
    )


def test_audio_transcription_rejects_foreign_origin_cookie_request(app, monkeypatch):
    """Same gap on ``POST /api/v2/audio/transcriptions`` (multipart, spends credits).

    An unwitting user's OpenRouter key is used to transcribe audio the attacker
    supplies; the attacker cannot read the response, but the call still bills
    the account.
    """
    import gptme.server.api_v2 as api_v2_mod

    calls: list[str] = []

    def fake_transcribe(**kwargs):
        calls.append(kwargs["model"])
        return {"text": "attacker audio"}

    monkeypatch.setattr(
        api_v2_mod, "_transcribe_audio_with_openrouter", fake_transcribe
    )

    client = _client_with_auth_cookie(app)
    resp = client.post(
        "/api/v2/audio/transcriptions",
        data={"file": (io.BytesIO(b"RIFF0000WAVE"), "note.wav")},
        content_type="multipart/form-data",
        headers={"Origin": FOREIGN_SAME_SITE_ORIGIN},
    )

    assert resp.status_code == 403, (
        "audio/transcriptions accepted a cross-origin cookie request: "
        f"{resp.status_code} {resp.get_json()!r}; stt_calls={calls}"
    )


# ---------------------------------------------------------------------------
# Finding 2: auth cookie never marked Secure behind a TLS-terminating proxy
# ---------------------------------------------------------------------------


def test_auth_cookie_is_secure_behind_tls_terminating_proxy(app):
    """The documented nginx deployment terminates TLS and proxies plain HTTP.

    gptme-server never applies ``ProxyFix`` and never consults
    ``X-Forwarded-Proto``, so ``request.is_secure`` is False in every documented
    deployment (nginx, docker-compose, cloud ingress) and the 24h
    bearer-equivalent cookie is issued without ``Secure``.  Cookies are
    port-agnostic, so the token would be attached to any plaintext HTTP request
    for the same host (e.g. the compose file's published ``5700`` port).
    """
    client = app.test_client()
    resp = client.post(
        "/api/v2/auth/cookie",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            # Exactly what docs/server.rst tells nginx to send.
            "X-Forwarded-Proto": "https",
        },
    )
    assert resp.status_code == 200

    set_cookies = resp.headers.getlist("Set-Cookie")
    assert set_cookies, "expected Set-Cookie headers"
    # Desired: `Secure` present for a TLS-terminating deployment.
    # (Direct TLS sets it because werkzeug reports wsgi.url_scheme=https; the
    # documented proxy path does not.)
    assert all("secure" in c.lower() for c in set_cookies), (
        "auth cookie issued without Secure over a TLS-terminating proxy: "
        f"{set_cookies!r}"
    )


# ---------------------------------------------------------------------------
# Finding 3: the authenticated SPA can be framed by any origin (clickjacking)
# ---------------------------------------------------------------------------


def test_bundled_ui_cannot_be_framed(app, tmp_path):
    """No ``X-Frame-Options`` and no CSP ``frame-ancestors`` on server pages.

    The webui keeps the bearer token in localStorage and auto-connects, so a
    framed instance is fully authenticated.  Any web page can iframe
    ``http://127.0.0.1:5700/`` and clickjack the tool-confirmation dialog /
    composer, which is equivalent to running commands as the user.
    """
    ui_dir = tmp_path / "webui"
    ui_dir.mkdir()
    (ui_dir / "index.html").write_text("<!doctype html><html><body>ui</body></html>")
    (ui_dir / "computer.html").write_text(
        "<!doctype html><html><body>vnc</body></html>"
    )

    from gptme.server.app import create_app

    ui_app = create_app(webui_dir=ui_dir, host="127.0.0.1")

    failures = []
    with ui_app.test_client() as client:
        for path in ("/", "/chat", "/computer"):
            resp = client.get(path)
            csp = resp.headers.get("Content-Security-Policy", "")
            if resp.headers.get("X-Frame-Options") or "frame-ancestors" in csp:
                continue
            failures.append(
                f"{path}: status={resp.status_code} XFO="
                f"{resp.headers.get('X-Frame-Options')!r} CSP={csp!r}"
            )

    assert not failures, "server pages are framable by any origin: " + "; ".join(
        failures
    )


# ---------------------------------------------------------------------------
# Finding 4: public API docs page loads third-party scripts on the cookie origin
# ---------------------------------------------------------------------------

COOKIE_ORIGIN_PATHS = ("/api/", "/preview/")


def test_api_docs_does_not_load_third_party_scripts_on_cookie_origin(app):
    """``/api/docs/`` is public and inside the ``gptme_auth`` cookie path.

    The page pulls three ``unpkg.com`` bundles without SRI and without any CSP,
    so a compromised CDN (or a MITM on first fetch) executes script on the
    origin that owns the ``Path=/api/`` auth cookie — the exact hole the
    preview proxy was hardened to avoid ("Preview HTML is never served under
    /api/").  Desired: self-hosted assets, or SRI + a restrictive ``script-src``.
    """
    from gptme.server.auth import AUTH_COOKIE_PATHS

    assert set(AUTH_COOKIE_PATHS) == set(COOKIE_ORIGIN_PATHS)  # scope reminder

    resp = app.test_client().get("/api/docs/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    csp = resp.headers.get("Content-Security-Policy", "")

    external_scripts = [
        line.strip()
        for line in body.splitlines()
        if "<script" in line and "src=" in line and "http" in line
    ]
    has_sri = (
        all("integrity=" in line for line in external_scripts)
        if external_scripts
        else True
    )

    assert not external_scripts or (has_sri and "script-src" in csp), (
        "public docs page loads third-party scripts on the auth-cookie origin "
        f"without SRI/CSP: {external_scripts!r} csp={csp!r}"
    )


# ---------------------------------------------------------------------------
# Origin check must not break legitimate clients
# ---------------------------------------------------------------------------


def _upload(client, headers: dict[str, str]):
    return client.post(
        "/api/v2/conversations/conv-abc/workspace/upload",
        data={"file": (io.BytesIO(b"hello"), "note.txt")},
        content_type="multipart/form-data",
        headers=headers,
    )


@pytest.fixture
def upload_logdir(tmp_path, monkeypatch):
    import gptme.server.workspace_api as workspace_api

    logdir = tmp_path / "conv-abc"
    (logdir / "attachments").mkdir(parents=True)
    manager = MagicMock()
    manager.logdir = logdir
    monkeypatch.setattr(workspace_api.LogManager, "load", lambda *a, **k: manager)
    return logdir


def test_upload_allows_same_origin_cookie_request(app, upload_logdir):
    """The bundled webui posts from the server's own origin."""
    client = _client_with_auth_cookie(app)
    resp = _upload(client, {"Origin": "http://localhost"})
    assert resp.status_code == 200, resp.get_json()
    assert (upload_logdir / "attachments" / "note.txt").exists()


def test_upload_allows_same_origin_behind_tls_proxy(app, upload_logdir):
    """Behind nginx the browser origin is https while the WSGI scheme is http."""
    client = _client_with_auth_cookie(app)
    resp = _upload(
        client,
        {"Origin": "https://localhost", "X-Forwarded-Proto": "https"},
    )
    assert resp.status_code == 200, resp.get_json()


def test_upload_rejects_https_origin_without_forwarded_proto(app, upload_logdir):
    """Scheme is part of the origin: https://host is not http://host."""
    client = _client_with_auth_cookie(app)
    resp = _upload(client, {"Origin": "https://localhost"})
    assert resp.status_code == 403


def test_upload_allows_cookie_request_without_origin(app, upload_logdir):
    """Non-browser clients send neither Origin nor Referer."""
    client = _client_with_auth_cookie(app)
    resp = _upload(client, {})
    assert resp.status_code == 200, resp.get_json()


def test_upload_rejects_null_origin_cookie_request(app, upload_logdir):
    """Sandboxed iframes and data: URLs send ``Origin: null``."""
    client = _client_with_auth_cookie(app)
    resp = _upload(client, {"Origin": "null"})
    assert resp.status_code == 403


def test_upload_falls_back_to_referer(app, upload_logdir):
    client = _client_with_auth_cookie(app)
    resp = _upload(client, {"Referer": f"{FOREIGN_SAME_SITE_ORIGIN}/evil.html"})
    assert resp.status_code == 403
    resp = _upload(client, {"Referer": "http://localhost/chat"})
    assert resp.status_code == 200, resp.get_json()


def test_upload_allows_bearer_request_from_foreign_origin(app, upload_logdir):
    """A bearer header proves the caller holds the token: no CSRF exposure."""
    client = app.test_client()
    resp = _upload(
        client,
        {"Origin": FOREIGN_SAME_SITE_ORIGIN, "Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 200, resp.get_json()


def test_upload_allows_configured_cors_origin(monkeypatch, upload_logdir):
    """A named ``--cors-origin`` is trusted for credentialed requests."""
    cors_app = _make_app(
        monkeypatch, cors_origin="https://chat.gptme.org,tauri://localhost"
    )
    client = _client_with_auth_cookie(cors_app)
    assert _upload(client, {"Origin": "https://chat.gptme.org"}).status_code == 200
    assert _upload(client, {"Origin": "tauri://localhost"}).status_code == 200
    assert _upload(client, {"Origin": FOREIGN_SAME_SITE_ORIGIN}).status_code == 403


def test_wildcard_cors_origin_does_not_trust_every_origin(monkeypatch, upload_logdir):
    """``--cors-origin '*'`` never enables credentials, so it grants no trust."""
    cors_app = _make_app(monkeypatch, cors_origin="*")
    client = _client_with_auth_cookie(cors_app)
    assert _upload(client, {"Origin": FOREIGN_SAME_SITE_ORIGIN}).status_code == 403


def test_foreign_origin_cookie_delete_is_rejected(app):
    """The check covers every unsafe method, not only multipart routes."""
    client = _client_with_auth_cookie(app)
    resp = client.delete(
        "/api/v2/conversations/conv-abc",
        headers={"Origin": FOREIGN_SAME_SITE_ORIGIN},
    )
    assert resp.status_code == 403


def test_foreign_origin_cookie_get_is_not_rejected(app):
    """Safe methods stay readable (SSE uses the cookie via EventSource)."""
    client = _client_with_auth_cookie(app)
    resp = client.get("/api/v2", headers={"Origin": FOREIGN_SAME_SITE_ORIGIN})
    assert resp.status_code != 403


def test_auth_cookie_not_secure_over_plain_http(app):
    """Plain local HTTP must still get a usable (non-Secure) cookie."""
    resp = app.test_client().post(
        "/api/v2/auth/cookie", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert resp.status_code == 200
    assert not any("secure" in c.lower() for c in resp.headers.getlist("Set-Cookie"))


def test_bundled_ui_framable_by_same_origin_only(tmp_path, monkeypatch):
    """computer.html iframes ``/chat``, so same-origin framing must still work."""
    ui_dir = tmp_path / "webui"
    ui_dir.mkdir()
    (ui_dir / "index.html").write_text("<!doctype html><html><body>ui</body></html>")

    ui_app = _make_app(monkeypatch, webui_dir=ui_dir)
    resp = ui_app.test_client().get("/chat")
    assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in resp.headers.get("Content-Security-Policy", "")


def test_api_docs_init_script_is_served_same_origin(app):
    """The docs CSP forbids inline script, so the bootstrap is a 'self' asset."""
    client = app.test_client()
    body = client.get("/api/docs/").get_data(as_text=True)
    assert "window.onload" not in body
    resp = client.get("/api/docs/swagger-init.js")
    assert resp.status_code == 200
    assert "SwaggerUIBundle" in resp.get_data(as_text=True)


@pytest.mark.parametrize("second_origin", ["https://second.example", None])
def test_cookie_origin_policy_is_per_app(monkeypatch, upload_logdir, second_origin):
    first = _make_app(monkeypatch, cors_origin="https://first.example")
    second = _make_app(monkeypatch, cors_origin=second_origin)
    first_client = _client_with_auth_cookie(first)
    second_client = _client_with_auth_cookie(second)
    assert _upload(first_client, {"Origin": "https://first.example"}).status_code == 200
    assert (
        _upload(first_client, {"Origin": "https://second.example"}).status_code == 403
    )
    assert (
        _upload(second_client, {"Origin": "https://first.example"}).status_code == 403
    )
    assert _upload(second_client, {"Origin": "https://second.example"}).status_code == (
        200 if second_origin else 403
    )
    for app_, origin in ((first, "https://first.example"), (second, second_origin)):
        csp = app_.test_client().get("/").headers["Content-Security-Policy"]
        assert csp == "frame-ancestors 'self'" + (f" {origin}" if origin else "")


@pytest.mark.parametrize(
    "authorization", ["Bearer ", "Bearer", "Basic x", "Bearer wrong"]
)
def test_invalid_bearer_never_falls_back_to_cookie(app, authorization):
    client = _client_with_auth_cookie(app)
    response = client.post(
        "/api/v2/auth/cookie",
        headers={"Origin": FOREIGN_SAME_SITE_ORIGIN, "Authorization": authorization},
    )
    assert response.status_code == 401


def test_unmatched_preview_path_keeps_ui_frame_protection(monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>UI</title>")
    app = _make_app(monkeypatch, webui_dir=tmp_path)
    response = app.test_client().get("/preview/not-a-port/")
    assert response.status_code == 200
    assert b"<title>UI</title>" in response.data
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in response.headers.get(
        "Content-Security-Policy", ""
    )


def test_collecting_transport_tests_does_not_set_model_environment():
    import subprocess
    import sys
    from pathlib import Path

    script = (
        "import os, runpy; "
        "os.environ.pop('MODEL', None); os.environ.pop('OPENAI_BASE_URL', None); "
        f"runpy.run_path({str(Path(__file__).resolve())!r}); "
        "assert 'MODEL' not in os.environ; assert 'OPENAI_BASE_URL' not in os.environ"
    )
    subprocess.run([sys.executable, "-c", script], check=True)
