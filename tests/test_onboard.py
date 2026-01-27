"""Tests for gptme onboard module."""

import os
from unittest.mock import patch

from gptme.cli.onboard import _detect_providers, _test_provider


class TestDetectProviders:
    """Test provider detection."""

    def test_detect_no_keys(self):
        """Test detection with no API keys set."""
        # Clear relevant env vars
        env_vars = [
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY",
            "GEMINI_API_KEY",
        ]
        with patch.dict(os.environ, {k: "" for k in env_vars}, clear=False):
            # Explicitly delete the keys
            for k in env_vars:
                os.environ.pop(k, None)

            providers = _detect_providers()
            # All should be not configured
            for provider, (has_key, _) in providers.items():
                if provider in ["openai", "anthropic", "openrouter", "gemini"]:
                    assert not has_key, f"{provider} should not be detected"

    def test_detect_with_keys(self):
        """Test detection with API keys set."""
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


class TestTestProvider:
    """Test provider connectivity testing."""

    def test_unknown_provider(self):
        """Test with unknown provider."""
        is_valid, error = _test_provider("unknown_provider")
        assert not is_valid
        assert "Unknown provider" in error

    def test_missing_key(self):
        """Test with missing API key."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            is_valid, error = _test_provider("openai")
            assert not is_valid
            assert "No API key found" in error
