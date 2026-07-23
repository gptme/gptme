"""Tests for DNS-rebinding protection via Host-header validation.

DNS rebinding lets a malicious page bypass CORS by resolving its domain to
127.0.0.1.  The browser then treats the page as same-origin with the local
server, skipping preflight checks.  Host-header validation blocks this because
the rebinding page still sends its own domain name as the Host header.

References: gptme/gptme#3320
"""

import pytest

flask = pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)


def _make_app(host: str = "127.0.0.1"):
    from gptme.server.app import create_app

    return create_app(host=host)


def _api_get(app, host_header: str | None, path: str = "/api/v2/conversations"):
    with app.test_client() as client:
        if host_header is None:
            # Werkzeug test client always injects localhost; override via environ_base
            return client.get(path, environ_base={"HTTP_HOST": ""})
        return client.get(path, headers={"Host": host_header})


# ---------------------------------------------------------------------------
# Unit tests for the helper functions
# ---------------------------------------------------------------------------


class TestParseHostHeader:
    def test_plain_hostname(self):
        from gptme.server.auth import _parse_host_header

        assert _parse_host_header("localhost") == "localhost"

    def test_hostname_with_port(self):
        from gptme.server.auth import _parse_host_header

        assert _parse_host_header("localhost:5700") == "localhost"

    def test_ipv4_with_port(self):
        from gptme.server.auth import _parse_host_header

        assert _parse_host_header("127.0.0.1:5700") == "127.0.0.1"

    def test_ipv4_without_port(self):
        from gptme.server.auth import _parse_host_header

        assert _parse_host_header("127.0.0.1") == "127.0.0.1"

    def test_ipv6_literal_with_port(self):
        from gptme.server.auth import _parse_host_header

        assert _parse_host_header("[::1]:5700") == "[::1]"

    def test_ipv6_literal_without_port(self):
        from gptme.server.auth import _parse_host_header

        assert _parse_host_header("[::1]") == "[::1]"

    def test_external_domain(self):
        from gptme.server.auth import _parse_host_header

        assert _parse_host_header("evil.example.com") == "evil.example.com"

    def test_external_domain_with_port(self):
        from gptme.server.auth import _parse_host_header

        assert _parse_host_header("evil.example.com:80") == "evil.example.com"


# ---------------------------------------------------------------------------
# Integration tests via Flask test client
# ---------------------------------------------------------------------------


class TestHostValidation:
    """API requests must carry an allowed Host header."""

    def test_localhost_host_allowed(self):
        app = _make_app(host="127.0.0.1")
        resp = _api_get(app, "localhost")
        assert resp.status_code != 403

    def test_127_0_0_1_host_allowed(self):
        app = _make_app(host="127.0.0.1")
        resp = _api_get(app, "127.0.0.1")
        assert resp.status_code != 403

    def test_127_0_0_1_with_port_allowed(self):
        app = _make_app(host="127.0.0.1")
        resp = _api_get(app, "127.0.0.1:5700")
        assert resp.status_code != 403

    def test_localhost_with_port_allowed(self):
        app = _make_app(host="127.0.0.1")
        resp = _api_get(app, "localhost:5700")
        assert resp.status_code != 403

    def test_ipv6_loopback_allowed(self):
        app = _make_app(host="127.0.0.1")
        resp = _api_get(app, "[::1]:5700")
        assert resp.status_code != 403

    def test_dns_rebinding_domain_rejected(self):
        """A DNS-rebound page sends its original domain as Host — must be rejected."""
        app = _make_app(host="127.0.0.1")
        resp = _api_get(app, "evil.example.com")
        assert resp.status_code == 403
        data = resp.get_json()
        assert data is not None
        assert "error" in data

    def test_dns_rebinding_with_port_rejected(self):
        app = _make_app(host="127.0.0.1")
        resp = _api_get(app, "evil.example.com:5700")
        assert resp.status_code == 403

    def test_missing_host_header_rejected(self):
        """HTTP/1.1 requires a Host header; absent means a raw client that
        bypassed the normal stack.  validate_host_header rejects it.

        Note: Flask's Werkzeug test client always normalises a missing HTTP_HOST
        to 'localhost', so we test this via a direct call to validate_host_header
        with a mocked request object instead of an integration request.
        """
        from unittest.mock import MagicMock, patch

        from gptme.server.auth import init_host_validation, validate_host_header

        init_host_validation(bind_host="127.0.0.1")

        mock_request = MagicMock()
        mock_request.headers = {}  # no Host header
        mock_request.path = "/api/v2/conversations"

        app = _make_app(host="127.0.0.1")
        with app.test_request_context("/api/v2/conversations"):
            with patch("gptme.server.auth.request", mock_request):
                result = validate_host_header()
            assert result is not None, "Expected a 403 response for missing Host"
            response, status = result
            assert status == 403

    def test_non_api_path_not_blocked(self):
        """Static assets (webui) must not be blocked by Host validation.

        The before_request hook only applies to /api/ paths so the webui
        continues to load even if the Host header is unexpected.
        """
        app = _make_app(host="127.0.0.1")
        with app.test_client() as client:
            resp = client.get("/", headers={"Host": "evil.example.com"})
        # Static routes return 200 for index.html regardless of Host
        assert resp.status_code == 200

    def test_custom_bind_host_allowed(self):
        """When the server binds to a custom address, that address is allowed."""
        app = _make_app(host="0.0.0.0")
        resp = _api_get(app, "0.0.0.0")
        assert resp.status_code != 403

    def test_subdomain_of_loopback_not_allowed(self):
        """'localhost.evil.com' looks like localhost but is not — reject it."""
        app = _make_app(host="127.0.0.1")
        resp = _api_get(app, "localhost.evil.com")
        assert resp.status_code == 403
