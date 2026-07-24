"""Test scaffold for gptme provider plugins.

Copy this file into your plugin package's ``tests/`` directory and adapt it:

    cp provider_test_scaffold.py tests/test_my_provider.py

Then replace:
- ``acme``            → your provider name
- ``ACME_API_KEY``    → your API key env var
- ``https://api.acme.ai/v1``  → your endpoint
- ``acme/turbo-v1``  → your model IDs

All tests here run *without* hitting the real API — they mock the OpenAI client
layer.  Before publishing, also run a live smoke test::

    ACME_API_KEY="sk-..." GPTME_RUN_LIVE_TESTS=1 pytest tests/ -m live -v

Usage with pytest::

    pytest tests/test_my_provider.py -v
    GPTME_RUN_LIVE_TESTS=1 pytest tests/test_my_provider.py -m live -v   # requires real API key
"""

from __future__ import annotations

import importlib.metadata
from unittest.mock import MagicMock, patch

import pytest

# ── Adapt these to your provider ─────────────────────────────────────────────
PROVIDER_NAME = "acme"
API_KEY_ENV = "ACME_API_KEY"
BASE_URL = "https://api.acme.ai/v1"
MODEL_IDS = ["acme/turbo-v1", "acme/mini-v1"]

# Import your plugin's ProviderPlugin instance here:
#   from gptme_provider_acme import provider as PROVIDER
# For this scaffold we build a synthetic one so it runs without installation:
from gptme.llm.models.types import ModelMeta, ProviderPlugin

PROVIDER = ProviderPlugin(
    name=PROVIDER_NAME,
    api_key_env=API_KEY_ENV,
    base_url=BASE_URL,
    models=[
        ModelMeta(
            provider="unknown",
            model=MODEL_IDS[0],
            context=128_000,
            max_output=4_096,
            price_input=0.10,
            price_output=0.40,
            supports_vision=True,
        ),
        ModelMeta(
            provider="unknown",
            model=MODEL_IDS[1],
            context=32_000,
            max_output=2_048,
            price_input=0.02,
            price_output=0.08,
        ),
    ],
)
# ─────────────────────────────────────────────────────────────────────────────


def _make_entry_point(plugin: ProviderPlugin) -> MagicMock:
    """Return a mock entry point that loads *plugin*.

    Note: this validates the in-memory plugin wiring only. It does NOT verify
    that the entry-point name or import target declared in your pyproject.toml
    match the installed package. Test that separately by installing the package
    and running ``importlib.metadata.entry_points(group="gptme.providers")``.
    """
    ep = MagicMock(spec=importlib.metadata.EntryPoint)
    ep.name = plugin.name
    ep.load.return_value = plugin
    return ep


@pytest.fixture(autouse=True)
def _reset_plugin_cache():
    from gptme.llm.provider_plugins import clear_plugin_cache

    clear_plugin_cache()
    yield
    clear_plugin_cache()


@pytest.fixture
def installed_provider():
    """Patch entry-point discovery so tests see your plugin as if it were installed."""
    ep = _make_entry_point(PROVIDER)
    with patch("importlib.metadata.entry_points", return_value=[ep]):
        yield PROVIDER


# ── 1. Plugin metadata ────────────────────────────────────────────────────────


class TestPluginMetadata:
    """Verify the ProviderPlugin declaration is correct."""

    def test_name(self):
        assert PROVIDER.name == PROVIDER_NAME

    def test_api_key_env(self):
        assert PROVIDER.api_key_env == API_KEY_ENV

    def test_base_url(self):
        assert PROVIDER.base_url == BASE_URL
        # Must be a valid URL (no trailing slash is fine; leading scheme required)
        assert PROVIDER.base_url.startswith(("https://", "http://"))

    def test_models_not_empty(self):
        assert len(PROVIDER.models) > 0, (
            "Provide at least one ModelMeta so users know what models exist"
        )

    def test_model_ids_fully_qualified(self):
        for m in PROVIDER.models:
            assert m.model.startswith(f"{PROVIDER_NAME}/"), (
                f"Model '{m.model}' must start with '{PROVIDER_NAME}/' "
                f"(e.g. '{PROVIDER_NAME}/my-model')"
            )

    def test_model_context_positive(self):
        for m in PROVIDER.models:
            assert m.context > 0, f"Model '{m.model}' context must be > 0"

    def test_pricing_non_negative(self):
        for m in PROVIDER.models:
            assert m.price_input >= 0, f"price_input for '{m.model}' must be >= 0"
            assert m.price_output >= 0, f"price_output for '{m.model}' must be >= 0"


# ── 2. Plugin discovery ───────────────────────────────────────────────────────


class TestPluginDiscovery:
    """Verify gptme can discover and load your plugin."""

    def test_discovered_by_name(self, installed_provider):
        from gptme.llm.provider_plugins import get_provider_plugin

        result = get_provider_plugin(PROVIDER_NAME)
        assert result is not None
        assert result.name == PROVIDER_NAME

    def test_is_plugin_provider(self, installed_provider):
        from gptme.llm.provider_plugins import is_plugin_provider

        assert is_plugin_provider(PROVIDER_NAME) is True

    def test_not_mistaken_for_builtin(self, installed_provider):
        from gptme.llm.models.types import PROVIDERS

        assert PROVIDER_NAME not in PROVIDERS, (
            f"'{PROVIDER_NAME}' is registered as a built-in provider. "
            "Plugin providers must use a name not already in PROVIDERS."
        )


# ── 3. Model resolution ───────────────────────────────────────────────────────


class TestModelResolution:
    """Verify model IDs resolve to the right ModelMeta."""

    @pytest.mark.parametrize("model_id", MODEL_IDS)
    def test_get_model_resolves(self, installed_provider, model_id):
        from gptme.llm.models.resolution import get_model

        result = get_model(model_id)
        assert result.model == model_id
        assert result.context > 0

    def test_unknown_model_falls_back(self, installed_provider):
        from gptme.llm.models.resolution import get_model

        result = get_model(f"{PROVIDER_NAME}/nonexistent-model-xyz")
        # Should fall back to 128k default, not raise
        assert result.context == 128_000

    def test_models_appear_in_listing(self, installed_provider):
        from gptme.llm.models.listing import get_model_list

        models = get_model_list(dynamic_fetch=False)
        listed = [m.model for m in models]
        for mid in MODEL_IDS:
            assert mid in listed, f"Model '{mid}' not in get_model_list() output"


# ── 4. Request routing ────────────────────────────────────────────────────────


class TestRequestRouting:
    """Verify that chat/stream calls are routed to the OpenAI client path."""

    def test_chat_routed_through_openai(self, installed_provider):
        from gptme.llm import _chat_complete
        from gptme.message import Message

        messages = [Message("user", "ping")]
        fake_result = ("pong", {"model": MODEL_IDS[0]})

        with patch("gptme.llm.llm_openai.chat", return_value=fake_result) as mock:
            result = _chat_complete(messages, MODEL_IDS[0], None)

        assert result == fake_result
        mock.assert_called_once()
        call_args = mock.call_args
        assert call_args.args[1] == MODEL_IDS[0]

    def test_stream_routed_through_openai(self, installed_provider):
        from gptme.llm import _stream
        from gptme.message import Message

        messages = [Message("user", "ping")]

        def _fake_stream(*args, **kwargs):
            yield "pong"
            return {"model": MODEL_IDS[0]}

        with patch("gptme.llm.llm_openai.stream", side_effect=_fake_stream) as mock:
            gen = _stream(messages, MODEL_IDS[0], None)
            chunks = list(gen)

        assert chunks == ["pong"]
        mock.assert_called_once()


# ── 5. Init function (only if you supply a custom init) ──────────────────────
#
# Uncomment and adapt if PROVIDER.init is not None.
#
# class TestCustomInit:
#     def test_init_registers_client(self, installed_provider):
#         from gptme.llm import init_llm
#         from gptme.llm.models import CustomProvider
#
#         client_registered = False
#
#         def _patched_init_openai_client(provider, **kwargs):
#             nonlocal client_registered
#             client_registered = True
#
#         with (
#             patch("gptme.llm.llm_openai._init_openai_client", side_effect=_patched_init_openai_client),
#             patch("gptme.llm.llm_openai.has_client", side_effect=lambda _: client_registered),
#             patch.dict("os.environ", {API_KEY_ENV: "test-key"}),
#         ):
#             init_llm(CustomProvider(PROVIDER_NAME))
#
#         assert client_registered, "init() must register an OpenAI-compatible client"


# ── 6. Live smoke test (skipped unless --live flag / env var set) ─────────────


@pytest.mark.live
@pytest.mark.skipif(
    not (
        __import__("os").getenv(API_KEY_ENV)
        and __import__("os").getenv("GPTME_RUN_LIVE_TESTS")
    ),
    reason=f"Set {API_KEY_ENV} and GPTME_RUN_LIVE_TESTS=1 to run live API tests",
)
class TestLiveAPI:
    """Optional smoke test that hits the real API.

    Requires BOTH the API key AND explicit opt-in to avoid unexpected charges::

        {API_KEY_ENV}="sk-..." GPTME_RUN_LIVE_TESTS=1 pytest tests/ -m live -v

    These tests are skipped automatically unless both env vars are set.
    """

    def test_live_completion(self):
        """A real completion returns non-empty content."""
        import gptme.llm as llm
        from gptme.llm.models import CustomProvider
        from gptme.message import Message

        model = MODEL_IDS[0]
        llm.init_llm(CustomProvider(PROVIDER_NAME))
        messages = [Message("user", 'Reply with the single word "pong".')]
        # _chat_complete returns (response_str, metadata_dict)
        result = llm._chat_complete(messages, model, None)
        assert str(result[0]).strip(), "Expected non-empty response from live API"
