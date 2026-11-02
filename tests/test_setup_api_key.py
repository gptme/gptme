"""API-key detection and validation in the CLI setup prompt."""

from unittest.mock import Mock, patch

import pytest

from gptme.cli.setup import _prompt_api_key, ask_for_api_key
from gptme.llm import get_model_from_api_key


@pytest.mark.parametrize(
    ("api_key", "provider", "env_var"),
    [
        ("AQ.test-gemini-key", "gemini", "GEMINI_API_KEY"),
        ("AIza-test-gemini-key", "gemini", "GEMINI_API_KEY"),
        ("sk-ant-test", "anthropic", "ANTHROPIC_API_KEY"),
        ("sk-or-test", "openrouter", "OPENROUTER_API_KEY"),
        ("sk-test", "openai", "OPENAI_API_KEY"),
    ],
)
def test_detect_api_key_provider(api_key: str, provider: str, env_var: str) -> None:
    assert get_model_from_api_key(api_key) == (api_key, provider, env_var)


@pytest.mark.parametrize("api_key", ["", "unknown-key", "AQ-without-dot", "aq.test"])
def test_unknown_api_key_prefix(api_key: str) -> None:
    assert get_model_from_api_key(api_key) is None


@pytest.mark.parametrize("api_key", ["AQ.test-gemini-key", "AIza-test-gemini-key"])
def test_prompt_gemini_key_validates_with_google(api_key: str) -> None:
    with (
        patch("gptme.cli.setup.Prompt.ask", side_effect=[f"  {api_key}  "]) as prompt,
        patch(
            "gptme.llm.validate.requests.get", return_value=Mock(status_code=200)
        ) as request,
    ):
        assert _prompt_api_key() == (api_key, "gemini", "GEMINI_API_KEY")

    prompt.assert_called_once_with("API key", password=True)
    request.assert_called_once_with(
        f"https://generativelanguage.googleapis.com/v1/models?key={api_key}",
        timeout=10,
    )


@pytest.mark.parametrize("api_key", ["AQ.test-gemini-key", "AIza-test-gemini-key"])
def test_setup_saves_gemini_key(api_key: str) -> None:
    with (
        patch("gptme.cli.setup._choose_first_run_auth", return_value="5"),
        patch("gptme.cli.setup.Prompt.ask", side_effect=[api_key]),
        patch("gptme.llm.validate.requests.get", return_value=Mock(status_code=200)),
        patch("gptme.cli.setup.set_config_value") as save,
    ):
        assert ask_for_api_key() == ("gemini", api_key)

    save.assert_called_once_with("env.GEMINI_API_KEY", api_key)


def test_prompt_retries_rejected_gemini_key() -> None:
    rejected = "AQ.rejected-key"
    accepted = "AQ.accepted-key"
    response = Mock(status_code=400)
    response.json.return_value = {"error": {"status": "INVALID_ARGUMENT"}}
    with (
        patch("gptme.cli.setup.Prompt.ask", side_effect=[rejected, accepted]),
        patch(
            "gptme.llm.validate.requests.get",
            side_effect=[response, Mock(status_code=200)],
        ) as request,
    ):
        assert _prompt_api_key() == (accepted, "gemini", "GEMINI_API_KEY")

    assert request.call_count == 2


def test_prompt_unknown_key_shows_both_gemini_formats(capsys) -> None:
    with (
        patch(
            "gptme.cli.setup.Prompt.ask",
            side_effect=["unknown-key", "AIza-test-gemini-key"],
        ),
        patch("gptme.llm.validate.requests.get", return_value=Mock(status_code=200)),
    ):
        assert _prompt_api_key() == (
            "AIza-test-gemini-key",
            "gemini",
            "GEMINI_API_KEY",
        )

    output = capsys.readouterr().out
    assert "Invalid API key format" in output
    assert "AIza" in output
    assert "AQ." in output
