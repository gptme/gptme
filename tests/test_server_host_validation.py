"""Tests for Host-header validation (DNS-rebinding hardening) in gptme-server.

gptme-server disables bearer auth for loopback binds. CORS preflight blocks
plain cross-origin JSON, but DNS rebinding defeats CORS: a malicious page whose
hostname re-resolves to 127.0.0.1 becomes same-origin with the local server and
gains full unauthenticated API access (which includes shell execution via the
agent). Validating the Host header blocks that vector.

See issue #3320.
"""

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

import gptme.server.auth as auth_mod  # fmt: skip
from gptme.server.app import create_app  # fmt: skip

pytestmark = [pytest.mark.timeout(15)]

# A lightweight endpoint that exists and returns JSON. Host validation runs as a
# before_request hook, so it fires before auth/routing regardless of endpoint.
HEALTH = "/api/v2/server/health"


def _client(host: str = "127.0.0.1", allowed_hosts: list[str] | None = None):
    app = create_app(host=host, allowed_hosts=allowed_hosts)
    app.config["TESTING"] = True
    return app.test_client()


def _get(client: FlaskClient, host_header: str):
    return client.get(HEALTH, headers={"Host": host_header})


class TestLoopbackAllowed:
    """Loopback binds enforce validation but allow the standard local hosts."""

    def test_localhost_with_port_allowed(self):
        resp = _get(_client(), "localhost:5700")
        assert resp.status_code == 200

    def test_localhost_without_port_allowed(self):
        resp = _get(_client(), "localhost")
        assert resp.status_code == 200

    def test_ipv4_loopback_with_port_allowed(self):
        resp = _get(_client(), "127.0.0.1:5700")
        assert resp.status_code == 200

    def test_ipv4_loopback_without_port_allowed(self):
        resp = _get(_client(), "127.0.0.1")
        assert resp.status_code == 200

    def test_ipv6_loopback_allowed(self):
        resp = _get(_client(), "[::1]:5700")
        assert resp.status_code == 200

    def test_trailing_dot_localhost_allowed(self):
        # "localhost." is the valid absolute-DNS root form and resolves to the
        # loopback host; it must be accepted (regression: #3324 Greptile P1).
        assert _get(_client(), "localhost.").status_code == 200
        assert _get(_client(), "localhost.:5700").status_code == 200


class TestRebindingRejected:
    """A Host that isn't on the allow-list is rejected with a clear 403."""

    def test_evil_host_rejected(self):
        resp = _get(_client(), "attacker.example.com")
        assert resp.status_code == 403
        error = resp.get_json()["error"]
        # Error must name the offending host and how to allow it.
        assert "attacker.example.com" in error
        assert "--allowed-hosts" in error

    def test_evil_host_with_port_rejected(self):
        resp = _get(_client(), "attacker.example.com:5700")
        assert resp.status_code == 403


class TestAllowedHostsExtension:
    """--allowed-hosts / allowed_hosts extends the allow-list for proxied setups."""

    def test_configured_host_allowed(self):
        client = _client(allowed_hosts=["gptme.local"])
        assert _get(client, "gptme.local").status_code == 200
        assert _get(client, "gptme.local:8080").status_code == 200

    def test_default_hosts_still_allowed_with_extension(self):
        client = _client(allowed_hosts=["gptme.local"])
        assert _get(client, "localhost").status_code == 200

    def test_other_host_still_rejected_with_extension(self):
        client = _client(allowed_hosts=["gptme.local"])
        assert _get(client, "attacker.example.com").status_code == 403


class TestConfiguredBindHost:
    """A concrete (non-wildcard) bind host is added to the allow-list."""

    def test_bind_host_allowed(self):
        # Binding to a concrete host enables auth (non-loopback), so validation
        # is skipped there; but a loopback bind with a custom host stays enforced.
        client = _client(host="127.0.0.1", allowed_hosts=["myhost.internal"])
        assert _get(client, "myhost.internal").status_code == 200


class TestDisableAuthBypass:
    """GPTME_DISABLE_AUTH setups (cloud pods behind authenticated ingress) must
    not break: Host validation is skipped so the operator owns security."""

    def test_disable_auth_skips_host_validation(self, monkeypatch):
        monkeypatch.setenv("GPTME_DISABLE_AUTH", "true")
        # 0.0.0.0 bind with auth disabled via env: any Host must pass through.
        client = _client(host="0.0.0.0")
        assert auth_mod._host_validation_enabled is False
        assert _get(client, "attacker.example.com").status_code == 200

    def test_disable_auth_honors_explicit_allowed_hosts(self, monkeypatch):
        monkeypatch.setenv("GPTME_DISABLE_AUTH", "true")
        # If the operator opts into an allow-list, we enforce it even under
        # GPTME_DISABLE_AUTH.
        client = _client(host="0.0.0.0", allowed_hosts=["ingress.internal"])
        assert auth_mod._host_validation_enabled is True
        assert _get(client, "ingress.internal").status_code == 200
        assert _get(client, "attacker.example.com").status_code == 403


class TestAuthEnabledSkipsValidation:
    """When auth is enabled (network bind), the bearer token gates access and
    Host validation is skipped — an evil Host reaches auth (401), not 403."""

    def test_network_bind_skips_host_validation(self, monkeypatch):
        # Ensure no env override is in play.
        monkeypatch.delenv("GPTME_DISABLE_AUTH", raising=False)
        client = _client(host="0.0.0.0")
        assert auth_mod._host_validation_enabled is False
        # Host check skipped → request falls through to bearer auth, which
        # rejects the missing token with 401 (not a 403 host rejection).
        resp = _get(client, "attacker.example.com")
        assert resp.status_code == 401


class TestHostnameExtraction:
    """Unit coverage for the Host-header parsing helper."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("localhost", "localhost"),
            ("localhost:5700", "localhost"),
            ("127.0.0.1:5700", "127.0.0.1"),
            ("[::1]", "::1"),
            ("[::1]:5700", "::1"),
            ("Example.COM:80", "example.com"),
            ("localhost.", "localhost"),
            ("localhost.:5700", "localhost"),
            ("127.0.0.1.", "127.0.0.1"),
        ],
    )
    def test_extract_hostname(self, raw, expected):
        assert auth_mod._extract_hostname(raw) == expected
