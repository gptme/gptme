"""Tests for the gptme managed service provider."""

import json
import time
from pathlib import Path
from unittest.mock import patch

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


def test_get_base_url_from_env():
    """Should use GPTME_CLOUD_BASE_URL env var (with /v1 normalization)."""
    from gptme.llm.llm_gptme import get_base_url

    config = _mock_config(env={"GPTME_CLOUD_BASE_URL": "https://my-server.example.com"})
    with patch("gptme.llm.llm_gptme._load_token", return_value=None):
        assert get_base_url(config) == "https://my-server.example.com/v1"


def test_get_base_url_from_env_with_v1():
    """Should preserve /v1 suffix in env var."""
    from gptme.llm.llm_gptme import get_base_url

    config = _mock_config(
        env={"GPTME_CLOUD_BASE_URL": "https://my-server.example.com/v1"}
    )
    with patch("gptme.llm.llm_gptme._load_token", return_value=None):
        assert get_base_url(config) == "https://my-server.example.com/v1"


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
