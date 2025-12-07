"""Tests for API key validation."""

from unittest.mock import Mock, patch

from gptme.llm.validate import (
    PROVIDER_DOCS,
    _validate_anthropic,
    _validate_openai,
    _validate_openrouter,
    validate_api_key,
)


class TestValidateApiKey:
    """Tests for the main validate_api_key function."""

    def test_unknown_provider_skips_validation(self):
        """Unknown providers should skip validation."""
        is_valid, error = validate_api_key("some-key", "unknown")  # type: ignore
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


class TestProviderDocs:
    """Tests for provider documentation URLs."""

    def test_all_major_providers_have_docs(self):
        """All major providers should have documentation URLs."""
        expected_providers = ["openai", "anthropic", "openrouter", "google"]
        for provider in expected_providers:
            assert provider in PROVIDER_DOCS
            assert PROVIDER_DOCS[provider].startswith("https://")
