"""Tests for setup helpers."""

from unittest.mock import MagicMock

from gptme.cli.setup import (
    _choose_first_run_auth,
    _generate_click_completion,
    _setup_openai_subscription,
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
