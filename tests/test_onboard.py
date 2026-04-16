"""Tests for gptme onboard module."""

import os
from unittest.mock import MagicMock, patch

from gptme.cli.onboard import _detect_providers, _test_provider


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
