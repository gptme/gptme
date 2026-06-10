"""Tests for the gptme managed service provider."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_gptme_in_providers():
    """gptme should be registered as a built-in OpenAI-compatible provider."""
    from gptme.llm.models import MODELS, PROVIDERS, PROVIDERS_OPENAI

    assert "gptme" in PROVIDERS
    assert "gptme" in PROVIDERS_OPENAI
    assert "gptme" in MODELS


def test_get_recommended_model():
    """gptme provider should have a recommended model."""
    from gptme.llm.models import get_recommended_model

    model = get_recommended_model("gptme")
    assert model == "claude-sonnet-4-6"


def test_get_provider_from_model():
    """gptme/model-name should resolve to gptme provider."""
    from gptme.llm import get_provider_from_model

    provider = get_provider_from_model("gptme/claude-sonnet-4-6")
    assert provider == "gptme"


def test_load_token_missing(tmp_path: Path):
    """Should return None when no token file exists."""
    from gptme.llm.llm_gptme import _load_token

    with patch(
        "gptme.llm.llm_gptme._get_token_path",
        return_value=tmp_path / "nonexistent.json",
    ):
        assert _load_token() is None


def test_load_token_valid(tmp_path: Path):
    """Should load a valid non-expired token."""
    from gptme.llm.llm_gptme import _load_token

    token_path = tmp_path / "gptme-cloud.json"
    token_data = {
        "access_token": "test-token-123",
        "expires_at": time.time() + 3600,
        "server_url": "https://fleet.gptme.ai",
    }
    token_path.write_text(json.dumps(token_data))

    with patch("gptme.llm.llm_gptme._get_token_path", return_value=token_path):
        result = _load_token()
        assert result is not None
        assert result["access_token"] == "test-token-123"


def test_load_token_expired(tmp_path: Path):
    """Should return None for expired tokens."""
    from gptme.llm.llm_gptme import _load_token

    token_path = tmp_path / "gptme-cloud.json"
    token_data = {
        "access_token": "expired-token",
        "expires_at": time.time() - 100,  # expired
        "server_url": "https://fleet.gptme.ai",
    }
    token_path.write_text(json.dumps(token_data))

    with patch("gptme.llm.llm_gptme._get_token_path", return_value=token_path):
        assert _load_token() is None


def test_load_token_zero_expires_at(tmp_path: Path):
    """Should return None when expires_at is 0 (missing expiration)."""
    from gptme.llm.llm_gptme import _load_token

    token_path = tmp_path / "gptme-cloud.json"
    token_data = {
        "access_token": "no-expiry-token",
        "expires_at": 0,
        "server_url": "https://fleet.gptme.ai",
    }
    token_path.write_text(json.dumps(token_data))

    with patch("gptme.llm.llm_gptme._get_token_path", return_value=token_path):
        assert _load_token() is None


def test_get_api_key_from_token(tmp_path: Path):
    """Should prefer Device Flow token over env var."""
    from gptme.llm.llm_gptme import get_api_key

    token_path = tmp_path / "gptme-cloud.json"
    token_data = {
        "access_token": "device-flow-token",
        "expires_at": time.time() + 3600,
    }
    token_path.write_text(json.dumps(token_data))

    config = _mock_config()
    with patch("gptme.llm.llm_gptme._get_token_path", return_value=token_path):
        assert get_api_key(config) == "device-flow-token"


def test_get_api_key_from_env():
    """Should fall back to GPTME_CLOUD_API_KEY env var."""
    from gptme.llm.llm_gptme import get_api_key

    config = _mock_config(env={"GPTME_CLOUD_API_KEY": "env-api-key"})
    with patch("gptme.llm.llm_gptme._load_token", return_value=None):
        assert get_api_key(config) == "env-api-key"


def test_get_api_key_missing():
    """Should raise KeyError when no auth available."""
    from gptme.llm.llm_gptme import get_api_key

    config = _mock_config()
    with (
        patch("gptme.llm.llm_gptme._load_token", return_value=None),
        pytest.raises(KeyError, match="gptme provider requires authentication"),
    ):
        get_api_key(config)


def test_get_base_url_default():
    """Should return default URL when no token or env."""
    from gptme.llm.llm_gptme import DEFAULT_BASE_URL, get_base_url

    config = _mock_config()
    with patch("gptme.llm.llm_gptme._load_token", return_value=None):
        assert get_base_url(config) == DEFAULT_BASE_URL


def test_get_base_url_from_token():
    """Should use server_url from token file."""
    from gptme.llm.llm_gptme import get_base_url

    token_data = {
        "access_token": "test",
        "server_url": "https://custom.gptme.ai",
    }
    config = _mock_config()
    with patch("gptme.llm.llm_gptme._load_token", return_value=token_data):
        assert get_base_url(config) == "https://custom.gptme.ai/v1"


def test_get_base_url_from_token_explicit():
    """Should prefer explicit base_url field over server_url fallback."""
    from gptme.llm.llm_gptme import get_base_url

    token_data = {
        "access_token": "test",
        "server_url": "https://fleet.gptme.ai",
        "base_url": "https://kpkxgnfpyntahyhckhgm.supabase.co/functions/v1/messages",
    }
    config = _mock_config()
    with patch("gptme.llm.llm_gptme._load_token", return_value=token_data):
        assert get_base_url(config) == (
            "https://kpkxgnfpyntahyhckhgm.supabase.co/functions/v1/messages"
        )


def test_get_base_url_from_env():
    """Should return GPTME_CLOUD_BASE_URL env var as-is (no /v1 normalization)."""
    from gptme.llm.llm_gptme import get_base_url

    config = _mock_config(
        env={"GPTME_CLOUD_BASE_URL": "https://my-server.example.com/v1"}
    )
    with patch("gptme.llm.llm_gptme._load_token", return_value=None):
        assert get_base_url(config) == "https://my-server.example.com/v1"


def test_get_base_url_from_env_supabase():
    """Should return Supabase function URL as-is from env var."""
    from gptme.llm.llm_gptme import _SUPABASE_FUNCTIONS_V1, get_base_url

    supabase_url = f"{_SUPABASE_FUNCTIONS_V1}/messages"
    config = _mock_config(env={"GPTME_CLOUD_BASE_URL": supabase_url})
    with patch("gptme.llm.llm_gptme._load_token", return_value=None):
        assert get_base_url(config) == supabase_url


def test_save_token(tmp_path: Path):
    """Should save token with restricted permissions."""
    from gptme.llm.llm_gptme import _save_token

    token_path = tmp_path / "auth" / "gptme-cloud.json"

    with patch("gptme.llm.llm_gptme._get_token_path", return_value=token_path):
        _save_token({"access_token": "saved-token", "expires_at": 999})

    assert token_path.exists()
    assert oct(token_path.stat().st_mode & 0o777) == "0o600"
    data = json.loads(token_path.read_text())
    assert data["access_token"] == "saved-token"


def test_auth_cli_has_login():
    """gptme-auth should have login, logout, and status subcommands."""
    from gptme.cli.auth import main

    command_names = [c.name for c in main.commands.values()]
    assert "login" in command_names
    assert "logout" in command_names
    assert "status" in command_names


def test_token_path_uses_url_hash():
    """Token path should include hash of service URL for multi-instance support."""
    from gptme.llm.llm_gptme import _get_token_path

    path1 = _get_token_path("https://fleet.gptme.ai")
    path2 = _get_token_path("https://custom.gptme.ai")
    assert path1 != path2
    assert "gptme-cloud-" in path1.name
    assert "gptme-cloud-" in path2.name


def test_load_token_legacy_fleet_migration(tmp_path: Path):
    """Should find old fleet.gptme.ai token when no new Supabase token exists.

    Covers the upgrade path: user authenticated before the Supabase URL migration.
    Their token is stored at the fleet.gptme.ai hash path; _load_token() must
    find it so get_base_url() can reconstruct the old-style URL via server_url.
    """
    import hashlib

    from gptme.llm.llm_gptme import _load_token

    legacy_url = "https://fleet.gptme.ai"
    url_hash = hashlib.sha256(legacy_url.encode()).hexdigest()[:12]
    legacy_token_path = tmp_path / f"gptme-cloud-{url_hash}.json"
    token_data = {
        "access_token": "legacy-token",
        "expires_at": time.time() + 3600,
        "server_url": legacy_url,
    }
    legacy_token_path.write_text(json.dumps(token_data))

    # Patch _TOKEN_DIR so _get_token_path resolves into tmp_path
    with patch("gptme.llm.llm_gptme._TOKEN_DIR", tmp_path):
        result = _load_token()  # no service_url → default path (Supabase) won't exist

    assert result is not None, (
        "Legacy fleet token should be found via migration fallback"
    )
    assert result["access_token"] == "legacy-token"
    assert result["server_url"] == legacy_url


def test_auth_status_shows_logged_in_for_legacy_fleet_token(tmp_path: Path):
    """auth_status should show 'Logged in' even for legacy fleet.gptme.ai tokens.

    Regression guard: auth_status was passing explicit Supabase URL to
    _load_token(), bypassing the migration fallback that finds old fleet.gptme.ai
    tokens during the URL-migration upgrade window.
    """
    import hashlib

    from click.testing import CliRunner

    from gptme.cli.auth import main as auth_main

    legacy_url = "https://fleet.gptme.ai"
    url_hash = hashlib.sha256(legacy_url.encode()).hexdigest()[:12]
    legacy_token_path = tmp_path / f"gptme-cloud-{url_hash}.json"
    legacy_token_path.write_text(
        json.dumps(
            {
                "access_token": "legacy-token",
                "expires_at": time.time() + 3600,
                "server_url": legacy_url,
                "sub": "user-legacy",
            }
        )
    )

    runner = CliRunner()
    with patch("gptme.llm.llm_gptme._TOKEN_DIR", tmp_path):
        result = runner.invoke(auth_main, ["status"])

    assert result.exit_code == 0, result.output
    assert "Logged in" in result.output, (
        "auth_status must find legacy fleet token via migration fallback; "
        f"got: {result.output!r}"
    )
    assert "Not logged in" not in result.output


def test_auth_logout_removes_legacy_fleet_token(tmp_path: Path):
    """auth_logout should remove legacy fleet.gptme.ai token when run against default URL.

    Regression guard: auth_logout was looking up the Supabase-URL-hashed path
    and reporting "No credentials stored" even though the legacy fleet token was
    still valid on disk (and still found by get_api_key via _load_token fallback).
    """
    import hashlib

    from click.testing import CliRunner

    from gptme.cli.auth import main as auth_main

    legacy_url = "https://fleet.gptme.ai"
    url_hash = hashlib.sha256(legacy_url.encode()).hexdigest()[:12]
    legacy_token_path = tmp_path / f"gptme-cloud-{url_hash}.json"
    legacy_token_path.write_text(
        json.dumps(
            {
                "access_token": "legacy-token",
                "expires_at": time.time() + 3600,
                "server_url": legacy_url,
                "sub": "user-legacy",
            }
        )
    )

    runner = CliRunner()
    with patch("gptme.llm.llm_gptme._TOKEN_DIR", tmp_path):
        result = runner.invoke(auth_main, ["logout"])

    assert result.exit_code == 0, result.output
    assert "Logged out" in result.output, (
        "auth_logout must remove legacy fleet token via migration fallback; "
        f"got: {result.output!r}"
    )
    assert not legacy_token_path.exists(), (
        "Legacy token file should be deleted after logout"
    )


def _mock_device_flow_responses(auth_uri: str = "https://gptme.ai/activate"):
    """Return (authorize_resp, token_resp) mocks for device_flow_authenticate."""
    auth_resp = MagicMock()
    auth_resp.status_code = 200
    auth_resp.json.return_value = {
        "device_code": "dc123",
        "user_code": "UC-123",
        "verification_uri": auth_uri,
        "interval": 1,
        "expires_in": 300,
    }
    tok_resp = MagicMock()
    tok_resp.status_code = 200
    tok_resp.json.return_value = {"access_token": "test-token", "expires_in": 3600}
    return auth_resp, tok_resp


def test_device_flow_authenticate_custom_server_no_base_url():
    """Custom server tokens must NOT store base_url; routing relies on server_url+/v1."""
    from gptme.llm.llm_gptme import device_flow_authenticate

    auth_resp, tok_resp = _mock_device_flow_responses()
    custom_url = "https://custom.example.com"

    with (
        patch("requests.post", side_effect=[auth_resp, tok_resp]),
        patch("gptme.llm.llm_gptme._save_token"),
    ):
        result = device_flow_authenticate(server_url=custom_url)

    assert "base_url" not in result, (
        "Custom server token must not hardcode Supabase base_url"
    )
    assert result["server_url"] == custom_url


def test_device_flow_authenticate_default_server_has_base_url():
    """Default Supabase tokens SHOULD store explicit base_url for edge fn routing."""
    from gptme.llm.llm_gptme import (
        DEFAULT_BASE_URL,
        DEFAULT_SERVICE_URL,
        device_flow_authenticate,
    )

    auth_resp, tok_resp = _mock_device_flow_responses()

    with (
        patch("requests.post", side_effect=[auth_resp, tok_resp]),
        patch("gptme.llm.llm_gptme._save_token"),
    ):
        result = device_flow_authenticate()  # uses DEFAULT_SERVICE_URL

    assert result.get("base_url") == DEFAULT_BASE_URL
    assert result["server_url"] == DEFAULT_SERVICE_URL


def test_get_models_url_custom_token():
    """Custom-server tokens (no base_url) should use server_url + /v1 for model listing."""
    from gptme.llm.llm_gptme import get_models_url

    token_data = {
        "access_token": "test",
        "server_url": "https://custom.example.com",
        "expires_at": 9999999999,
    }
    config = _mock_config()
    with patch("gptme.llm.llm_gptme._load_token", return_value=token_data):
        assert get_models_url(config) == "https://custom.example.com/v1"


def test_get_models_url_env_models_url():
    """GPTME_CLOUD_MODELS_URL should take precedence over GPTME_CLOUD_BASE_URL."""
    from gptme.llm.llm_gptme import get_models_url

    config = _mock_config(
        env={
            "GPTME_CLOUD_MODELS_URL": "https://custom.example.com/v1",
            "GPTME_CLOUD_BASE_URL": "https://other.example.com/v1",
        }
    )
    with patch("gptme.llm.llm_gptme._load_token", return_value=None):
        assert get_models_url(config) == "https://custom.example.com/v1"


def test_get_models_url_env_base_url_custom_server():
    """GPTME_CLOUD_BASE_URL for a custom (non-Supabase) server should be used for models."""
    from gptme.llm.llm_gptme import get_models_url

    config = _mock_config(env={"GPTME_CLOUD_BASE_URL": "https://custom.example.com/v1"})
    with patch("gptme.llm.llm_gptme._load_token", return_value=None):
        assert get_models_url(config) == "https://custom.example.com/v1"


def test_get_models_url_env_base_url_supabase_uses_default():
    """GPTME_CLOUD_BASE_URL pointing at Supabase should NOT be used for models (wrong path)."""
    from gptme.llm.llm_gptme import (
        _SUPABASE_FUNCTIONS_V1,
        DEFAULT_MODELS_BASE_URL,
        get_models_url,
    )

    supabase_url = f"{_SUPABASE_FUNCTIONS_V1}/messages"
    config = _mock_config(env={"GPTME_CLOUD_BASE_URL": supabase_url})
    with patch("gptme.llm.llm_gptme._load_token", return_value=None):
        assert get_models_url(config) == DEFAULT_MODELS_BASE_URL


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
