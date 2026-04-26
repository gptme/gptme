"""Tests for the third-party provider plugin system.

Tests cover ProviderPlugin dataclass, plugin discovery via entry points,
get_model() resolution for plugin providers, and init_llm() routing.
"""

from __future__ import annotations

import importlib.metadata
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import Callable

import pytest

from gptme.llm.models.types import ModelMeta, ProviderPlugin
from gptme.llm.provider_plugins import (
    clear_plugin_cache,
    discover_provider_plugins,
    get_plugin_api_keys,
    get_provider_plugin,
    is_plugin_provider,
)
from gptme.message import Message

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_plugin_cache():
    """Clear the plugin discovery cache before and after each test."""
    clear_plugin_cache()
    yield
    clear_plugin_cache()


def _make_plugin(
    name: str = "testprovider",
    api_key_env: str = "TESTPROVIDER_API_KEY",
    base_url: str = "https://api.testprovider.ai/v1",
    models: list[ModelMeta] | None = None,
    init: Callable | None = None,
) -> ProviderPlugin:
    if models is None:
        models = [
            ModelMeta(
                provider="unknown",
                model=f"{name}/test-model-v1",
                context=128_000,
            )
        ]
    return ProviderPlugin(
        name=name,
        api_key_env=api_key_env,
        base_url=base_url,
        models=models,
        init=init,
    )


def _make_entry_point(plugin: ProviderPlugin) -> MagicMock:
    ep = MagicMock(spec=importlib.metadata.EntryPoint)
    ep.name = plugin.name
    ep.load.return_value = plugin
    return ep


# ---------------------------------------------------------------------------
# ProviderPlugin dataclass
# ---------------------------------------------------------------------------


class TestProviderPlugin:
    def test_basic_construction(self):
        plugin = _make_plugin()
        assert plugin.name == "testprovider"
        assert plugin.api_key_env == "TESTPROVIDER_API_KEY"
        assert plugin.base_url == "https://api.testprovider.ai/v1"
        assert len(plugin.models) == 1
        assert plugin.init is None

    def test_with_custom_init(self):
        init_fn = MagicMock()
        plugin = _make_plugin(init=init_fn)
        assert plugin.init is init_fn

    def test_models_default_empty(self):
        plugin = ProviderPlugin(
            name="empty",
            api_key_env="EMPTY_API_KEY",
            base_url="https://api.example.com/v1",
        )
        assert plugin.models == []

    def test_model_fully_qualified_name(self):
        plugin = _make_plugin(name="myprovider")
        assert plugin.models[0].model == "myprovider/test-model-v1"


# ---------------------------------------------------------------------------
# discover_provider_plugins
# ---------------------------------------------------------------------------


class TestDiscoverProviderPlugins:
    def test_no_plugins_registered(self):
        with patch("importlib.metadata.entry_points", return_value=[]):
            plugins = discover_provider_plugins()
        assert plugins == ()

    def test_single_plugin_discovered(self):
        plugin = _make_plugin()
        ep = _make_entry_point(plugin)
        with patch("importlib.metadata.entry_points", return_value=[ep]):
            plugins = discover_provider_plugins()
        assert len(plugins) == 1
        assert plugins[0].name == "testprovider"

    def test_multiple_plugins_discovered(self):
        p1 = _make_plugin(name="provider_a")
        p2 = _make_plugin(name="provider_b")
        eps = [_make_entry_point(p1), _make_entry_point(p2)]
        with patch("importlib.metadata.entry_points", return_value=eps):
            plugins = discover_provider_plugins()
        assert {p.name for p in plugins} == {"provider_a", "provider_b"}

    def test_invalid_plugin_type_is_skipped(self):
        """Entry points that don't export a ProviderPlugin are silently skipped."""
        ep = MagicMock(spec=importlib.metadata.EntryPoint)
        ep.name = "bad_plugin"
        ep.load.return_value = "not_a_provider_plugin"

        valid_plugin = _make_plugin()
        valid_ep = _make_entry_point(valid_plugin)

        with patch("importlib.metadata.entry_points", return_value=[ep, valid_ep]):
            plugins = discover_provider_plugins()
        assert len(plugins) == 1
        assert plugins[0].name == "testprovider"

    def test_load_exception_is_skipped(self):
        """Entry points that raise on load are silently skipped."""
        ep = MagicMock(spec=importlib.metadata.EntryPoint)
        ep.name = "broken_plugin"
        ep.load.side_effect = ImportError("missing dependency")

        valid_plugin = _make_plugin()
        valid_ep = _make_entry_point(valid_plugin)

        with patch("importlib.metadata.entry_points", return_value=[ep, valid_ep]):
            plugins = discover_provider_plugins()
        assert len(plugins) == 1

    def test_results_are_cached(self):
        plugin = _make_plugin()
        ep = _make_entry_point(plugin)
        with patch("importlib.metadata.entry_points", return_value=[ep]) as mock_eps:
            _ = discover_provider_plugins()
            _ = discover_provider_plugins()
        # entry_points should only be called once due to lru_cache
        mock_eps.assert_called_once()

    def test_clear_cache_allows_rediscovery(self):
        plugin = _make_plugin()
        ep = _make_entry_point(plugin)
        with patch("importlib.metadata.entry_points", return_value=[ep]) as mock_eps:
            discover_provider_plugins()
            clear_plugin_cache()
            discover_provider_plugins()
        assert mock_eps.call_count == 2

    def test_entry_points_called_with_correct_group(self):
        with patch("importlib.metadata.entry_points", return_value=[]) as mock_eps:
            discover_provider_plugins()
        mock_eps.assert_called_once_with(group="gptme.providers")


# ---------------------------------------------------------------------------
# get_provider_plugin / is_plugin_provider
# ---------------------------------------------------------------------------


class TestGetProviderPlugin:
    def test_returns_plugin_by_name(self):
        plugin = _make_plugin(name="myprovider")
        with patch(
            "importlib.metadata.entry_points", return_value=[_make_entry_point(plugin)]
        ):
            result = get_provider_plugin("myprovider")
        assert result is not None
        assert result.name == "myprovider"

    def test_returns_none_for_unknown_name(self):
        with patch("importlib.metadata.entry_points", return_value=[]):
            result = get_provider_plugin("nonexistent")
        assert result is None

    def test_is_plugin_provider_true(self):
        plugin = _make_plugin(name="myprovider")
        with patch(
            "importlib.metadata.entry_points", return_value=[_make_entry_point(plugin)]
        ):
            assert is_plugin_provider("myprovider") is True

    def test_is_plugin_provider_false(self):
        with patch("importlib.metadata.entry_points", return_value=[]):
            assert is_plugin_provider("openai") is False
            assert is_plugin_provider("nonexistent") is False


# ---------------------------------------------------------------------------
# get_plugin_api_keys
# ---------------------------------------------------------------------------


class TestGetPluginApiKeys:
    def test_returns_mapping(self):
        p1 = _make_plugin(name="a", api_key_env="A_API_KEY")
        p2 = _make_plugin(name="b", api_key_env="B_API_KEY")
        eps = [_make_entry_point(p1), _make_entry_point(p2)]
        with patch("importlib.metadata.entry_points", return_value=eps):
            keys = get_plugin_api_keys()
        assert keys == {"a": "A_API_KEY", "b": "B_API_KEY"}

    def test_empty_when_no_plugins(self):
        with patch("importlib.metadata.entry_points", return_value=[]):
            keys = get_plugin_api_keys()
        assert keys == {}


# ---------------------------------------------------------------------------
# get_model() integration
# ---------------------------------------------------------------------------


class TestGetModelWithPlugin:
    def test_get_model_resolves_plugin_model(self):
        from gptme.llm.models.resolution import get_model

        plugin = _make_plugin(
            name="myprovider",
            models=[
                ModelMeta(
                    provider="unknown",
                    model="myprovider/fast-model",
                    context=64_000,
                    max_output=8_000,
                    supports_vision=True,
                )
            ],
        )
        with patch(
            "importlib.metadata.entry_points", return_value=[_make_entry_point(plugin)]
        ):
            result = get_model("myprovider/fast-model")

        assert result.context == 64_000
        assert result.max_output == 8_000
        assert result.supports_vision is True

    def test_get_model_fallback_for_unlisted_plugin_model(self):
        """A model that belongs to a plugin provider but isn't in plugin.models falls back to 128k context."""
        from gptme.llm.models.resolution import get_model

        plugin = _make_plugin(name="myprovider", models=[])
        with patch(
            "importlib.metadata.entry_points", return_value=[_make_entry_point(plugin)]
        ):
            result = get_model("myprovider/unknown-model")

        assert result.context == 128_000
        assert result.model == "myprovider/unknown-model"

    def test_get_model_does_not_match_other_provider_suffix(self):
        """Suffix fallback should only match models registered for the same plugin prefix."""
        from gptme.llm.models.resolution import get_model

        plugin = _make_plugin(
            name="myprovider",
            models=[
                ModelMeta(
                    provider="unknown",
                    model="otherprovider/fast-model",
                    context=64_000,
                )
            ],
        )
        with patch(
            "importlib.metadata.entry_points", return_value=[_make_entry_point(plugin)]
        ):
            result = get_model("myprovider/fast-model")

        assert result.context == 128_000
        assert result.model == "myprovider/fast-model"


class TestPluginRouting:
    def test_init_llm_allows_custom_plugin_init_that_registers_client(self):
        from gptme.llm import init_llm
        from gptme.llm.models import CustomProvider

        client_registered = False

        def init_fn(_config):
            nonlocal client_registered
            client_registered = True

        plugin = _make_plugin(name="myprovider", init=init_fn)

        with (
            patch(
                "importlib.metadata.entry_points",
                return_value=[_make_entry_point(plugin)],
            ),
            patch(
                "gptme.llm.llm_openai.has_client",
                side_effect=lambda _provider: client_registered,
            ),
            patch("gptme.llm.llm_openai.init") as mock_init_openai,
        ):
            init_llm(CustomProvider("myprovider"))

        assert client_registered is True
        mock_init_openai.assert_not_called()

    def test_init_llm_rejects_custom_plugin_init_without_client_registration(self):
        from gptme.llm import init_llm
        from gptme.llm.models import CustomProvider

        init_fn = MagicMock()
        plugin = _make_plugin(name="myprovider", init=init_fn)

        with (
            patch(
                "importlib.metadata.entry_points",
                return_value=[_make_entry_point(plugin)],
            ),
            patch("gptme.llm.llm_openai.has_client", return_value=False),
            patch("gptme.llm.llm_openai.init") as mock_init_openai,
            pytest.raises(
                RuntimeError,
                match="did not register an OpenAI-compatible client",
            ),
        ):
            init_llm(CustomProvider("myprovider"))

        init_fn.assert_called_once()
        mock_init_openai.assert_not_called()

    def test_chat_complete_routes_plugin_provider_through_openai(self):
        from gptme.llm import _chat_complete

        plugin = _make_plugin(name="myprovider")
        messages = [Message("user", "hello")]
        expected = ("ok", {"model": "myprovider/test-model-v1"})

        with (
            patch(
                "importlib.metadata.entry_points",
                return_value=[_make_entry_point(plugin)],
            ),
            patch(
                "gptme.llm.llm_openai.chat", return_value=expected
            ) as mock_chat_openai,
        ):
            result = _chat_complete(messages, "myprovider/test-model-v1", None)

        assert result == expected
        mock_chat_openai.assert_called_once_with(
            messages,
            "myprovider/test-model-v1",
            None,
            output_schema=None,
            max_tokens=None,
        )

    def test_stream_routes_plugin_provider_through_openai(self):
        from gptme.llm import _stream

        plugin = _make_plugin(name="myprovider")
        messages = [Message("user", "hello")]

        def fake_stream(*args, **kwargs):
            yield "chunk-1"
            return {"model": "myprovider/test-model-v1"}

        with (
            patch(
                "importlib.metadata.entry_points",
                return_value=[_make_entry_point(plugin)],
            ),
            patch(
                "gptme.llm.llm_openai.stream", side_effect=fake_stream
            ) as mock_stream_openai,
        ):
            stream = _stream(messages, "myprovider/test-model-v1", None)
            chunks = list(stream)

        assert chunks == ["chunk-1"]
        assert stream.metadata == {"model": "myprovider/test-model-v1"}
        mock_stream_openai.assert_called_once_with(
            messages,
            "myprovider/test-model-v1",
            None,
            output_schema=None,
            max_tokens=None,
        )


class TestPluginModelListing:
    """Tests that plugin provider models appear in model listing commands."""

    def test_get_model_list_includes_plugin_models(self):
        """Plugin provider models must appear in get_model_list() output."""
        from gptme.llm.models.listing import get_model_list

        plugin = _make_plugin(name="myprovider")
        with patch(
            "importlib.metadata.entry_points", return_value=[_make_entry_point(plugin)]
        ):
            models = get_model_list(dynamic_fetch=False)

        model_names = [m.model for m in models]
        assert "myprovider/test-model-v1" in model_names

    def test_get_models_for_provider_returns_plugin_models_directly(self):
        """_get_models_for_provider() should return plugin.models for plugin providers."""
        from gptme.llm.models.listing import _get_models_for_provider
        from gptme.llm.models.types import CustomProvider

        plugin = _make_plugin(name="myprovider")
        with patch(
            "importlib.metadata.entry_points", return_value=[_make_entry_point(plugin)]
        ):
            result = _get_models_for_provider(CustomProvider("myprovider"))

        assert len(result) == 1
        assert result[0].model == "myprovider/test-model-v1"

    def test_get_model_list_plugin_filter_by_provider(self):
        """provider_filter should work correctly for plugin providers."""
        from gptme.llm.models.listing import get_model_list

        plugin = _make_plugin(name="myprovider")
        with patch(
            "importlib.metadata.entry_points", return_value=[_make_entry_point(plugin)]
        ):
            models = get_model_list(provider_filter="myprovider", dynamic_fetch=False)

        assert len(models) == 1
        assert models[0].model == "myprovider/test-model-v1"
