"""NVIDIA uses the existing OpenAI transport and is detected via NVIDIA_API_KEY."""

from unittest.mock import MagicMock, patch

import pytest

from gptme.config import get_config
from gptme.llm import (
    PROVIDER_API_KEYS,
    PROVIDER_DEFAULT_MODELS,
    list_available_providers,
    llm_openai,
)
from gptme.llm.models.types import PROVIDERS_OPENAI


def test_nvidia_registration() -> None:
    assert "nvidia" in PROVIDERS_OPENAI
    assert PROVIDER_API_KEYS["nvidia"] == "NVIDIA_API_KEY"
    # Like azure, nvidia has no built-in recommended model.
    assert "nvidia" not in PROVIDER_DEFAULT_MODELS


def test_nvidia_listed_when_key_set(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config = MagicMock()
    config.get_env.side_effect = lambda name: (
        "nvapi-test" if name == "NVIDIA_API_KEY" else None
    )

    with patch("gptme.llm.get_config", return_value=config):
        available = {
            (str(provider), source) for provider, source in list_available_providers()
        }
    assert ("nvidia", "NVIDIA_API_KEY") in available


def test_nvidia_not_listed_without_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config = MagicMock()
    config.get_env.return_value = None

    with patch("gptme.llm.get_config", return_value=config):
        providers = {str(provider) for provider, _ in list_available_providers()}
    assert "nvidia" not in providers


def test_nvidia_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
    with (
        patch.object(llm_openai, "_init_openai_client") as create,
        patch.dict(llm_openai.clients, {"nvidia": object()}),
    ):
        llm_openai.init("nvidia", get_config())
    assert create.call_args.kwargs["api_key"] == "test-nvidia-key"
    assert create.call_args.kwargs["base_url"] == "https://integrate.api.nvidia.com/v1"
