"""Tests for gptme-util llm generate --max-tokens and --temperature flags."""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gptme.cli.util import main


def _mock_init_generate(mock_init, mock_provider, mock_init_llm, mock_default_model):
    """Return a context-manager stack mocking the llm generate setup path."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("gptme.cli.util.main"))  # dummy; replaced below
    return stack


def _generate_patches(response="test response"):
    """Return a list of patch() objects covering the llm generate init path."""
    from unittest.mock import MagicMock, patch

    mock_model = MagicMock()
    mock_model.full = "anthropic/claude-haiku-4-5"

    return [
        patch("gptme.init.init"),
        patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
        patch("gptme.llm.init_llm"),
        patch("gptme.llm.models.get_default_model", return_value=mock_model),
        patch(
            "gptme.llm._chat_complete",
            return_value=(response, None),
        ),
    ]


class TestLlmGenerateHelp:
    def test_help_shows_new_flags(self):
        runner = CliRunner()
        result = runner.invoke(main, ["llm", "generate", "--help"])
        assert result.exit_code == 0
        assert "--max-tokens" in result.output
        assert "--temperature" in result.output


class TestLlmGenerateValidation:
    def test_max_tokens_zero_rejected(self):
        runner = CliRunner()
        result = runner.invoke(main, ["llm", "generate", "--max-tokens", "0", "hello"])
        assert result.exit_code != 0
        assert (
            "max-tokens" in result.output.lower()
            or "max_tokens" in result.output.lower()
        )

    def test_max_tokens_negative_rejected(self):
        runner = CliRunner()
        result = runner.invoke(main, ["llm", "generate", "--max-tokens", "-1", "hello"])
        assert result.exit_code != 0

    def test_temperature_above_range_rejected(self):
        runner = CliRunner()
        result = runner.invoke(
            main, ["llm", "generate", "--temperature", "2.1", "hello"]
        )
        assert result.exit_code != 0
        assert "temperature" in result.output.lower()

    def test_temperature_negative_rejected(self):
        runner = CliRunner()
        result = runner.invoke(
            main, ["llm", "generate", "--temperature", "-0.1", "hello"]
        )
        assert result.exit_code != 0

    def test_max_tokens_non_integer_rejected(self):
        runner = CliRunner()
        result = runner.invoke(
            main, ["llm", "generate", "--max-tokens", "abc", "hello"]
        )
        assert result.exit_code != 0


class TestLlmGenerateParams:
    def test_max_tokens_passed_to_chat_complete(self):
        """--max-tokens is forwarded to _chat_complete."""
        from unittest.mock import MagicMock, patch

        mock_model = MagicMock()
        mock_model.full = "anthropic/claude-haiku-4-5"
        mock_complete = MagicMock(return_value=("hello", None))

        with (
            patch("gptme.init.init"),
            patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
            patch("gptme.llm.init_llm"),
            patch("gptme.llm.models.get_default_model", return_value=mock_model),
            patch("gptme.llm._chat_complete", mock_complete),
        ):
            runner = CliRunner()
            result = runner.invoke(
                main,
                ["llm", "generate", "--max-tokens", "50", "say hello"],
            )

        assert result.exit_code == 0, result.output
        call_kwargs = mock_complete.call_args[1]
        assert call_kwargs["max_tokens"] == 50

    def test_temperature_passed_to_chat_complete(self):
        """--temperature is forwarded to _chat_complete."""
        from unittest.mock import MagicMock, patch

        mock_model = MagicMock()
        mock_model.full = "anthropic/claude-haiku-4-5"
        mock_complete = MagicMock(return_value=("hello", None))

        with (
            patch("gptme.init.init"),
            patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
            patch("gptme.llm.init_llm"),
            patch("gptme.llm.models.get_default_model", return_value=mock_model),
            patch("gptme.llm._chat_complete", mock_complete),
        ):
            runner = CliRunner()
            result = runner.invoke(
                main,
                ["llm", "generate", "--temperature", "0.0", "say hello"],
            )

        assert result.exit_code == 0, result.output
        call_kwargs = mock_complete.call_args[1]
        assert call_kwargs["temperature"] == 0.0

    def test_both_params_passed_together(self):
        """Both --max-tokens and --temperature can be combined."""
        from unittest.mock import MagicMock, patch

        mock_model = MagicMock()
        mock_model.full = "anthropic/claude-haiku-4-5"
        mock_complete = MagicMock(return_value=("hello", None))

        with (
            patch("gptme.init.init"),
            patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
            patch("gptme.llm.init_llm"),
            patch("gptme.llm.models.get_default_model", return_value=mock_model),
            patch("gptme.llm._chat_complete", mock_complete),
        ):
            runner = CliRunner()
            result = runner.invoke(
                main,
                [
                    "llm",
                    "generate",
                    "--max-tokens",
                    "100",
                    "--temperature",
                    "0.7",
                    "say hello",
                ],
            )

        assert result.exit_code == 0, result.output
        call_kwargs = mock_complete.call_args[1]
        assert call_kwargs["max_tokens"] == 100
        assert call_kwargs["temperature"] == pytest.approx(0.7)

    def test_no_params_defaults_to_none(self):
        """Without flags, max_tokens and temperature default to None."""
        from unittest.mock import MagicMock, patch

        mock_model = MagicMock()
        mock_model.full = "anthropic/claude-haiku-4-5"
        mock_complete = MagicMock(return_value=("hello", None))

        with (
            patch("gptme.init.init"),
            patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
            patch("gptme.llm.init_llm"),
            patch("gptme.llm.models.get_default_model", return_value=mock_model),
            patch("gptme.llm._chat_complete", mock_complete),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["llm", "generate", "say hello"])

        assert result.exit_code == 0, result.output
        call_kwargs = mock_complete.call_args[1]
        assert call_kwargs["max_tokens"] is None
        assert call_kwargs["temperature"] is None

    def test_max_tokens_passed_to_stream(self):
        """--max-tokens is forwarded to _stream when --stream is set."""
        from unittest.mock import MagicMock, patch

        mock_model = MagicMock()
        mock_model.full = "anthropic/claude-haiku-4-5"
        mock_stream = MagicMock(return_value=iter(["hello", " world"]))

        with (
            patch("gptme.init.init"),
            patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
            patch("gptme.llm.init_llm"),
            patch("gptme.llm.models.get_default_model", return_value=mock_model),
            patch("gptme.llm._stream", mock_stream),
        ):
            runner = CliRunner()
            result = runner.invoke(
                main,
                ["llm", "generate", "--stream", "--max-tokens", "50", "say hello"],
            )

        assert result.exit_code == 0, result.output
        call_kwargs = mock_stream.call_args[1]
        assert call_kwargs["max_tokens"] == 50

    def test_temperature_boundary_values_accepted(self):
        """0.0 and 2.0 are valid temperature boundaries."""
        from unittest.mock import MagicMock, patch

        mock_model = MagicMock()
        mock_model.full = "anthropic/claude-haiku-4-5"

        for temp in ["0.0", "2.0"]:
            mock_complete = MagicMock(return_value=("hello", None))
            with (
                patch("gptme.init.init"),
                patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
                patch("gptme.llm.init_llm"),
                patch("gptme.llm.models.get_default_model", return_value=mock_model),
                patch("gptme.llm._chat_complete", mock_complete),
            ):
                runner = CliRunner()
                result = runner.invoke(
                    main,
                    ["llm", "generate", "--temperature", temp, "hello"],
                )
            assert result.exit_code == 0, (
                f"temperature={temp} should be valid, got: {result.output}"
            )
