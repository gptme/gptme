"""CrowLLM is served over the existing OpenAI-compatible transport."""

from unittest.mock import patch

import pytest

from gptme.config import get_config
from gptme.llm import PROVIDER_API_KEYS, PROVIDER_DEFAULT_MODELS, llm_openai
from gptme.llm.models.types import PROVIDERS_OPENAI


def test_crowllm_registration() -> None:
    assert "crowllm" in PROVIDERS_OPENAI
    assert PROVIDER_API_KEYS["crowllm"] == "CROWLLM_API_KEY"
    assert PROVIDER_DEFAULT_MODELS["crowllm"] == "crowllm/glm-5.3"


def test_crowllm_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CROWLLM_API_KEY", "test-crowllm-key")
    with (
        patch.object(llm_openai, "_init_openai_client") as create,
        patch.dict(llm_openai.clients, {"crowllm": object()}),
    ):
        llm_openai.init("crowllm", get_config())
    assert create.call_args.kwargs["api_key"] == "test-crowllm-key"
    assert create.call_args.kwargs["base_url"] == "https://crowllm.com/v1"
