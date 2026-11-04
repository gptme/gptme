"""Tests for setup helpers."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

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
    result = _generate_click_completion("tcsh")
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


def test_custom_provider_update_preserves_key_and_overrides_old_local_values(
    tmp_path, monkeypatch
):
    """Interactive edits must survive a reload across both config layers."""
    import gptme.config.user as user_mod

    main = tmp_path / "config.toml"
    local = tmp_path / "config.local.toml"
    main.write_text('[user]\nname = "Unrelated"\n')
    local.write_text(
        '[[providers]]\nname = "relay"\nbase_url = "https://old.invalid/v1"\n'
        'api_key = "SYNTHETIC-ONLY"\ndefault_model = "old-model"\n'
    )
    monkeypatch.setattr(user_mod, "config_path", str(main))
    monkeypatch.setattr("gptme.cli.setup.config_path", str(main))
    monkeypatch.setattr("gptme.config.core.reload_config", lambda: None)
    monkeypatch.setattr(
        "gptme.cli.setup.get_config",
        lambda: SimpleNamespace(user=user_mod.load_user_config(str(main))),
    )
    monkeypatch.setattr(
        "gptme.cli.setup.Prompt.ask",
        MagicMock(side_effect=["relay", "https://new.invalid/v2", "", "new-model"]),
    )

    _setup_custom_provider()

    provider = user_mod.load_user_config(str(main)).providers[0]
    assert provider.base_url == "https://new.invalid/v2"
    assert provider.default_model == "new-model"
    assert provider.api_key == "SYNTHETIC-ONLY"
    assert main.read_text() == '[user]\nname = "Unrelated"\n'
    assert "SYNTHETIC-ONLY" not in main.read_text()


@pytest.mark.parametrize("new_key", ["", "SYNTHETIC-NEW"])
def test_custom_provider_update_keeps_partial_local_overrides_and_other_providers(
    tmp_path, monkeypatch, new_key
):
    import gptme.config.user as user_mod

    main = tmp_path / "config.toml"
    local = tmp_path / "config.local.toml"
    original_main = (
        '# Shared config\n[user]\nname = "Unrelated"\n'
        '[[providers]]\nname = "relay"\nbase_url = "https://old.invalid/v1"\n'
        'default_model = "old-model"\napi_key_env = "RELAY_TOKEN"\n'
        '[[providers]]\nname = "other"\nbase_url = "https://other.invalid/v1"\n'
    )
    main.write_text(original_main)
    local.write_text(
        '# Private config\n[env]\nUNRELATED = "preserved"\n'
        '[[providers]]\nname = "relay"\ndefault_model = "local-model"\n'
        '[[providers]]\nname = "private-other"\nbase_url = "https://private.invalid/v1"\n'
        'api_key = "SYNTHETIC-OTHER"\n'
    )
    monkeypatch.setattr(user_mod, "config_path", str(main))
    monkeypatch.setattr("gptme.cli.setup.config_path", str(main))
    monkeypatch.setattr("gptme.config.core.reload_config", lambda: None)
    monkeypatch.setattr(
        "gptme.cli.setup.get_config",
        lambda: SimpleNamespace(user=user_mod.load_user_config(str(main))),
    )
    monkeypatch.setattr(
        "gptme.cli.setup.Prompt.ask",
        MagicMock(side_effect=["relay", "https://new.invalid/v2", new_key, ""]),
    )

    before = user_mod.load_user_config(str(main))
    assert before.providers[0].default_model == "local-model"
    _setup_custom_provider()
    loaded = user_mod.load_user_config(str(main))
    provider = loaded.providers[0]
    assert provider.base_url == "https://new.invalid/v2"
    assert provider.default_model == "local-model"
    assert provider.api_key == (new_key or None)
    assert provider.api_key_env == "RELAY_TOKEN"
    assert loaded.providers[1:] == before.providers[1:]
    assert main.read_text() == original_main
    assert "# Private config" in local.read_text()
    assert 'UNRELATED = "preserved"' in local.read_text()
    assert "SYNTHETIC" not in main.read_text()
