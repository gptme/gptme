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
    _check_port,
    _is_websocket_upgrade,
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
