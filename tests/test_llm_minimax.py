"""Tests for MiniMax provider integration."""

from unittest.mock import MagicMock, patch

import pytest

from gptme.llm.models import (
    MODELS,
    PROVIDERS,
    PROVIDERS_OPENAI,
    ModelMeta,
    get_model,
    get_recommended_model,
    get_summary_model,
)


class TestMiniMaxModels:
    """Tests for MiniMax model registry entries."""

    def test_minimax_in_builtin_providers(self):
        """MiniMax should be a registered built-in provider."""
        assert "minimax" in PROVIDERS

    def test_minimax_in_providers_openai(self):
        """MiniMax should be in PROVIDERS_OPENAI (OpenAI-compatible)."""
        assert "minimax" in PROVIDERS_OPENAI

    def test_minimax_in_models_dict(self):
        """MiniMax should have entries in the MODELS dict."""
        assert "minimax" in MODELS
        assert len(MODELS["minimax"]) > 0

    def test_minimax_m27_model(self):
        """MiniMax-M2.7 should be registered with correct metadata."""
        model = get_model("minimax/MiniMax-M2.7")
        assert model.provider == "minimax"
        assert model.model == "MiniMax-M2.7"
        assert model.context == 1_000_000
        assert model.max_output == 131_072
        assert model.price_input > 0
        assert model.price_output > 0

    def test_minimax_m27_highspeed_model(self):
        """MiniMax-M2.7-highspeed should be registered with correct metadata."""
        model = get_model("minimax/MiniMax-M2.7-highspeed")
        assert model.provider == "minimax"
        assert model.model == "MiniMax-M2.7-highspeed"
        assert model.context == 1_000_000
        assert model.max_output == 131_072
        # highspeed variant should be cheaper
        m27 = get_model("minimax/MiniMax-M2.7")
        assert model.price_input < m27.price_input
        assert model.price_output < m27.price_output

    def test_minimax_m25_model(self):
        """MiniMax-M2.5 should be registered with correct metadata."""
        model = get_model("minimax/MiniMax-M2.5")
        assert model.provider == "minimax"
        assert model.model == "MiniMax-M2.5"
        assert model.context == 204_000

    def test_minimax_m25_highspeed_model(self):
        """MiniMax-M2.5-highspeed should be registered with correct metadata."""
        model = get_model("minimax/MiniMax-M2.5-highspeed")
        assert model.provider == "minimax"
        assert model.model == "MiniMax-M2.5-highspeed"
        assert model.context == 204_000

    def test_all_minimax_models_support_streaming(self):
        """All MiniMax models should support streaming by default."""
        for model_name in MODELS["minimax"]:
            model = get_model(f"minimax/{model_name}")
            assert model.supports_streaming is True


class TestMiniMaxRecommended:
    """Tests for MiniMax recommended/summary model selection."""

    def test_recommended_model(self):
        """MiniMax recommended model should be MiniMax-M2.7."""
        result = get_recommended_model("minimax")
        assert result == "MiniMax-M2.7"
        # Verify it exists in MODELS
        assert result in MODELS["minimax"]

    def test_summary_model(self):
        """MiniMax summary model should be MiniMax-M2.7-highspeed."""
        result = get_summary_model("minimax")
        assert result == "MiniMax-M2.7-highspeed"

    def test_provider_only_resolves(self):
        """Using just 'minimax' should resolve to recommended model."""
        model = get_model("minimax")
        assert model.provider == "minimax"
        assert model.model == "MiniMax-M2.7"


class TestMiniMaxProviderInit:
    """Tests for MiniMax provider client initialization."""

    @patch.dict("os.environ", {"MINIMAX_API_KEY": "test-key-123"})
    def test_minimax_api_key_in_provider_keys(self):
        """MINIMAX_API_KEY should be in PROVIDER_API_KEYS."""
        from gptme.llm import PROVIDER_API_KEYS

        assert "minimax" in PROVIDER_API_KEYS
        assert PROVIDER_API_KEYS["minimax"] == "MINIMAX_API_KEY"

    @patch("openai.OpenAI")
    def test_minimax_client_init(self, mock_openai_cls):
        """MiniMax provider should initialize OpenAI client with correct base_url."""
        from gptme.llm.llm_openai import clients, init

        mock_config = MagicMock()
        mock_config.get_env.return_value = None
        mock_config.get_env_required.return_value = "test-minimax-key"

        # Clear any existing client
        clients.pop("minimax", None)

        init("minimax", mock_config)

        mock_openai_cls.assert_called_once()
        call_kwargs = mock_openai_cls.call_args
        assert call_kwargs.kwargs["api_key"] == "test-minimax-key"
        assert call_kwargs.kwargs["base_url"] == "https://api.minimax.io/v1"


class TestMiniMaxIntegration:
    """Integration tests for MiniMax provider (require MINIMAX_API_KEY)."""

    @pytest.fixture(autouse=True)
    def _check_api_key(self):
        """Skip integration tests if MINIMAX_API_KEY is not set."""
        import os

        if not os.environ.get("MINIMAX_API_KEY"):
            pytest.skip("MINIMAX_API_KEY not set")

    def test_minimax_chat_completion(self):
        """Test basic chat completion with MiniMax provider."""
        from gptme.llm import init_llm
        from gptme.llm.llm_openai import chat
        from gptme.message import Message

        init_llm("minimax")
        messages = [
            Message(role="system", content="You are a helpful assistant."),
            Message(role="user", content="Say hello in one word."),
        ]
        response, metadata = chat(messages, "minimax/MiniMax-M2.7", tools=None)
        assert response
        assert len(response) > 0

    def test_minimax_streaming(self):
        """Test streaming with MiniMax provider."""
        from gptme.llm import init_llm
        from gptme.llm.llm_openai import stream
        from gptme.message import Message

        init_llm("minimax")
        messages = [
            Message(role="system", content="You are a helpful assistant."),
            Message(role="user", content="Say hello in one word."),
        ]
        chunks = list(stream(messages, "minimax/MiniMax-M2.7", tools=None))
        assert len(chunks) > 0
        full_response = "".join(chunks)
        assert len(full_response) > 0

    def test_minimax_highspeed_model(self):
        """Test chat completion with MiniMax-M2.7-highspeed model."""
        from gptme.llm import init_llm
        from gptme.llm.llm_openai import chat
        from gptme.message import Message

        init_llm("minimax")
        messages = [
            Message(role="system", content="You are a helpful assistant."),
            Message(role="user", content="What is 2+2? Answer with just the number."),
        ]
        response, metadata = chat(
            messages, "minimax/MiniMax-M2.7-highspeed", tools=None
        )
        assert response
        assert "4" in response
