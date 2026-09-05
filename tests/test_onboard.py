"""Tests for gptme onboard module."""

import os
from unittest.mock import MagicMock, patch

from gptme.cli.onboard import (
    _detect_providers,
    _get_default_model,
    _show_provider_status,
    _test_provider,
)


def _mock_empty_config():
    """Return a mock config with no API keys or model configured."""
    mock_config = MagicMock()
    mock_config.get_env.return_value = None
    mock_config.chat = None
    return mock_config


class TestDetectProviders:
    """Test provider detection."""

    def test_detect_no_keys(self):
        """Test detection with no API keys set (env or config)."""
        # Clear relevant env vars
        env_vars = [
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY",
            "GEMINI_API_KEY",
        ]
        with patch.dict(os.environ, dict.fromkeys(env_vars, ""), clear=False):
            # Explicitly delete the keys
            for k in env_vars:
                os.environ.pop(k, None)

            # Mock config to return no keys either
            with patch("gptme.config.get_config", return_value=_mock_empty_config()):
                providers = _detect_providers()
                # All should be not configured
                for provider, (has_key, _) in providers.items():
                    if provider in ["openai", "anthropic", "openrouter", "gemini"]:
                        assert not has_key, f"{provider} should not be detected"

    def test_detect_with_env_keys(self):
        """Test detection with API keys set in environment."""
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "sk-test1234567890abcdef"},
            clear=False,
        ):
            providers = _detect_providers()
            has_key, preview = providers.get("openai", (False, None))
            assert has_key, "OpenAI should be detected"
            assert preview is not None
            assert "sk-t" in preview  # First 4 chars
            assert "cdef" in preview  # Last 4 chars

    def test_detect_with_config_keys(self):
        """Test detection finds API keys in config file."""
        env_vars = ["ANTHROPIC_API_KEY"]
        with patch.dict(os.environ, dict.fromkeys(env_vars, ""), clear=False):
            for k in env_vars:
                os.environ.pop(k, None)

            # Mock config to return an API key
            mock_config = _mock_empty_config()
            mock_config.get_env.side_effect = lambda key: (
                "sk-ant-test1234567890" if key == "ANTHROPIC_API_KEY" else None
            )
            with patch("gptme.config.get_config", return_value=mock_config):
                providers = _detect_providers()
                has_key, preview = providers.get("anthropic", (False, None))
                assert has_key, "Anthropic should be detected from config"
                assert preview is not None
                assert "(config)" in preview

    def test_detect_with_config_model(self):
        """Test detection finds provider from configured model."""
        env_vars = ["OPENAI_API_KEY"]
        with patch.dict(os.environ, dict.fromkeys(env_vars, ""), clear=False):
            for k in env_vars:
                os.environ.pop(k, None)

            # Mock config with a model set but no API key
            mock_config = _mock_empty_config()
            mock_config.get_env.return_value = None
            mock_chat = MagicMock()
            mock_chat.model = "openai/gpt-4o"
            mock_config.chat = mock_chat
            with patch("gptme.config.get_config", return_value=mock_config):
                providers = _detect_providers()
                has_key, preview = providers.get("openai", (False, None))
                assert has_key, "OpenAI should be detected from config model"
                assert preview is not None
                assert "model:" in preview

    @patch(
        "gptme.cli.onboard.list_available_providers",
        return_value=[("openai-subscription", "oauth")],
    )
    def test_detect_with_oauth(self, _mock_providers):
        """Test detection finds subscription providers with OAuth credentials."""
        with patch("gptme.config.get_config", return_value=_mock_empty_config()):
            providers = _detect_providers()

        assert providers["openai-subscription"] == (True, "oauth")

    @patch(
        "gptme.cli.onboard.list_available_providers",
        side_effect=ValueError("malformed credentials"),
    )
    def test_detect_preserves_env_fallback_when_oauth_lookup_fails(
        self, _mock_providers
    ):
        """Credential lookup failures do not discard environment detection."""
        with (
            patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "sk-test1234567890abcdef"},
                clear=True,
            ),
            patch("gptme.config.get_config", return_value=_mock_empty_config()),
        ):
            providers = _detect_providers()

        assert providers["openai"][0]

    @patch(
        "gptme.cli.onboard.list_available_providers",
        side_effect=RuntimeError("token file corrupted"),
    )
    def test_oauth_lookup_failure_logs_warning(self, _mock_providers, caplog):
        """OAuth credential lookup failures emit a warning rather than silently passing."""
        import logging

        with (
            caplog.at_level(logging.WARNING, logger="gptme.cli.onboard"),
            patch("gptme.config.get_config", return_value=_mock_empty_config()),
        ):
            _detect_providers()

        messages = " ".join(r.message for r in caplog.records)
        assert "OAuth credential check failed" in messages
        assert "gptme auth" in messages


class TestTestProvider:
    """Test provider connectivity testing."""

    def test_unknown_provider(self):
        """Test with unknown provider."""
        is_valid, error = _test_provider("unknown_provider")
        assert not is_valid
        assert "Unknown provider" in error

    def test_missing_key(self):
        """Test with missing API key (env and config)."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            # Mock config to also have no key
            with patch("gptme.config.get_config", return_value=_mock_empty_config()):
                is_valid, error = _test_provider("openai")
                assert not is_valid
                assert "No API key found" in error

    @patch(
        "gptme.cli.onboard.list_available_providers",
        return_value=[("openai-subscription", "oauth")],
    )
    def test_oauth_credentials_found(self, _mock_providers):
        """Subscription credentials are accepted without an API-key validation call."""
        is_valid, message = _test_provider("openai-subscription")

        assert is_valid
        assert "OAuth credentials found" in message

    @patch("gptme.cli.onboard.list_available_providers", return_value=[])
    def test_oauth_credentials_missing(self, _mock_providers):
        """Missing subscription credentials give the provider-specific auth command."""
        is_valid, message = _test_provider("openai-subscription")

        assert not is_valid
        assert message == "Not authenticated (run gptme auth openai-subscription)"

    @patch(
        "gptme.cli.onboard.list_available_providers",
        side_effect=ValueError("malformed credentials"),
    )
    def test_oauth_lookup_failure_in_test_provider(self, _mock_providers):
        """Credential lookup failure in _test_provider returns (False, message)."""
        is_valid, message = _test_provider("openai-subscription")

        assert not is_valid
        assert "Not authenticated" in message


class TestShowProviderStatus:
    """Test the provider status table."""

    def test_shows_configured_oauth_provider_not_in_builtins(self, capsys):
        """An OAuth provider detected as configured is shown, even though it's
        not in the builtin PROVIDERS list (regression: it was previously
        omitted, so --check could report "1 provider(s) configured" while the
        visible table showed everything as not configured)."""
        providers: dict[str, tuple[bool, str | None]] = {
            "openai-subscription": (True, "oauth")
        }

        _show_provider_status(providers)

        out = capsys.readouterr().out
        assert "openai-subscription" in out
        assert "Configured" in out


class TestGetDefaultModel:
    """Test _get_default_model returns a valid full model string."""

    def test_known_provider_returns_recommended(self):
        """Providers with a builtin recommendation return provider/model."""
        result = _get_default_model("openai")
        assert result.startswith("openai/"), result
        assert "/" in result

    def test_grok_subscription_returns_valid_model(self):
        """grok-subscription has no builtin recommended model; must fall back to
        the first model in the static MODELS dict (e.g. grok-4.6) rather than
        returning the bare provider name, which fails at startup."""
        result = _get_default_model("grok-subscription")
        assert result.startswith("grok-subscription/"), result
        assert result != "grok-subscription", (
            "_get_default_model must never return the bare provider name"
        )

    def test_openai_subscription_returns_valid_model(self):
        """openai-subscription should similarly return a qualified default."""
        result = _get_default_model("openai-subscription")
        assert result.startswith("openai-subscription/"), result
        assert result != "openai-subscription"

    def test_unknown_provider_returns_empty_not_bare_name(self):
        """A provider with no entry in MODELS must return '' not the bare name.

        The bare provider name is not a valid model string (runtime requires
        ``provider/model``).  The caller is responsible for prompting the user.
        """
        result = _get_default_model("unknown-future-provider")
        assert result == "", (
            "_get_default_model must return '' for unknown providers, "
            f"not the bare name; got {result!r}"
        )
