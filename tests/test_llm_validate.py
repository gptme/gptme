"""Tests for API key validation."""

from unittest.mock import Mock, patch

import pytest

from gptme.llm.validate import (
    PROVIDER_DOCS,
    VALIDATION_PROVIDER_ERROR_PREFIX,
    _validate_anthropic,
    _validate_google,
    _validate_openai,
    _validate_openai_compatible,
    _validate_openrouter,
    validate_api_key,
)


class TestValidateApiKey:
    """Tests for the main validate_api_key function."""

    def test_unknown_provider_skips_validation(self):
        """Unknown providers should skip validation."""
        is_valid, error = validate_api_key("some-key", "unknown")
        assert is_valid
        assert error == ""

    @patch("gptme.llm.validate._validate_openai")
    def test_openai_provider_calls_correct_validator(self, mock_validate):
        """OpenAI keys should use the OpenAI validator."""
        mock_validate.return_value = (True, "")
        validate_api_key("sk-test", "openai")
        mock_validate.assert_called_once_with("sk-test", 10)

    @patch("gptme.llm.validate._validate_anthropic")
    def test_anthropic_provider_calls_correct_validator(self, mock_validate):
        """Anthropic keys should use the Anthropic validator."""
        mock_validate.return_value = (True, "")
        validate_api_key("sk-ant-test", "anthropic")
        mock_validate.assert_called_once_with("sk-ant-test", 10)

    @patch("gptme.llm.validate._validate_openrouter")
    def test_openrouter_provider_calls_correct_validator(self, mock_validate):
        """OpenRouter keys should use the OpenRouter validator."""
        mock_validate.return_value = (True, "")
        validate_api_key("sk-or-test", "openrouter")
        mock_validate.assert_called_once_with("sk-or-test", 10)

    @patch("gptme.llm.validate._validate_openai_compatible")
    def test_requesty_provider_calls_compatible_validator(self, mock_validate):
        mock_validate.return_value = (True, "")
        validate_api_key("req-test", "requesty")
        mock_validate.assert_called_once_with(
            "req-test", 10, "https://router.requesty.ai/v1"
        )

    @patch("gptme.llm.validate._validate_openai_compatible")
    def test_moonshot_provider_calls_compatible_validator(self, mock_validate):
        mock_validate.return_value = (True, "")
        validate_api_key("moonshot-test", "moonshot")
        mock_validate.assert_called_once_with(
            "moonshot-test", 10, "https://api.moonshot.ai/v1"
        )


class TestValidateOpenAI:
    """Tests for OpenAI API key validation."""

    @patch("gptme.llm.validate.requests.get")
    def test_valid_key_returns_true(self, mock_get):
        """Valid API key should return (True, '')."""
        mock_get.return_value = Mock(status_code=200)
        is_valid, error = _validate_openai("sk-valid-key", 10)
        assert is_valid
        assert error == ""

    @patch("gptme.llm.validate.requests.get")
    def test_invalid_key_returns_false(self, mock_get):
        """Invalid API key should return (False, error_message)."""
        mock_get.return_value = Mock(status_code=401)
        is_valid, error = _validate_openai("sk-invalid-key", 10)
        assert not is_valid
        assert "Invalid API key" in error

    @patch("gptme.llm.validate.requests.get")
    def test_rate_limited_returns_true(self, mock_get):
        """Rate limited response means key is valid."""
        mock_get.return_value = Mock(status_code=429)
        is_valid, error = _validate_openai("sk-valid-key", 10)
        assert is_valid
        assert error == ""


class TestValidateGoogle:
    """Tests for Google AI (Gemini) API key validation."""

    @patch("gptme.llm.validate.requests.get")
    def test_valid_key_returns_true(self, mock_get):
        """Valid API key should return (True, '')."""
        mock_get.return_value = Mock(status_code=200)
        is_valid, error = _validate_google("valid-gemini-key", 10)
        assert is_valid
        assert error == ""

    @patch("gptme.llm.validate.requests.get")
    def test_rate_limited_returns_true(self, mock_get):
        """Rate-limited key is valid — 429 should not be treated as an auth failure."""
        mock_get.return_value = Mock(status_code=429)
        is_valid, error = _validate_google("valid-gemini-key", 10)
        assert is_valid
        assert error == ""

    @patch("gptme.llm.validate.requests.get")
    def test_provider_unavailable_returns_502_prefix(self, mock_get):
        """5xx responses should return provider-unavailable prefix, not auth failure."""
        mock_get.return_value = Mock(status_code=500)
        is_valid, error = _validate_google("some-key", 10)
        assert not is_valid
        assert VALIDATION_PROVIDER_ERROR_PREFIX in error


class TestValidateAnthropic:
    """Tests for Anthropic API key validation."""

    @patch("gptme.llm.validate.requests.post")
    def test_valid_key_returns_true(self, mock_post):
        """Valid API key should return (True, '')."""
        mock_post.return_value = Mock(status_code=200)
        is_valid, error = _validate_anthropic("sk-ant-valid-key", 10)
        assert is_valid
        assert error == ""

    @patch("gptme.llm.validate.requests.post")
    def test_invalid_key_returns_false(self, mock_post):
        """Invalid API key should return (False, error_message)."""
        mock_post.return_value = Mock(status_code=401)
        is_valid, error = _validate_anthropic("sk-ant-invalid-key", 10)
        assert not is_valid
        assert "Invalid API key" in error

    @patch("gptme.llm.validate.requests.post")
    def test_bad_request_with_valid_key(self, mock_post):
        """Bad request (empty messages) with valid key should return True."""
        mock_post.return_value = Mock(
            status_code=400,
            json=Mock(
                return_value={"error": {"message": "messages must not be empty"}}
            ),
        )
        is_valid, error = _validate_anthropic("sk-ant-valid-key", 10)
        assert is_valid
        assert error == ""

    @patch("gptme.llm.validate.requests.post")
    def test_bad_request_with_invalid_key_returns_false(self, mock_post):
        """A 400 response that explicitly rejects the key should fail validation."""
        mock_post.return_value = Mock(
            status_code=400,
            json=Mock(
                return_value={
                    "error": {
                        "type": "invalid_api_key",
                        "message": "Invalid API key",
                    }
                }
            ),
        )

        is_valid, error = _validate_anthropic("sk-ant-invalid-key", 10)

        assert not is_valid
        assert "Invalid API key" in error

    @patch("gptme.llm.validate.requests.post")
    def test_authentication_error_type_returns_false(self, mock_post):
        """A 400 with type=authentication_error should be treated as invalid key."""
        mock_post.return_value = Mock(
            status_code=400,
            json=Mock(
                return_value={
                    "error": {
                        "type": "authentication_error",
                        "message": "Invalid key",
                    }
                }
            ),
        )
        is_valid, error = _validate_anthropic("sk-ant-invalid-key", 10)
        assert not is_valid
        assert "Invalid API key" in error

    @patch("gptme.llm.validate.requests.post")
    def test_permission_error_type_returns_true_with_warning(self, mock_post):
        """A 400 with type=permission_error means key is valid but lacks probe-model access."""
        mock_post.return_value = Mock(
            status_code=400,
            json=Mock(
                return_value={
                    "error": {
                        "type": "permission_error",
                        "message": "Your API key does not have access to this model.",
                    }
                }
            ),
        )
        is_valid, error = _validate_anthropic("sk-ant-restricted-key", 10)
        assert is_valid, "permission_error means valid key with restricted model access"
        assert error != "", "should surface a warning about restricted access"

    @patch("gptme.llm.validate.requests.post")
    def test_permission_error_with_auth_words_in_message_returns_true(self, mock_post):
        """permission_error type wins even if the message body contains auth-related words.

        Regression guard for the ordering bug where auth-substring check ran before
        the permission_error check: a message like "Authentication required to access
        model" would have triggered the auth branch and returned False (invalid key)
        despite the type being permission_error.
        """
        mock_post.return_value = Mock(
            status_code=400,
            json=Mock(
                return_value={
                    "error": {
                        "type": "permission_error",
                        "message": "Authentication required to access claude-haiku-4-5.",
                    }
                }
            ),
        )
        is_valid, error = _validate_anthropic("sk-ant-restricted-key", 10)
        assert is_valid, (
            "permission_error type must win over auth substrings in message"
        )
        assert error != "", "should surface a warning about restricted access"

    @patch("gptme.llm.validate.requests.post")
    def test_quota_exhausted_returns_warning(self, mock_post):
        """Quota-exhausted 400 response should return (True, warning_msg) not (True, '')."""
        quota_msg = "You have reached your specified API usage limits. You will regain access on 2026-05-01 at 00:00 UTC."
        mock_post.return_value = Mock(
            status_code=400,
            json=Mock(
                return_value={
                    "error": {"type": "invalid_request_error", "message": quota_msg}
                }
            ),
        )
        is_valid, error = _validate_anthropic("sk-ant-valid-key", 10)
        assert is_valid  # Key itself is valid (will work after reset)
        assert "quota" in error.lower() or "usage limits" in error.lower()


class TestValidateOpenRouter:
    """Tests for OpenRouter API key validation."""

    @patch("gptme.llm.validate.requests.get")
    def test_valid_key_returns_true(self, mock_get):
        """Valid API key should return (True, '')."""
        mock_get.return_value = Mock(status_code=200)
        is_valid, error = _validate_openrouter("sk-or-valid-key", 10)
        assert is_valid
        assert error == ""

    @patch("gptme.llm.validate.requests.get")
    def test_invalid_key_returns_false(self, mock_get):
        """Invalid API key should return (False, error_message)."""
        mock_get.return_value = Mock(status_code=401)
        is_valid, error = _validate_openrouter("sk-or-invalid-key", 10)
        assert not is_valid
        assert "Invalid API key" in error


class TestValidateOpenAICompatible:
    """Tests for OpenAI-compatible provider key validation."""

    @pytest.mark.parametrize(
        ("status_code", "expected_valid", "expected_message"),
        [
            (200, True, ""),
            (401, False, "Invalid API key"),
            (403, False, "forbidden"),
            (429, True, ""),
            # 5xx → provider-unavailable error, not a credential failure
            (500, False, VALIDATION_PROVIDER_ERROR_PREFIX),
            (503, False, VALIDATION_PROVIDER_ERROR_PREFIX),
        ],
    )
    @patch("gptme.llm.validate.requests.get")
    def test_status_classification(
        self, mock_get, status_code, expected_valid, expected_message
    ):
        mock_get.return_value = Mock(status_code=status_code)
        is_valid, error = _validate_openai_compatible(
            "test-key", 10, "https://example.com/v1/"
        )

        assert is_valid is expected_valid
        assert expected_message.lower() in error.lower()
        mock_get.assert_called_once_with(
            "https://example.com/v1/models",
            headers={"Authorization": "Bearer test-key"},
            timeout=10,
        )


class TestUnvalidatableProviders:
    """Tests for providers where live validation is not possible."""

    def test_azure_returns_warning_not_silent(self):
        """Azure validation skip should surface a warning, not silently pass."""
        is_valid, msg = validate_api_key("some-azure-key", "azure")
        assert is_valid
        assert msg != ""
        assert "azure" in msg.lower() or "validation" in msg.lower()

    def test_nvidia_returns_warning_not_silent(self):
        """NVIDIA validation skip should surface a warning, not silently pass."""
        is_valid, msg = validate_api_key("nvapi-some-key", "nvidia")
        assert is_valid
        assert msg != ""

    def test_local_passes_silently(self):
        """Local providers use placeholder keys and need no warning."""
        is_valid, msg = validate_api_key("ignore", "local")
        assert is_valid
        assert msg == ""


class TestProviderDocs:
    """Tests for provider documentation URLs."""

    def test_all_major_providers_have_docs(self):
        """All major providers should have documentation URLs."""
        expected_providers = [
            "openai",
            "anthropic",
            "openrouter",
            "gemini",
            "google",
            "groq",
            "deepseek",
            "xai",
            "azure",
            "nvidia",
            "requesty",
            "moonshot",
            "local",
        ]
        for provider in expected_providers:
            assert provider in PROVIDER_DOCS
            assert PROVIDER_DOCS[provider].startswith("https://")
