"""Tests for `gptme-util llm generate` command, specifically --output-format."""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from gptme.cli.util import llm_generate


def _fake_chat_complete(messages, model, tools, **kwargs):
    usage = {"input_tokens": 10, "output_tokens": 5}
    meta = {"model": model, "usage": usage}
    return "hello world", meta


@pytest.fixture()
def mock_llm_env():
    """Patch LLM initialisation so tests run without real credentials or API calls."""
    with (
        # redirect_stderr is used inside the function to suppress console output;
        # keep it functional but mock everything it imports
        patch("gptme.init.init"),
        patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
        patch("gptme.llm.init_llm"),
        patch(
            "gptme.llm.models.get_default_model",
            return_value=MagicMock(full="anthropic/claude-test"),
        ),
        # Patch at the source so the local import inside the function body gets the mock
        patch("gptme.llm._chat_complete", side_effect=_fake_chat_complete),
    ):
        yield


def test_llm_generate_json_output(mock_llm_env):
    """--output-format json returns a valid JSON object with content and usage."""
    runner = CliRunner()
    result = runner.invoke(
        llm_generate,
        ["--output-format", "json", "--model", "anthropic/claude-test", "say hi"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["content"] == "hello world"
    assert data["model"] == "anthropic/claude-test"
    assert "usage" in data
    assert data["usage"]["input_tokens"] == 10
    assert data["usage"]["output_tokens"] == 5


def test_llm_generate_json_incompatible_with_stream():
    """--output-format json raises UsageError when combined with --stream."""
    runner = CliRunner()
    result = runner.invoke(
        llm_generate,
        ["--output-format", "json", "--stream", "say hi"],
    )
    assert result.exit_code != 0
    assert "incompatible" in result.output.lower()


def test_llm_generate_text_output_unchanged(mock_llm_env):
    """Default (text) output is the raw response string, not JSON."""
    runner = CliRunner()
    result = runner.invoke(
        llm_generate,
        ["--model", "anthropic/claude-test", "say hi"],
    )
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "hello world"
    # Must NOT be a JSON object
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.output)
