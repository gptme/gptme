"""Tests for the models-related gptme-util CLI commands."""

import json
from unittest.mock import Mock, patch

from click.testing import CliRunner

from gptme.cli.util import main


class TestModelsTest:
    """Tests for 'models test' command."""

    def test_help(self):
        """Test that help text is shown."""
        runner = CliRunner()
        result = runner.invoke(main, ["models", "test", "--help"])
        assert result.exit_code == 0
        assert "Test connectivity to a model" in result.output
        assert "MODEL_NAME" in result.output

    def test_unknown_model(self):
        """Test error on unrecognized model/provider."""
        with patch(
            "gptme.llm.get_provider_from_model",
            side_effect=ValueError("Unknown provider: fake"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["models", "test", "fake/model"])
        assert result.exit_code == 1
        assert "Unknown model or provider" in result.output
        assert "gptme-util models list" in result.output

    def test_missing_api_key(self):
        """Test error when API key is not configured."""
        mock_config = Mock()
        mock_config.get_env.return_value = None
        with (
            patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
            patch("gptme.cli.util.get_config", return_value=mock_config),
        ):
            runner = CliRunner()
            result = runner.invoke(
                main, ["models", "test", "anthropic/claude-haiku-4-5"]
            )
        assert result.exit_code == 1
        assert "ANTHROPIC_API_KEY" in result.output
        assert "not set" in result.output or "not configured" in result.output

    def test_missing_api_key_json(self):
        """Test --json output when API key is missing."""
        mock_config = Mock()
        mock_config.get_env.return_value = None
        with (
            patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
            patch("gptme.cli.util.get_config", return_value=mock_config),
        ):
            runner = CliRunner()
            result = runner.invoke(
                main, ["models", "test", "anthropic/claude-haiku-4-5", "--json"]
            )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False
        assert "ANTHROPIC_API_KEY" in data["error"]

    def test_successful_call(self):
        """Test successful model test call."""
        mock_config = Mock()
        mock_config.get_env.return_value = "sk-ant-test-key"
        with (
            patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
            patch("gptme.cli.util.get_config", return_value=mock_config),
            patch("gptme.llm.init_llm"),
            patch(
                "gptme.llm._chat_complete",
                return_value=("OK", {"model": "claude-haiku-4-5"}),
            ) as mock_complete,
        ):
            runner = CliRunner()
            result = runner.invoke(
                main, ["models", "test", "anthropic/claude-haiku-4-5"]
            )

        assert result.exit_code == 0, result.output
        assert "✅" in result.output
        assert "working correctly" in result.output
        call_args = mock_complete.call_args
        messages = call_args[0][0]
        assert messages[0].role == "system"
        assert messages[1].role == "user"
        assert call_args[1]["max_tokens"] == 5

    def test_successful_call_json(self):
        """Test --json output on successful call."""
        mock_config = Mock()
        mock_config.get_env.return_value = "sk-ant-test-key"
        with (
            patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
            patch("gptme.cli.util.get_config", return_value=mock_config),
            patch("gptme.llm.init_llm"),
            patch("gptme.llm._chat_complete", return_value=("OK", {})),
        ):
            runner = CliRunner()
            result = runner.invoke(
                main, ["models", "test", "anthropic/claude-haiku-4-5", "--json"]
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["model"] == "anthropic/claude-haiku-4-5"
        assert data["provider"] == "anthropic"
        assert "latency_ms" in data
        assert data["response"] == "OK"

    def test_api_failure(self):
        """Test error output when API call fails."""
        mock_config = Mock()
        mock_config.get_env.return_value = "sk-ant-expired"
        with (
            patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
            patch("gptme.cli.util.get_config", return_value=mock_config),
            patch("gptme.llm.init_llm"),
            patch(
                "gptme.llm._chat_complete",
                side_effect=Exception("Error code: 401 - Unauthorized"),
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(
                main, ["models", "test", "anthropic/claude-haiku-4-5"]
            )
        assert result.exit_code == 1
        assert "Request failed" in result.output
        assert "Common causes" in result.output

    def test_bare_provider_resolves_default(self):
        """Test that a bare provider name resolves to a default model."""
        mock_config = Mock()
        mock_config.get_env.side_effect = lambda k: (
            "sk-test" if k == "ANTHROPIC_API_KEY" else None
        )
        with (
            patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
            patch("gptme.cli.util.get_config", return_value=mock_config),
            patch("gptme.llm.init_llm"),
            patch("gptme.llm._chat_complete", return_value=("OK", {})),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["models", "test", "anthropic"])
        assert result.exit_code == 0, result.output
        assert "Using default model for anthropic" in result.output
        assert "claude-haiku-4-5" in result.output

    def test_bare_provider_no_default(self):
        """Test that providers without a default model (e.g. azure) give a clear error."""
        runner = CliRunner()
        result = runner.invoke(main, ["models", "test", "azure"])
        assert result.exit_code == 1
        assert "No default model for 'azure'" in result.output
        assert (
            "azure/my-deployment" in result.output or "full model name" in result.output
        )
