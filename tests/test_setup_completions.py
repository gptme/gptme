"""Tests for setup helpers."""

from pathlib import Path
from unittest.mock import MagicMock

from gptme.cli.setup import (
    _choose_first_run_auth,
    _generate_click_completion,
    _setup_custom_provider,
    _setup_grok_subscription,
    _setup_openai_subscription,
    ask_for_api_key,
)


def test_generate_bash_completion():
    """Test that bash completion script is generated correctly."""
    script = _generate_click_completion("bash")
    assert script is not None
    assert "_GPTME_COMPLETE" in script
    assert "_gptme_completion" in script
    assert "gptme" in script
    # Bash completions should have bash-specific content
    assert "complete" in script.lower()


def test_generate_zsh_completion():
    """Test that zsh completion script is generated correctly."""
    script = _generate_click_completion("zsh")
    assert script is not None
    assert "_GPTME_COMPLETE" in script
    assert "_gptme_completion" in script
    assert "gptme" in script


def test_generate_unsupported_shell():
    """Test that unsupported shells return None."""
    result = _generate_click_completion("powershell")
    assert result is None


def test_generate_fish_completion():
    """Test that fish completion can also be generated (even though we use a separate path)."""
    script = _generate_click_completion("fish")
    assert script is not None
    assert "_GPTME_COMPLETE" in script
    assert "gptme" in script


def test_choose_first_run_auth_defaults_to_subscription(monkeypatch):
    prompt = MagicMock(return_value="1")
    monkeypatch.setattr("gptme.cli.setup.Prompt.ask", prompt)

    assert _choose_first_run_auth() == "1"
    assert prompt.call_args.kwargs["default"] == "1"
    assert "4" in prompt.call_args.kwargs["choices"]


def test_setup_openai_subscription_persists_default_model(monkeypatch):
    authenticate = MagicMock()
    set_value = MagicMock()
    monkeypatch.setattr(
        "gptme.llm.llm_openai_subscription.oauth_authenticate", authenticate
    )
    monkeypatch.setattr("gptme.cli.setup.set_config_value", set_value)
    monkeypatch.setattr(
        "gptme.cli.setup.get_recommended_model", lambda _provider: "gpt-5.6-sol"
    )

    provider, credential = _setup_openai_subscription()

    authenticate.assert_called_once_with()
    set_value.assert_called_once_with(
        "models.default", "openai-subscription/gpt-5.6-sol"
    )
    assert provider == "openai-subscription"
    assert credential == "oauth"


def test_setup_grok_subscription_persists_default_model(monkeypatch):
    authenticate = MagicMock()
    set_value = MagicMock()
    monkeypatch.setattr(
        "gptme.llm.llm_grok_subscription.oauth_authenticate", authenticate
    )
    monkeypatch.setattr("gptme.cli.setup.set_config_value", set_value)
    monkeypatch.setattr(
        "gptme.cli.setup.get_recommended_model", lambda _provider: "grok-4.5"
    )

    provider, credential = _setup_grok_subscription()

    authenticate.assert_called_once_with()
    set_value.assert_called_once_with("models.default", "grok-subscription/grok-4.5")
    assert provider == "grok-subscription"
    assert credential == "oauth"


def test_custom_provider_requires_default_for_first_run(monkeypatch):
    prompt = MagicMock(
        side_effect=["custom", "http://localhost:8000/v1", "", "", "llama3"]
    )
    save_provider = MagicMock()
    monkeypatch.setattr("gptme.cli.setup.Prompt.ask", prompt)
    monkeypatch.setattr("gptme.cli.setup.save_provider_config", save_provider)
    monkeypatch.setattr(
        "gptme.cli.setup.get_user_config_paths",
        lambda: (Path("/config.toml"), Path("/config.local.toml")),
    )

    provider, credential = _setup_custom_provider(require_default_model=True)

    assert provider == "custom"
    assert credential == ""
    assert save_provider.call_args.args[0].default_model == "llama3"
    assert prompt.call_count == 5


def test_custom_provider_strips_matching_provider_prefix(monkeypatch):
    prompt = MagicMock(
        side_effect=["custom", "http://localhost:8000/v1", "", "custom/llama3"]
    )
    save_provider = MagicMock()
    monkeypatch.setattr("gptme.cli.setup.Prompt.ask", prompt)
    monkeypatch.setattr("gptme.cli.setup.save_provider_config", save_provider)
    monkeypatch.setattr(
        "gptme.cli.setup.get_user_config_paths",
        lambda: (Path("/config.toml"), Path("/config.local.toml")),
    )

    provider, credential = _setup_custom_provider(require_default_model=True)

    assert provider == "custom"
    assert credential == ""
    assert save_provider.call_args.args[0].default_model == "llama3"


def test_provider_setup_forwards_default_model_requirement(monkeypatch):
    setup_custom = MagicMock(return_value=("custom", ""))
    monkeypatch.setattr("gptme.cli.setup._choose_first_run_auth", lambda: "6")
    monkeypatch.setattr("gptme.cli.setup._setup_custom_provider", setup_custom)

    assert ask_for_api_key(require_default_model=True) == ("custom", "")
    setup_custom.assert_called_once_with(require_default_model=True)
