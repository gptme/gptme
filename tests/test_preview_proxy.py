"""Tests for the preview port proxy (gptme-cloud#910, Option A).

Covers:
- SSRF guard: privileged ports (<1024) and blocked ports (5700, 5900) are rejected.
- HTTP streaming proxy: successful and unreachable-target paths.
- WebSocket detection helper.
- ``_check_port`` boundary conditions.
"""

import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

# Skip entire module when Flask is not installed.
pytest.importorskip("flask", reason="flask not installed; install -E server")

from flask.testing import FlaskClient  # fmt: skip

from gptme.server.preview_proxy_api import (  # fmt: skip
    _BLOCKED_PORTS,
    _MIN_ALLOWED_PORT,
    PREVIEW_CSP_SANDBOX,
    _check_port,
    _forward_request_headers,
    _forward_response_headers,
    _is_websocket_upgrade,
    _sanitize_query_string,
)

pytestmark = [pytest.mark.timeout(10)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def _loopback_http_server(response_body: bytes = b"hello", status: int = 200):
    """Spin up a minimal HTTP server on a random loopback port.

    Yields the port number.  The server shuts down when the context exits.
    """

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, *args):  # silence test output
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield port
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# Unit tests for _check_port
# ---------------------------------------------------------------------------


class TestCheckPort:
    def test_accepts_valid_high_port(self):
        assert _check_port(8080) is None

    def test_accepts_minimum_allowed_port(self):
        assert _check_port(_MIN_ALLOWED_PORT) is None

    def test_rejects_port_zero(self):
        assert _check_port(0) is not None

    def test_rejects_privileged_port_80(self):
        assert _check_port(80) is not None

    def test_rejects_privileged_port_1023(self):
        assert _check_port(1023) is not None

    def test_rejects_port_above_65535(self):
        assert _check_port(65536) is not None

    @pytest.mark.parametrize("port", sorted(_BLOCKED_PORTS))
    def test_rejects_explicitly_blocked_port(self, port: int):
        err = _check_port(port)
        assert err is not None, f"port {port} should be blocked"

    def test_accepts_novnc_websockify_port(self):
        """Port 6080 (noVNC / websockify) must be allowed."""
        assert _check_port(6080) is None

    def test_accepts_vite_default_port(self):
        """Port 5173 (Vite dev server default) must be allowed."""
        assert _check_port(5173) is None


# ---------------------------------------------------------------------------
# Unit tests for _is_websocket_upgrade
# ---------------------------------------------------------------------------


class TestIsWebsocketUpgrade:
    def test_detects_upgrade_request(self, client: FlaskClient):
        """_is_websocket_upgrade returns True for an Upgrade: websocket header."""
        with client.application.test_request_context(
            "/preview/6080/",
            headers={
                "Upgrade": "websocket",
                "Connection": "Upgrade",
            },
        ):
            import flask

            assert _is_websocket_upgrade(flask.request) is True

    def test_ignores_plain_request(self, client: FlaskClient):
        with client.application.test_request_context("/preview/6080/"):
            import flask

            assert _is_websocket_upgrade(flask.request) is False

    def test_case_insensitive(self, client: FlaskClient):
        with client.application.test_request_context(
            "/preview/6080/",
            headers={
                "Upgrade": "WebSocket",
                "Connection": "upgrade",
            },
        ):
            import flask

            assert _is_websocket_upgrade(flask.request) is True


# ---------------------------------------------------------------------------
# Integration tests via Flask test client
# ---------------------------------------------------------------------------


class TestPreviewProxySSRF:
    """Proxy returns 400 for disallowed ports (SSRF guard)."""

    def test_blocks_port_80(self, client: FlaskClient):
        resp = client.get("/preview/80/index.html")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data

    def test_blocks_port_1023(self, client: FlaskClient):
        resp = client.get("/preview/1023/")
        assert resp.status_code == 400

    def test_blocks_gptme_server_port(self, client: FlaskClient):
        """Port 5700 (gptme-server) is explicitly blocked to prevent loops."""
        resp = client.get("/preview/5700/")
        assert resp.status_code == 400

    def test_blocks_raw_vnc_port(self, client: FlaskClient):
        """Port 5900 (x11vnc) is blocked; only websockify/noVNC on 6080 is allowed."""
        resp = client.get("/preview/5900/")
        assert resp.status_code == 400


class TestPreviewProxyHTTP:
    """HTTP forwarding through the proxy."""

    def test_proxy_successful_get(self, client: FlaskClient):
        """The proxy forwards a GET to a real local server and streams back the body."""
        with _loopback_http_server(b"world") as port:
            resp = client.get(f"/preview/{port}/")
            assert resp.status_code == 200
            assert resp.data == b"world"

    def test_proxy_unreachable_target_returns_502(self, client: FlaskClient):
        """A connection refused to a closed port produces a 502."""
        # Pick a port that is very likely to be closed (OS will refuse immediately).
        # Bind + immediately close to get a port we know is free/closed.
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            closed_port = s.getsockname()[1]
        # s is now closed; the port is not listening.
        resp = client.get(f"/preview/{closed_port}/")
        assert resp.status_code == 502

    def test_proxy_forwards_path_and_query(self, client: FlaskClient):
        """The subpath and query-string are forwarded to the upstream server."""
        received: list[str] = []

        class _CaptureHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()

        client.get(f"/preview/{port}/some/path?foo=bar")
        t.join(timeout=3)

        assert received == ["/some/path?foo=bar"]
        srv.server_close()

    def test_api_v2_preview_is_not_mounted(self, client: FlaskClient):
        """Untrusted preview content must not live under the /api/ cookie path."""
        resp = client.get("/api/v2/preview/6080/")
        assert resp.status_code == 404

    def test_does_not_forward_authorization_or_cookie(self, client: FlaskClient):
        """Bearer tokens and auth cookies must not leak to loopback listeners."""
        captured: list[dict[str, str]] = []

        class _CaptureHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                captured.append({k.lower(): v for k, v in self.headers.items()})
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()

        client.get(
            f"/preview/{port}/",
            headers={
                "Authorization": "Bearer super-secret-token",
                "Cookie": "gptme_token=super-secret-token",
                "X-Forwarded-User": "erik",
                "X-Auth-Request-Access-Token": "traefik-token",
            },
        )
        t.join(timeout=3)
        srv.server_close()

        assert captured, "upstream should have received the proxied request"
        headers = captured[0]
        assert "authorization" not in headers
        assert "cookie" not in headers
        assert "x-forwarded-user" not in headers
        assert "x-auth-request-access-token" not in headers

    def test_strips_token_query_param(self, client: FlaskClient):
        """Deprecated ?token= auth must not be forwarded upstream."""
        received: list[str] = []

        class _CaptureHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()

        client.get(f"/preview/{port}/page?token=secret&foo=bar")
        t.join(timeout=3)
        srv.server_close()

        assert received == ["/page?foo=bar"]

    def test_decoded_gzip_does_not_keep_content_encoding(self, client: FlaskClient):
        """Decoded gzip bodies must not keep Content-Encoding: gzip."""
        import gzip

        raw = b"hello gzip body"
        compressed = gzip.compress(raw)

        class _GzipHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(compressed)))
                self.end_headers()
                self.wfile.write(compressed)

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), _GzipHandler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()

        resp = client.get(
            f"/preview/{port}/",
            headers={"Accept-Encoding": "gzip"},
        )
        t.join(timeout=3)
        srv.server_close()

        assert resp.status_code == 200
        assert resp.data == raw
        assert "Content-Encoding" not in resp.headers
        assert resp.headers.get("Content-Encoding") is None

    def test_html_response_is_unique_origin_sandboxed(self, client: FlaskClient):
        """HTML previews must not inherit gptme-server origin privileges."""
        html = (
            b"<html><body><script>fetch('/api/v2/conversations')</script></body></html>"
        )

        class _HtmlHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.send_header(
                    "Content-Security-Policy",
                    "sandbox allow-scripts allow-same-origin",
                )
                self.end_headers()
                self.wfile.write(html)

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), _HtmlHandler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()

        resp = client.get(f"/preview/{port}/index.html")
        t.join(timeout=3)
        srv.server_close()

        assert resp.status_code == 200
        csp = resp.headers.get("Content-Security-Policy", "")
        assert csp == PREVIEW_CSP_SANDBOX
        assert "allow-same-origin" not in csp
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_plain_text_does_not_get_csp_sandbox(self, client: FlaskClient):
        """Non-document responses only get nosniff, not a document sandbox."""
        with _loopback_http_server(b"hello") as port:
            resp = client.get(f"/preview/{port}/")
        assert resp.status_code == 200
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert "Content-Security-Policy" not in resp.headers

    def test_strips_upstream_set_cookie(self, client: FlaskClient):
        """Untrusted upstreams must not overwrite the preview auth cookie."""

        class _CookieHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header(
                    "Set-Cookie",
                    "gptme_auth=poisoned; Path=/preview/",
                )
                self.send_header("Clear-Site-Data", '"cookies"')
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), _CookieHandler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()

        resp = client.get(f"/preview/{port}/")
        t.join(timeout=3)
        srv.server_close()

        assert resp.status_code == 200
        assert resp.data == b"ok"
        assert resp.headers.get("Set-Cookie") is None
        assert resp.headers.getlist("Set-Cookie") == []
        assert resp.headers.get("Clear-Site-Data") is None
        assert client.get_cookie("gptme_auth") is None


class TestHeaderHelpers:
    def test_sanitize_drops_token(self):
        assert _sanitize_query_string("token=secret&foo=bar") == "foo=bar"
        assert _sanitize_query_string("token=secret") == ""
        assert _sanitize_query_string("") == ""

    def test_forward_request_headers_strips_identity(self):
        headers = [
            ("Authorization", "Bearer abc"),
            ("Cookie", "gptme_token=abc"),
            ("X-Forwarded-User", "bob"),
            ("X-Auth-Request-Email", "a@b.c"),
            ("Accept", "text/html"),
            ("Host", "example.com"),
            ("Connection", "keep-alive"),
            ("Accept-Encoding", "gzip"),
        ]
        forwarded = _forward_request_headers(headers, drop_accept_encoding=True)
        lower = {k.lower() for k in forwarded}
        assert "authorization" not in lower
        assert "cookie" not in lower
        assert "x-forwarded-user" not in lower
        assert "x-auth-request-email" not in lower
        assert "host" not in lower
        assert "connection" not in lower
        assert forwarded.get("Accept-Encoding") == "identity"
        assert forwarded["Accept"] == "text/html"

    def test_forward_response_headers_strips_cookies(self):
        headers = {
            "Content-Type": "text/html",
            "Set-Cookie": "gptme_auth=poisoned; Path=/preview/",
            "Set-Cookie2": "obsolete=1",
            "Clear-Site-Data": '"cookies"',
            "Content-Encoding": "gzip",
            "Content-Security-Policy": "sandbox allow-same-origin",
            "Connection": "close",
            "X-Custom": "keep",
        }
        forwarded = _forward_response_headers(headers)
        lower = {key.lower() for key, _ in forwarded}
        assert "set-cookie" not in lower
        assert "set-cookie2" not in lower
        assert "clear-site-data" not in lower
        assert "content-encoding" not in lower
        assert "content-security-policy" not in lower
        assert "connection" not in lower
        assert ("X-Custom", "keep") in forwarded
        assert ("Content-Type", "text/html") in forwarded
