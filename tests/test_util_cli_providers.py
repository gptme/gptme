"""Tests for the providers-related gptme-util CLI commands."""

from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from gptme.cli.util import main
from gptme.config import ProviderConfig


@pytest.fixture
def mock_config(mocker):
    """Mock configuration with provider settings."""
    config = Mock()
    config.user.providers = []
    mocker.patch("gptme.cli.util.get_config", return_value=config)
    return config


@pytest.fixture
def make_provider():
    """Factory for creating ProviderConfig instances."""

    def _make(
        name="test-provider",
        base_url="http://localhost:8000/v1",
        api_key=None,
        api_key_env=None,
        default_model=None,
    ):
        return ProviderConfig(
            name=name,
            base_url=base_url,
            api_key=api_key,
            api_key_env=api_key_env,
            default_model=default_model,
        )

    return _make


class TestProvidersList:
    """Tests for 'providers list' command."""

    def test_no_providers(self, mock_config):
        """Test when no providers are configured."""
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list"])
        assert result.exit_code == 0
        assert "No custom providers configured" in result.output
        assert "gptme.toml" in result.output

    def test_list_single_provider(self, mock_config, make_provider):
        """Test listing a single provider."""
        mock_config.user.providers = [
            make_provider(
                name="vllm-local",
                base_url="http://localhost:8000/v1",
                default_model="llama-3",
            )
        ]
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list"])
        assert result.exit_code == 0
        assert "vllm-local" in result.output
        assert "http://localhost:8000/v1" in result.output
        assert "llama-3" in result.output

    def test_list_provider_with_api_key_env(self, mock_config, make_provider):
        """Test listing shows API key env var source."""
        mock_config.user.providers = [make_provider(api_key_env="MY_SECRET_KEY")]
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list"])
        assert result.exit_code == 0
        assert "$MY_SECRET_KEY" in result.output

    def test_list_provider_with_direct_key(self, mock_config, make_provider):
        """Test listing shows direct key indicator (not the key itself)."""
        mock_config.user.providers = [make_provider(api_key="sk-secret")]
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list"])
        assert result.exit_code == 0
        assert "configured directly" in result.output
        assert "sk-secret" not in result.output

    def test_list_multiple_providers(self, mock_config, make_provider):
        """Test listing multiple providers."""
        mock_config.user.providers = [
            make_provider(name="provider-a", base_url="http://a:8000/v1"),
            make_provider(name="provider-b", base_url="http://b:9000/v1"),
        ]
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list"])
        assert result.exit_code == 0
        assert "provider-a" in result.output
        assert "provider-b" in result.output
        assert "2 custom provider(s)" in result.output


class TestProvidersTest:
    """Tests for 'providers test' command."""

    def test_provider_not_found(self, mock_config):
        """Test when provider name doesn't match any config."""
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "test", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_provider_not_found_shows_available(self, mock_config, make_provider):
        """Test that missing provider shows available options."""
        mock_config.user.providers = [make_provider(name="my-llm")]
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "test", "wrong-name"])
        assert result.exit_code == 1
        assert "not found" in result.output
        assert "my-llm" in result.output

    def test_provider_not_found_no_providers(self, mock_config):
        """Test missing provider when no providers configured at all."""
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "test", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output
        assert "gptme.toml" in result.output

    def test_missing_api_key_env(self, mock_config, make_provider, monkeypatch):
        """Test when API key env var is not set."""
        mock_config.user.providers = [make_provider(api_key_env="MISSING_KEY_VAR")]
        monkeypatch.delenv("MISSING_KEY_VAR", raising=False)
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "test", "test-provider"])
        assert result.exit_code == 1
        assert "not set" in result.output

    def test_successful_connection(self, mock_config, make_provider):
        """Test successful provider connection listing models."""
        mock_config.user.providers = [
            make_provider(api_key="test-key", default_model="llama-3")
        ]

        # Mock OpenAI client
        mock_model = Mock()
        mock_model.id = "llama-3"
        mock_client = Mock()
        mock_client.models.list.return_value = [mock_model]

        with patch("openai.OpenAI", return_value=mock_client) as mock_cls:
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "test-provider"])

        assert result.exit_code == 0
        assert "Connected" in result.output
        assert "llama-3" in result.output
        assert "Default model" in result.output
        assert "is available" in result.output

        # Verify client was created with correct params
        mock_cls.assert_called_once_with(
            api_key="test-key",
            base_url="http://localhost:8000/v1",
            timeout=10,
        )

    def test_successful_connection_default_model_missing(
        self, mock_config, make_provider
    ):
        """Test when default model is not in the provider's model list."""
        mock_config.user.providers = [
            make_provider(api_key="test-key", default_model="missing-model")
        ]

        mock_model = Mock()
        mock_model.id = "other-model"
        mock_client = Mock()
        mock_client.models.list.return_value = [mock_model]

        with patch("openai.OpenAI", return_value=mock_client):
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "test-provider"])

        assert result.exit_code == 0
        assert "Connected" in result.output
        assert "not found in model list" in result.output

    def test_connection_failure(self, mock_config, make_provider):
        """Test when connection to provider fails."""
        mock_config.user.providers = [make_provider(api_key="test-key")]

        mock_client = Mock()
        mock_client.models.list.side_effect = ConnectionError("Connection refused")

        with patch("openai.OpenAI", return_value=mock_client):
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "test-provider"])

        assert result.exit_code == 1
        assert "Connection failed" in result.output
        assert "Connection refused" in result.output

    def test_many_models_truncated(self, mock_config, make_provider):
        """Test that model list is truncated after 10 entries."""
        mock_config.user.providers = [make_provider(api_key="test-key")]

        models = []
        for i in range(15):
            m = Mock()
            m.id = f"model-{i}"
            models.append(m)

        mock_client = Mock()
        mock_client.models.list.return_value = models

        with patch("openai.OpenAI", return_value=mock_client):
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "test-provider"])

        assert result.exit_code == 0
        assert "model-0" in result.output
        assert "model-9" in result.output
        assert "model-10" not in result.output
        assert "5 more" in result.output

    def test_hyphenated_provider_name_env_var(self, mock_config, make_provider):
        """Test that hyphenated provider names produce valid env var names."""
        mock_config.user.providers = [
            make_provider(name="my-local-llm")  # hyphens → underscores
        ]

        mock_client = Mock()
        mock_client.models.list.return_value = []

        with (
            patch.dict("os.environ", {"MY_LOCAL_LLM_API_KEY": "env-key"}),
            patch("openai.OpenAI", return_value=mock_client) as mock_cls,
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "my-local-llm"])

        assert result.exit_code == 0
        assert "Connected" in result.output
        mock_cls.assert_called_once_with(
            api_key="env-key",
            base_url="http://localhost:8000/v1",
            timeout=10,
        )

    def test_api_key_from_env_default(self, mock_config, make_provider):
        """Test API key resolution from default env var (PROVIDER_NAME_API_KEY)."""
        mock_config.user.providers = [
            make_provider(name="myservice")  # no api_key or api_key_env
        ]

        mock_client = Mock()
        mock_client.models.list.return_value = []

        with (
            patch.dict("os.environ", {"MYSERVICE_API_KEY": "env-key"}),
            patch("openai.OpenAI", return_value=mock_client) as mock_cls,
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "myservice"])

        assert result.exit_code == 0
        assert "Connected" in result.output
        mock_cls.assert_called_once_with(
            api_key="env-key",
            base_url="http://localhost:8000/v1",
            timeout=10,
        )

    def test_api_key_from_explicit_env(self, mock_config, make_provider):
        """Test API key resolution from explicit env var."""
        mock_config.user.providers = [make_provider(api_key_env="CUSTOM_KEY_VAR")]

        mock_client = Mock()
        mock_client.models.list.return_value = []

        with (
            patch.dict("os.environ", {"CUSTOM_KEY_VAR": "custom-env-key"}),
            patch("openai.OpenAI", return_value=mock_client) as mock_cls,
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "test-provider"])

        assert result.exit_code == 0
        assert "Connected" in result.output
        mock_cls.assert_called_once_with(
            api_key="custom-env-key",
            base_url="http://localhost:8000/v1",
            timeout=10,
        )

    def test_star_marks_default_model(self, mock_config, make_provider):
        """Test that the default model gets a star marker in the list."""
        mock_config.user.providers = [
            make_provider(api_key="k", default_model="special-model")
        ]

        m1, m2 = Mock(), Mock()
        m1.id = "other-model"
        m2.id = "special-model"
        mock_client = Mock()
        mock_client.models.list.return_value = [m1, m2]

        with patch("openai.OpenAI", return_value=mock_client):
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "test-provider"])

        assert result.exit_code == 0
        # The default model should have a star
        lines = result.output.split("\n")
        special_line = [ln for ln in lines if "special-model" in ln][0]
        assert "⭐" in special_line
        other_line = [ln for ln in lines if "other-model" in ln][0]
        assert "⭐" not in other_line

    def test_no_default_model(self, mock_config, make_provider):
        """Test output when no default model is configured."""
        mock_config.user.providers = [
            make_provider(api_key="k")  # no default_model
        ]

        mock_model = Mock()
        mock_model.id = "some-model"
        mock_client = Mock()
        mock_client.models.list.return_value = [mock_model]

        with patch("openai.OpenAI", return_value=mock_client):
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "test-provider"])

        assert result.exit_code == 0
        assert "Connected" in result.output
        # No default model message
        assert "Default model" not in result.output
