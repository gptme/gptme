"""LLMTR uses the existing OpenAI-compatible transport."""

from unittest.mock import patch

import pytest

from gptme.config import get_config
from gptme.llm import PROVIDER_API_KEYS, PROVIDER_DEFAULT_MODELS, llm_openai
from gptme.llm.models import get_model
from gptme.llm.models.types import PROVIDERS, PROVIDERS_OPENAI


def test_llmtr_registration() -> None:
    assert "llmtr" in PROVIDERS
    assert "llmtr" in PROVIDERS_OPENAI
    assert PROVIDER_API_KEYS["llmtr"] == "LLMTR_API_KEY"
    assert PROVIDER_DEFAULT_MODELS["llmtr"] == "llmtr/minimax/minimax-m3.1-flash"
    model = get_model("llmtr/minimax/minimax-m3.1-flash")
    assert model.provider == "llmtr"
    assert model.model == "minimax/minimax-m3.1-flash"
    assert model.context == 128_000
    assert model.default_tool_format == "tool"


def test_llmtr_bare_provider_selects_default_model() -> None:
    model = get_model("llmtr")
    assert model == get_model("llmtr/minimax/minimax-m3.1-flash")
    assert model.provider == "llmtr"
    assert model.model == "minimax/minimax-m3.1-flash"


def test_llmtr_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLMTR_API_KEY", "test-llmtr-key")
    with (
        patch.object(llm_openai, "_init_openai_client") as create,
        patch.dict(llm_openai.clients, {"llmtr": object()}),
    ):
        llm_openai.init("llmtr", get_config())
    assert create.call_args.kwargs["api_key"] == "test-llmtr-key"
    assert create.call_args.kwargs["base_url"] == "https://llmtr.com/v1"
