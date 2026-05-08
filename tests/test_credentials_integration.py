from unittest.mock import MagicMock, patch

from gptme.credentials import set_stored_api_key
from gptme.llm import list_available_providers
from gptme.llm.llm_openai import _get_provider_api_key


def test_list_available_providers_includes_credentials_store(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    set_stored_api_key("openrouter", "sk-or-test-12345678")

    config = MagicMock()
    config.get_env.return_value = None

    with patch("gptme.llm.get_config", return_value=config):
        available = {
            (str(provider), source) for provider, source in list_available_providers()
        }
    assert ("openrouter", "credentials.toml") in available


def test_get_provider_api_key_falls_back_to_credentials_store():
    config = MagicMock()
    config.get_env.return_value = None

    with patch("gptme.credentials.get_stored_api_key", return_value="sk-or-test"):
        assert (
            _get_provider_api_key(config, "openrouter", "OPENROUTER_API_KEY")
            == "sk-or-test"
        )
