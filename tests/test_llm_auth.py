"""Comprehensive tests for LLM authentication helpers.

Covers pure-function auth logic that doesn't require API calls:
- Server auth helpers (is_local_host, generate_token, get_server_token, init_auth)
- Server auth middleware edge cases (invalid scheme, cookie auth, query param)
- OpenAI subscription auth (PKCE, JWT decode, token I/O, port check)
- gptme provider edge cases (near-expiry boundary, malformed JSON, URL normalization)
"""

import base64
import hashlib
import json
import os
import socket
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# --- Server auth helpers ---

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)


class TestIsLocalHost:
    """Tests for is_local_host() hostname detection."""

    def test_localhost(self):
        from gptme.server.auth import is_local_host

        assert is_local_host("localhost") is True

    def test_ipv4_loopback(self):
        from gptme.server.auth import is_local_host

        assert is_local_host("127.0.0.1") is True

    def test_ipv6_loopback(self):
        from gptme.server.auth import is_local_host

        assert is_local_host("::1") is True

    def test_all_interfaces(self):
        from gptme.server.auth import is_local_host

        assert is_local_host("0.0.0.0") is False

    def test_external_host(self):
        from gptme.server.auth import is_local_host

        assert is_local_host("192.168.1.1") is False

    def test_hostname(self):
        from gptme.server.auth import is_local_host

        assert is_local_host("myserver.example.com") is False

    def test_empty_string(self):
        from gptme.server.auth import is_local_host

        assert is_local_host("") is False


class TestGenerateToken:
    """Tests for generate_token() cryptographic token generation."""

    def test_returns_string(self):
        from gptme.server.auth import generate_token

        token = generate_token()
        assert isinstance(token, str)

    def test_sufficient_length(self):
        from gptme.server.auth import generate_token

        token = generate_token()
        assert len(token) >= 32

    def test_uniqueness(self):
        from gptme.server.auth import generate_token

        tokens = {generate_token() for _ in range(10)}
        assert len(tokens) == 10, "Generated tokens should be unique"

    def test_url_safe(self):
        from gptme.server.auth import generate_token

        token = generate_token()
        # URL-safe characters only
        safe_chars = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        )
        assert all(c in safe_chars for c in token)


class TestGetServerToken:
    """Tests for get_server_token() token retrieval and caching."""

    def test_from_env(self):
        import gptme.server.auth

        original = gptme.server.auth._server_token
        try:
            gptme.server.auth._server_token = None
            with patch.dict(os.environ, {"GPTME_SERVER_TOKEN": "env-token-xyz"}):
                token = gptme.server.auth.get_server_token()
                assert token == "env-token-xyz"
        finally:
            gptme.server.auth._server_token = original

    def test_auto_generates_when_no_env(self):
        import gptme.server.auth

        original = gptme.server.auth._server_token
        try:
            gptme.server.auth._server_token = None
            with patch.dict(os.environ, {}):
                os.environ.pop("GPTME_SERVER_TOKEN", None)
                token = gptme.server.auth.get_server_token()
                assert token is not None
                assert len(token) >= 32
        finally:
            gptme.server.auth._server_token = original

    def test_caches_token(self):
        import gptme.server.auth

        original = gptme.server.auth._server_token
        try:
            gptme.server.auth._server_token = None
            with patch.dict(os.environ, {}):
                os.environ.pop("GPTME_SERVER_TOKEN", None)
                token1 = gptme.server.auth.get_server_token()
                token2 = gptme.server.auth.get_server_token()
                assert token1 == token2, "Token should be cached across calls"
        finally:
            gptme.server.auth._server_token = original


class TestInitAuth:
    """Tests for init_auth() state management."""

    def test_local_host_disables_auth(self):
        import gptme.server.auth

        original = gptme.server.auth._auth_enabled
        try:
            gptme.server.auth._auth_enabled = True
            result = gptme.server.auth.init_auth("127.0.0.1", display=False)
            assert gptme.server.auth._auth_enabled is False
            assert result is None
        finally:
            gptme.server.auth._auth_enabled = original

    def test_network_host_enables_auth(self):
        import gptme.server.auth

        original_enabled = gptme.server.auth._auth_enabled
        original_token = gptme.server.auth._server_token
        try:
            gptme.server.auth._auth_enabled = False
            gptme.server.auth._server_token = None
            with patch.dict(os.environ, {"GPTME_SERVER_TOKEN": "net-token"}):
                result = gptme.server.auth.init_auth("0.0.0.0", display=False)
                assert gptme.server.auth._auth_enabled is True
                assert result == "net-token"
        finally:
            gptme.server.auth._auth_enabled = original_enabled
            gptme.server.auth._server_token = original_token

    def test_env_disable_overrides_network(self):
        import gptme.server.auth

        original = gptme.server.auth._auth_enabled
        try:
            gptme.server.auth._auth_enabled = True
            with patch.dict(os.environ, {"GPTME_DISABLE_AUTH": "true"}):
                result = gptme.server.auth.init_auth("0.0.0.0", display=False)
                assert gptme.server.auth._auth_enabled is False
                assert result is None
        finally:
            gptme.server.auth._auth_enabled = original
            os.environ.pop("GPTME_DISABLE_AUTH", None)

    def test_env_disable_values(self):
        """GPTME_DISABLE_AUTH accepts true, 1, yes (case-insensitive)."""
        import gptme.server.auth

        for val in ("true", "TRUE", "True", "1", "yes", "YES"):
            original = gptme.server.auth._auth_enabled
            try:
                gptme.server.auth._auth_enabled = True
                with patch.dict(os.environ, {"GPTME_DISABLE_AUTH": val}):
                    gptme.server.auth.init_auth("0.0.0.0", display=False)
                    assert gptme.server.auth._auth_enabled is False, (
                        f"Failed for '{val}'"
                    )
            finally:
                gptme.server.auth._auth_enabled = original
                os.environ.pop("GPTME_DISABLE_AUTH", None)


class TestRequireAuthEdgeCases:
    """Edge cases for the require_auth decorator."""

    @pytest.fixture(autouse=True)
    def _setup_auth(self):
        import gptme.server.auth

        original_token = gptme.server.auth._server_token
        original_enabled = gptme.server.auth._auth_enabled

        gptme.server.auth._server_token = "test-token-abc"
        gptme.server.auth._auth_enabled = True

        yield

        gptme.server.auth._server_token = original_token
        gptme.server.auth._auth_enabled = original_enabled

    def _make_app(self):
        from flask import Flask

        from gptme.server.auth import require_auth

        app = Flask(__name__)

        @app.route("/test")
        @require_auth
        def protected():
            return {"ok": True}

        return app

    def test_invalid_scheme(self):
        """Non-Bearer auth scheme should be rejected."""
        app = self._make_app()
        with app.test_client() as client:
            resp = client.get("/test", headers={"Authorization": "Basic dXNlcjpwYXNz"})
            assert resp.status_code == 401
            assert "scheme" in resp.json["error"].lower()

    def test_malformed_header(self):
        """Authorization header without space should be rejected."""
        app = self._make_app()
        with app.test_client() as client:
            resp = client.get("/test", headers={"Authorization": "BearerNoSpace"})
            assert resp.status_code == 401
            assert "format" in resp.json["error"].lower()

    def test_cookie_auth(self):
        """Authentication via cookie should work."""
        app = self._make_app()
        with app.test_client() as client:
            client.set_cookie("gptme_auth", "test-token-abc", domain="localhost")
            resp = client.get("/test")
            assert resp.status_code == 200

    def test_cookie_wrong_token(self):
        """Cookie with wrong token should be rejected."""
        app = self._make_app()
        with app.test_client() as client:
            client.set_cookie("gptme_auth", "wrong-token", domain="localhost")
            resp = client.get("/test")
            assert resp.status_code == 401

    def test_query_param_auth(self):
        """Query parameter authentication should work (deprecated but supported)."""
        app = self._make_app()
        with app.test_client() as client:
            resp = client.get("/test?token=test-token-abc")
            assert resp.status_code == 200

    def test_header_takes_priority_over_cookie(self):
        """Authorization header should be checked before cookie."""
        app = self._make_app()
        with app.test_client() as client:
            # Cookie has wrong token, header has correct token
            client.set_cookie("gptme_auth", "wrong-cookie-token", domain="localhost")
            resp = client.get(
                "/test", headers={"Authorization": "Bearer test-token-abc"}
            )
            assert resp.status_code == 200


# --- OpenAI Subscription auth helpers ---


class TestGeneratePKCE:
    """Tests for _generate_pkce() PKCE code verifier and challenge."""

    def test_returns_tuple(self):
        from gptme.llm.llm_openai_subscription import _generate_pkce

        result = _generate_pkce()
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_verifier_length(self):
        from gptme.llm.llm_openai_subscription import _generate_pkce

        verifier, _ = _generate_pkce()
        # secrets.token_urlsafe(32) produces ~43 chars
        assert len(verifier) >= 32

    def test_challenge_is_s256_of_verifier(self):
        """Challenge must be base64url(sha256(verifier)) per RFC 7636."""
        from gptme.llm.llm_openai_subscription import _generate_pkce

        verifier, challenge = _generate_pkce()
        # Recompute expected challenge
        digest = hashlib.sha256(verifier.encode()).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        assert challenge == expected

    def test_uniqueness(self):
        from gptme.llm.llm_openai_subscription import _generate_pkce

        pairs = {_generate_pkce() for _ in range(10)}
        assert len(pairs) == 10

    def test_no_padding(self):
        """Challenge should not have base64 padding characters."""
        from gptme.llm.llm_openai_subscription import _generate_pkce

        _, challenge = _generate_pkce()
        assert "=" not in challenge


class TestDecodeJWTPayload:
    """Tests for _decode_jwt_payload() JWT parsing without verification."""

    def _make_jwt(self, payload: dict) -> str:
        """Create a fake JWT with given payload."""
        header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
        payload_b64 = (
            base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        )
        sig = base64.urlsafe_b64encode(b"signature").rstrip(b"=").decode()
        return f"{header}.{payload_b64}.{sig}"

    def test_valid_jwt(self):
        from gptme.llm.llm_openai_subscription import _decode_jwt_payload

        jwt = self._make_jwt({"sub": "user123", "exp": 9999999999})
        result = _decode_jwt_payload(jwt)
        assert result["sub"] == "user123"
        assert result["exp"] == 9999999999

    def test_invalid_format_no_dots(self):
        from gptme.llm.llm_openai_subscription import _decode_jwt_payload

        with pytest.raises(ValueError, match="Invalid JWT"):
            _decode_jwt_payload("not-a-jwt")

    def test_invalid_format_two_parts(self):
        from gptme.llm.llm_openai_subscription import _decode_jwt_payload

        with pytest.raises(ValueError, match="Invalid JWT"):
            _decode_jwt_payload("header.payload")

    def test_handles_padding(self):
        """JWT payload may need base64 padding to decode correctly."""
        from gptme.llm.llm_openai_subscription import _decode_jwt_payload

        # Create payload that needs padding (1 byte → needs 3 padding chars)
        payload = {"x": "y"}
        jwt = self._make_jwt(payload)
        result = _decode_jwt_payload(jwt)
        assert result == payload


class TestExtractAccountId:
    """Tests for _extract_account_id() JWT claim extraction."""

    def _make_jwt(self, payload: dict) -> str:
        header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
        payload_b64 = (
            base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        )
        sig = base64.urlsafe_b64encode(b"signature").rstrip(b"=").decode()
        return f"{header}.{payload_b64}.{sig}"

    def test_extracts_org_id_from_openai_claim(self):
        from gptme.llm.llm_openai_subscription import _extract_account_id

        jwt = self._make_jwt(
            {"https://api.openai.com/auth": {"organization_id": "org-abc123"}}
        )
        assert _extract_account_id(jwt) == "org-abc123"

    def test_falls_back_to_sub(self):
        from gptme.llm.llm_openai_subscription import _extract_account_id

        jwt = self._make_jwt({"sub": "user-456"})
        assert _extract_account_id(jwt) == "user-456"

    def test_raises_when_no_account_info(self):
        from gptme.llm.llm_openai_subscription import _extract_account_id

        jwt = self._make_jwt({"iss": "test", "iat": 12345})
        with pytest.raises(ValueError, match="Could not extract account ID"):
            _extract_account_id(jwt)


class TestGetTokenExpiry:
    """Tests for _get_token_expiry() JWT expiration extraction."""

    def _make_jwt(self, payload: dict) -> str:
        header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
        payload_b64 = (
            base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        )
        sig = base64.urlsafe_b64encode(b"signature").rstrip(b"=").decode()
        return f"{header}.{payload_b64}.{sig}"

    def test_extracts_exp_claim(self):
        from gptme.llm.llm_openai_subscription import _get_token_expiry

        jwt = self._make_jwt({"exp": 1700000000})
        assert _get_token_expiry(jwt) == 1700000000.0

    def test_defaults_to_one_hour_when_no_exp(self):
        from gptme.llm.llm_openai_subscription import _get_token_expiry

        jwt = self._make_jwt({"sub": "user"})
        before = time.time() + 3600
        result = _get_token_expiry(jwt)
        after = time.time() + 3600
        assert before <= result <= after

    def test_defaults_on_invalid_jwt(self):
        from gptme.llm.llm_openai_subscription import _get_token_expiry

        before = time.time() + 3600
        result = _get_token_expiry("not-a-jwt")
        after = time.time() + 3600
        assert before <= result <= after


class TestSubscriptionTokenIO:
    """Tests for _save_tokens() and _load_tokens() persistence."""

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        from gptme.llm.llm_openai_subscription import (
            SubscriptionAuth,
            _load_tokens,
            _save_tokens,
        )

        auth = SubscriptionAuth(
            access_token="access-xyz",
            refresh_token="refresh-abc",
            account_id="acct-123",
            expires_at=9999999999.0,
        )

        with patch(
            "gptme.llm.llm_openai_subscription._get_token_storage_path",
            return_value=tmp_path / "tokens.json",
        ):
            _save_tokens(auth)
            loaded = _load_tokens()

        assert loaded is not None
        assert loaded.access_token == "access-xyz"
        assert loaded.refresh_token == "refresh-abc"
        assert loaded.account_id == "acct-123"
        assert loaded.expires_at == 9999999999.0

    def test_save_sets_restricted_permissions(self, tmp_path: Path):
        from gptme.llm.llm_openai_subscription import SubscriptionAuth, _save_tokens

        token_path = tmp_path / "tokens.json"
        auth = SubscriptionAuth(
            access_token="x",
            refresh_token=None,
            account_id="a",
            expires_at=0.0,
        )

        with patch(
            "gptme.llm.llm_openai_subscription._get_token_storage_path",
            return_value=token_path,
        ):
            _save_tokens(auth)

        assert oct(token_path.stat().st_mode & 0o777) == "0o600"

    def test_load_returns_none_when_missing(self, tmp_path: Path):
        from gptme.llm.llm_openai_subscription import _load_tokens

        with patch(
            "gptme.llm.llm_openai_subscription._get_token_storage_path",
            return_value=tmp_path / "nonexistent.json",
        ):
            assert _load_tokens() is None

    def test_load_returns_none_on_corrupt_json(self, tmp_path: Path):
        from gptme.llm.llm_openai_subscription import _load_tokens

        token_path = tmp_path / "tokens.json"
        token_path.write_text("not valid json {{{")

        with patch(
            "gptme.llm.llm_openai_subscription._get_token_storage_path",
            return_value=token_path,
        ):
            assert _load_tokens() is None

    def test_load_returns_none_on_missing_fields(self, tmp_path: Path):
        from gptme.llm.llm_openai_subscription import _load_tokens

        token_path = tmp_path / "tokens.json"
        # Missing required 'account_id' field
        token_path.write_text(json.dumps({"access_token": "x", "expires_at": 99}))

        with patch(
            "gptme.llm.llm_openai_subscription._get_token_storage_path",
            return_value=token_path,
        ):
            assert _load_tokens() is None

    def test_save_with_none_refresh_token(self, tmp_path: Path):
        from gptme.llm.llm_openai_subscription import SubscriptionAuth, _save_tokens

        token_path = tmp_path / "tokens.json"
        auth = SubscriptionAuth(
            access_token="access",
            refresh_token=None,
            account_id="acct",
            expires_at=0.0,
        )

        with patch(
            "gptme.llm.llm_openai_subscription._get_token_storage_path",
            return_value=token_path,
        ):
            _save_tokens(auth)

        data = json.loads(token_path.read_text())
        assert data["refresh_token"] is None


class TestIsPortAvailable:
    """Tests for _is_port_available() socket check."""

    def test_available_port(self):
        from gptme.llm.llm_openai_subscription import _is_port_available

        # Hold one socket to get a free port number, then check a different
        # OS-assigned port while this socket is still open — avoids TOCTOU.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
            held.bind(("127.0.0.1", 0))
            held_port = held.getsockname()[1]
            # Obtain a second free port by letting the OS pick another one
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                free_port = probe.getsockname()[1]
            # probe is released; held keeps it from being reused immediately
            assert free_port != held_port
            assert _is_port_available(free_port) is True

    def test_unavailable_port(self):
        from gptme.llm.llm_openai_subscription import _is_port_available

        # Bind to a port and keep it open
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            assert _is_port_available(port) is False


# --- gptme provider edge cases ---


class TestGptmeProviderEdgeCases:
    """Additional edge cases for gptme provider auth."""

    def test_token_near_expiry_boundary(self, tmp_path: Path):
        """Token within 60s buffer should be treated as expired."""
        from gptme.llm.llm_gptme import _load_token

        token_path = tmp_path / "gptme-cloud.json"
        # Expires in 30 seconds (within 60s buffer)
        token_data = {
            "access_token": "almost-expired",
            "expires_at": time.time() + 30,
            "server_url": "https://fleet.gptme.ai",
        }
        token_path.write_text(json.dumps(token_data))

        with patch("gptme.llm.llm_gptme._get_token_path", return_value=token_path):
            assert _load_token() is None

    def test_token_just_outside_buffer(self, tmp_path: Path):
        """Token with >60s remaining should be valid."""
        from gptme.llm.llm_gptme import _load_token

        token_path = tmp_path / "gptme-cloud.json"
        # Expires in 120 seconds (outside 60s buffer)
        token_data = {
            "access_token": "still-valid",
            "expires_at": time.time() + 120,
            "server_url": "https://fleet.gptme.ai",
        }
        token_path.write_text(json.dumps(token_data))

        with patch("gptme.llm.llm_gptme._get_token_path", return_value=token_path):
            result = _load_token()
            assert result is not None
            assert result["access_token"] == "still-valid"

    def test_token_missing_expires_at(self, tmp_path: Path):
        """Token without expires_at should be treated as expired."""
        from gptme.llm.llm_gptme import _load_token

        token_path = tmp_path / "gptme-cloud.json"
        token_data = {
            "access_token": "no-expiry",
            "server_url": "https://fleet.gptme.ai",
        }
        token_path.write_text(json.dumps(token_data))

        with patch("gptme.llm.llm_gptme._get_token_path", return_value=token_path):
            assert _load_token() is None

    def test_malformed_json_token_file(self, tmp_path: Path):
        """Corrupt token file should return None gracefully."""
        from gptme.llm.llm_gptme import _load_token

        token_path = tmp_path / "gptme-cloud.json"
        token_path.write_text("{not valid json!!")

        with patch("gptme.llm.llm_gptme._get_token_path", return_value=token_path):
            assert _load_token() is None

    def test_url_normalization_trailing_slash(self):
        """server_url with trailing slash should normalize correctly."""
        from gptme.llm.llm_gptme import get_base_url

        token_data = {
            "access_token": "test",
            "server_url": "https://custom.gptme.ai/",
        }
        config = _mock_config()
        with patch("gptme.llm.llm_gptme._load_token", return_value=token_data):
            assert get_base_url(config) == "https://custom.gptme.ai/v1"

    def test_url_normalization_already_has_v1_slash(self):
        """server_url already ending in /v1/ should not double-suffix."""
        from gptme.llm.llm_gptme import get_base_url

        token_data = {
            "access_token": "test",
            "server_url": "https://custom.gptme.ai/v1/",
        }
        config = _mock_config()
        with patch("gptme.llm.llm_gptme._load_token", return_value=token_data):
            result = get_base_url(config)
            assert result == "https://custom.gptme.ai/v1"

    def test_token_path_deterministic(self):
        """Same URL should always produce the same token path."""
        from gptme.llm.llm_gptme import _get_token_path

        path1 = _get_token_path("https://fleet.gptme.ai")
        path2 = _get_token_path("https://fleet.gptme.ai")
        assert path1 == path2

    def test_token_path_default_url(self):
        """Default URL should produce a valid token path."""
        from gptme.llm.llm_gptme import _get_token_path

        path = _get_token_path()
        assert "gptme-cloud-" in path.name
        assert path.suffix == ".json"


# --- Helpers ---


def _mock_config(env: dict[str, str] | None = None):
    """Create a minimal mock config for testing."""

    class MockConfig:
        def get_env(self, key: str) -> str | None:
            if env:
                return env.get(key)
            return None

        def get_env_required(self, key: str) -> str:
            if env and key in env:
                return env[key]
            raise KeyError(f"Missing environment variable: {key}")

    return MockConfig()
