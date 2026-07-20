"""Tests for the grok subscription provider."""

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.llm.llm_grok_subscription import (
    GROK_AUTH_KEY,
    OAUTH_CLIENT_ID,
    OAUTH_ISSUER,
    SubscriptionAuth,
    _load_grok_cli_tokens,
    _load_stored_tokens,
    _parse_expires_at,
    _refresh_access_token,
    _save_tokens,
    get_auth,
    init,
)


def _make_auth(expired: bool = False) -> SubscriptionAuth:
    """Create a test SubscriptionAuth object."""
    return SubscriptionAuth(
        access_token="test-access-token",
        refresh_token="test-refresh-token",
        expires_at=1.0 if expired else 9_999_999_999.0,
    )


def _grok_cli_auth_file(tmp_path: Path, expired: bool = False) -> dict:
    """Build a grok CLI auth.json payload."""
    exp = (
        "2020-01-01T00:00:00.000000000Z"
        if expired
        else "2099-01-01T00:00:00.000000000Z"
    )
    return {
        GROK_AUTH_KEY: {
            "key": "cli-access-token",
            "auth_mode": "oidc",
            "refresh_token": "cli-refresh-token",
            "expires_at": exp,
            "oidc_issuer": OAUTH_ISSUER,
            "oidc_client_id": OAUTH_CLIENT_ID,
        }
    }


# ── _parse_expires_at ────────────────────────────────────────────────────────


def test_parse_expires_at_iso_z():
    # 2026-07-20T12:00:00Z = 1784548800
    ts = _parse_expires_at("2026-07-20T12:00:00.000000000Z")
    assert abs(ts - 1784548800.0) < 2


def test_parse_expires_at_invalid_falls_back():
    # Invalid expiry should return 0 (already expired) to force immediate refresh
    ts = _parse_expires_at("not-a-date")
    assert ts == 0.0


# ── _load_grok_cli_tokens ────────────────────────────────────────────────────


def test_load_grok_cli_tokens_no_file(tmp_path):
    with patch(
        "gptme.llm.llm_grok_subscription._get_grok_cli_auth_path",
        return_value=tmp_path / "nonexistent.json",
    ):
        assert _load_grok_cli_tokens() is None


def test_load_grok_cli_tokens_valid(tmp_path):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps(_grok_cli_auth_file(tmp_path)))
    with patch(
        "gptme.llm.llm_grok_subscription._get_grok_cli_auth_path",
        return_value=auth_file,
    ):
        auth = _load_grok_cli_tokens()
    assert auth is not None
    assert auth.access_token == "cli-access-token"
    assert auth.refresh_token == "cli-refresh-token"
    assert auth.expires_at > time.time()


def test_load_grok_cli_tokens_fallback_any_auth_x_ai_key(tmp_path):
    """Any key containing 'auth.x.ai' is accepted as a fallback."""
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "https://auth.x.ai::different-client-id": {
                    "key": "fallback-token",
                    "refresh_token": "fallback-refresh",
                    "expires_at": "2099-01-01T00:00:00.000000000Z",
                }
            }
        )
    )
    with patch(
        "gptme.llm.llm_grok_subscription._get_grok_cli_auth_path",
        return_value=auth_file,
    ):
        auth = _load_grok_cli_tokens()
    assert auth is not None
    assert auth.access_token == "fallback-token"


# ── _save_tokens / _load_stored_tokens ──────────────────────────────────────


def test_save_and_load_stored_tokens(tmp_path):
    token_path = tmp_path / "grok_subscription.json"
    auth = _make_auth()
    with patch(
        "gptme.llm.llm_grok_subscription._get_token_storage_path",
        return_value=token_path,
    ):
        _save_tokens(auth)
        loaded = _load_stored_tokens()

    assert loaded is not None
    assert loaded.access_token == auth.access_token
    assert loaded.refresh_token == auth.refresh_token
    assert abs(loaded.expires_at - auth.expires_at) < 1


def test_load_stored_tokens_missing(tmp_path):
    with patch(
        "gptme.llm.llm_grok_subscription._get_token_storage_path",
        return_value=tmp_path / "nonexistent.json",
    ):
        assert _load_stored_tokens() is None


# ── _refresh_access_token ────────────────────────────────────────────────────


def test_refresh_access_token_success(tmp_path):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "new-access-token",
        "refresh_token": "new-refresh-token",
        "expires_in": 21600,
    }
    token_path = tmp_path / "grok_subscription.json"
    with (
        patch(
            "gptme.llm.llm_grok_subscription.requests.post", return_value=mock_response
        ),
        patch(
            "gptme.llm.llm_grok_subscription._get_token_storage_path",
            return_value=token_path,
        ),
    ):
        auth = _refresh_access_token("old-refresh-token")

    assert auth.access_token == "new-access-token"
    assert auth.refresh_token == "new-refresh-token"
    assert auth.expires_at > time.time()


def test_refresh_access_token_http_error():
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"
    with (
        patch(
            "gptme.llm.llm_grok_subscription.requests.post", return_value=mock_response
        ),
        pytest.raises(ValueError, match="Token refresh failed: 401"),
    ):
        _refresh_access_token("bad-refresh-token")


# ── get_auth ─────────────────────────────────────────────────────────────────


def test_get_auth_uses_in_memory_cache():
    import gptme.llm.llm_grok_subscription as mod

    auth = _make_auth()
    mod._auth = auth
    try:
        result = get_auth()
    finally:
        mod._auth = None
    assert result is auth


def test_get_auth_loads_cli_tokens_when_valid(tmp_path):
    import gptme.llm.llm_grok_subscription as mod

    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps(_grok_cli_auth_file(tmp_path)))
    mod._auth = None
    try:
        with (
            patch(
                "gptme.llm.llm_grok_subscription._get_grok_cli_auth_path",
                return_value=auth_file,
            ),
            patch(
                "gptme.llm.llm_grok_subscription._get_token_storage_path",
                return_value=tmp_path / "nonexistent.json",
            ),
        ):
            result = get_auth()
    finally:
        mod._auth = None
    assert result.access_token == "cli-access-token"


def test_get_auth_raises_when_no_tokens(tmp_path):
    import gptme.llm.llm_grok_subscription as mod

    mod._auth = None
    with (
        patch(
            "gptme.llm.llm_grok_subscription._get_grok_cli_auth_path",
            return_value=tmp_path / "nonexistent.json",
        ),
        patch(
            "gptme.llm.llm_grok_subscription._get_token_storage_path",
            return_value=tmp_path / "nonexistent.json",
        ),
        pytest.raises(ValueError, match="not authenticated"),
    ):
        get_auth()


def test_get_auth_refreshes_expired_cli_token(tmp_path):
    import gptme.llm.llm_grok_subscription as mod

    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps(_grok_cli_auth_file(tmp_path, expired=True)))
    token_path = tmp_path / "grok_subscription.json"
    mod._auth = None

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "refreshed-token",
        "refresh_token": "new-refresh",
        "expires_in": 21600,
    }

    try:
        with (
            patch(
                "gptme.llm.llm_grok_subscription._get_grok_cli_auth_path",
                return_value=auth_file,
            ),
            patch(
                "gptme.llm.llm_grok_subscription._get_token_storage_path",
                return_value=token_path,
            ),
            patch(
                "gptme.llm.llm_grok_subscription.requests.post",
                return_value=mock_response,
            ),
        ):
            result = get_auth()
    finally:
        mod._auth = None

    assert result.access_token == "refreshed-token"


# ── init ─────────────────────────────────────────────────────────────────────


def test_init_registers_openai_client(tmp_path):
    """init() must register an OpenAI client for grok-subscription."""
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps(_grok_cli_auth_file(tmp_path)))

    import gptme.llm.llm_grok_subscription as mod

    mod._auth = None

    class _FakeConfig:
        def get_env(self, k):
            return None

    with (
        patch(
            "gptme.llm.llm_grok_subscription._get_grok_cli_auth_path",
            return_value=auth_file,
        ),
        patch(
            "gptme.llm.llm_grok_subscription._get_token_storage_path",
            return_value=tmp_path / "nonexistent.json",
        ),
        patch("gptme.llm.llm_openai._init_openai_client") as mock_init,
    ):
        result = init(_FakeConfig())

    assert result is True
    mock_init.assert_called_once()
    call_args = mock_init.call_args
    assert call_args.args[0] == "grok-subscription"
    assert call_args.kwargs["api_key"] == "cli-access-token"
    assert "api.x.ai" in call_args.kwargs["base_url"]

    mod._auth = None


def test_init_returns_true_without_tokens(tmp_path):
    """init() returns True even if no tokens are available (provider always reachable)."""
    import gptme.llm.llm_grok_subscription as mod

    mod._auth = None

    class _FakeConfig:
        def get_env(self, k):
            return None

    with (
        patch(
            "gptme.llm.llm_grok_subscription._get_grok_cli_auth_path",
            return_value=tmp_path / "nonexistent.json",
        ),
        patch(
            "gptme.llm.llm_grok_subscription._get_token_storage_path",
            return_value=tmp_path / "nonexistent.json",
        ),
    ):
        result = init(_FakeConfig())

    assert result is True
    mod._auth = None


# ── model metadata ───────────────────────────────────────────────────────────


def test_grok_subscription_model_in_registry():
    from gptme.llm.models import get_model

    meta = get_model("grok-subscription/grok-4.5")
    assert meta.provider == "grok-subscription"
    assert meta.model == "grok-4.5"
    assert meta.context == 500_000
    assert meta.supports_reasoning is True


def test_grok_subscription_in_providers_openai():
    from gptme.llm.models.types import PROVIDERS_OPENAI

    assert "grok-subscription" in PROVIDERS_OPENAI


def test_grok_subscription_in_builtin_providers():
    from gptme.llm.models.types import PROVIDERS

    assert "grok-subscription" in PROVIDERS


def test_grok_subscription_in_oauth_providers():
    from gptme.llm.validate import OAUTH_PROVIDERS

    assert "grok-subscription" in OAUTH_PROVIDERS


def test_grok_subscription_default_model():
    from gptme.llm import PROVIDER_DEFAULT_MODELS

    assert (
        PROVIDER_DEFAULT_MODELS.get("grok-subscription") == "grok-subscription/grok-4.5"
    )


def test_grok_subscription_appears_in_available_providers(tmp_path):
    """list_available_providers() lists grok-subscription when grok CLI auth file exists."""
    from gptme.llm import list_available_providers

    grok_cli_file = tmp_path / ".grok" / "auth.json"
    grok_cli_file.parent.mkdir()
    grok_cli_file.write_text(json.dumps(_grok_cli_auth_file(tmp_path)))

    # Path.home() re-reads HOME each call, so patching HOME is sufficient
    with patch.dict(
        os.environ,
        {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "config")},
    ):
        providers = list_available_providers()

    provider_names = [p for p, _ in providers]
    assert "grok-subscription" in provider_names
