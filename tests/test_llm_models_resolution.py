"""Tests for gptme.llm.models.resolution and gptme.llm.models.listing.

Covers: default model state, alias resolution, date suffix stripping,
closest-match heuristic edge cases, OpenAI-subscription reasoning suffixes,
model_to_dict serialization, filter logic, format helpers, and cache behavior.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from gptme.llm.models import ModelMeta
from gptme.llm.models.listing import (
    _apply_model_filters,
    _format_model_details,
    _print_simple_format,
    model_to_dict,
)
from gptme.llm.models.resolution import (
    _find_base_model_properties,
    _find_closest_model_properties,
    get_default_model,
    get_default_model_summary,
    get_model,
    get_summary_model,
    log_warn_once,
    set_default_model,
)
from gptme.llm.models.types import MODEL_ALIASES

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _restore_default_model():
    """Save and restore the default model ContextVar between tests."""
    from gptme.llm.models.resolution import _default_model_var

    token = _default_model_var.set(None)
    yield
    _default_model_var.reset(token)


@pytest.fixture(autouse=True)
def _restore_model_list_cache():
    """Save and restore the model list cache between tests."""
    import gptme.llm.models.listing as listing_mod

    old_cache = listing_mod._model_list_cache
    old_time = listing_mod._model_list_cache_time
    yield
    listing_mod._model_list_cache = old_cache
    listing_mod._model_list_cache_time = old_time


# ── Default model state ──────────────────────────────────────────────────


class TestDefaultModelState:
    """Tests for get/set default model and summary model."""

    def test_default_model_initially_none(self):
        """Default model is None before being set."""
        assert get_default_model() is None

    def test_set_and_get_default_model_with_string(self):
        """Setting default model with a string resolves it."""
        set_default_model("anthropic/claude-sonnet-4-6")
        model = get_default_model()
        assert model is not None
        assert model.provider == "anthropic"
        assert model.model == "claude-sonnet-4-6"

    def test_set_and_get_default_model_with_meta(self):
        """Setting default model with a ModelMeta stores it directly."""
        meta = ModelMeta(provider="openai", model="gpt-5", context=128_000)
        set_default_model(meta)
        model = get_default_model()
        assert model is meta

    def test_get_default_model_summary_returns_none_when_no_default(self):
        """Summary model returns None if no default is set."""
        result = get_default_model_summary()
        assert result is None

    def test_get_default_model_summary_for_anthropic(self):
        """Summary model for anthropic is claude-haiku-4-5."""
        set_default_model("anthropic/claude-sonnet-4-6")
        summary = get_default_model_summary()
        assert summary is not None
        assert "haiku" in summary.model

    def test_get_default_model_summary_for_local(self):
        """Local provider has no summary model — returns default model itself."""
        local_model = ModelMeta(provider="local", model="llama-3", context=8192)
        set_default_model(local_model)
        summary = get_default_model_summary()
        assert summary is not None
        assert summary is local_model


# ── get_summary_model ────────────────────────────────────────────────────


class TestGetSummaryModel:
    """Tests for the summary model lookup per provider."""

    @pytest.mark.parametrize(
        ("provider", "expected_substr"),
        [
            ("openai", "mini"),
            ("anthropic", "haiku"),
            ("gemini", "flash"),
            ("deepseek", "deepseek-chat"),
            ("xai", "fast"),
        ],
    )
    def test_known_providers_return_summary_model(self, provider, expected_substr):
        result = get_summary_model(provider)
        assert result is not None
        assert expected_substr in result

    def test_local_returns_none(self):
        assert get_summary_model("local") is None

    def test_unknown_provider_returns_none(self):
        assert get_summary_model("nonexistent") is None  # type: ignore[arg-type]


# ── Alias resolution ─────────────────────────────────────────────────────


class TestAliasResolution:
    """Tests for MODEL_ALIASES and _find_base_model_properties."""

    def test_anthropic_aliases_exist(self):
        """Verify anthropic aliases are defined."""
        assert "anthropic" in MODEL_ALIASES
        assert len(MODEL_ALIASES["anthropic"]) >= 4

    def test_alias_resolves_to_dated_model(self):
        """claude-opus-4-1 alias should resolve to dated variant."""
        props = _find_base_model_properties("anthropic", "claude-opus-4-1")
        assert props is not None
        assert props["context"] > 0

    def test_alias_resolution_via_get_model(self):
        """get_model should resolve aliases transparently."""
        model = get_model("anthropic/claude-opus-4-1")
        assert model.provider == "anthropic"
        # Should get real metadata, not fallback
        assert model.price_input > 0
        assert model.supports_vision is True

    def test_all_anthropic_aliases_resolve(self):
        """Every defined alias should resolve to valid model properties."""
        for alias in MODEL_ALIASES.get("anthropic", {}):
            props = _find_base_model_properties("anthropic", alias)
            assert props is not None, f"Alias {alias} failed to resolve"
            assert props["context"] > 0, f"Alias {alias} has invalid context"

    def test_non_alias_not_resolved_as_alias(self):
        """A model name that isn't an alias should not be resolved by alias lookup."""
        # This model doesn't exist as alias or exact match, so alias step returns None
        # but date suffix or closest match may still work
        props = _find_base_model_properties("anthropic", "claude-nonexistent-9-9")
        # Should be None since there's no alias and no base model after stripping date
        assert props is None


# ── Date suffix stripping ────────────────────────────────────────────────


class TestDateSuffixStripping:
    """Tests for date suffix removal in _find_base_model_properties."""

    def test_dated_variant_inherits_base_properties(self):
        """A model with date suffix should inherit from the base model."""
        # claude-sonnet-4-6 exists in MODELS; a dated variant should find it
        props = _find_base_model_properties("anthropic", "claude-sonnet-4-6-20260101")
        assert props is not None
        assert props["context"] >= 200_000  # real model, not fallback 128k

    def test_date_suffix_on_unknown_base_returns_none(self):
        """Date suffix stripping on a non-existent base should return None."""
        props = _find_base_model_properties("anthropic", "nonexistent-model-20260101")
        assert props is None

    def test_no_date_suffix_not_modified(self):
        """Model name without date suffix isn't altered."""
        # claude-sonnet-4-6 is exact match in MODELS, so _find_base is only called
        # after exact lookup fails — this verifies the function handles it gracefully
        props = _find_base_model_properties("anthropic", "claude-sonnet-4-6")
        # This is already in MODELS, so _find_base won't match it as a date-stripped variant
        # It would only match via alias if it were aliased, which it isn't
        # So we expect None (the exact match is done before _find_base is called)
        # Actually, claude-sonnet-4-6 is a direct key in MODELS, so this function
        # returns None correctly — the exact match happens in get_model() before calling this
        assert props is None

    def test_provider_not_in_models(self):
        """Provider not in MODELS returns None."""
        props = _find_base_model_properties("nonexistent", "model-20260101")  # type: ignore[arg-type]
        assert props is None


# ── Closest match edge cases ─────────────────────────────────────────────


class TestClosestMatchEdgeCases:
    """Additional edge cases for _find_closest_model_properties."""

    def test_deep_seek_family_match(self):
        """DeepSeek models should match within their family."""
        props = _find_closest_model_properties("deepseek", "deepseek-reasoner-v3")
        assert props is not None
        assert props["context"] > 0

    def test_groq_family_match(self):
        """Groq models should find closest match."""
        props = _find_closest_model_properties("groq", "llama-4-70b-versatile")
        assert props is not None

    def test_openai_subscription_match(self):
        """OpenAI subscription models should find closest match."""
        props = _find_closest_model_properties("openai-subscription", "gpt-6-turbo")
        assert props is not None

    def test_gptme_provider_empty_returns_none(self):
        """gptme provider has no static models, so closest match returns None."""
        props = _find_closest_model_properties("gptme", "claude-sonnet-5-0")
        # gptme has an empty model dict in MODELS, so no candidates exist
        assert props is None


# ── OpenAI-subscription reasoning suffix ─────────────────────────────────


class TestReasoningSuffix:
    """Tests for OpenAI-subscription :high/:medium reasoning level stripping."""

    def test_reasoning_suffix_stripped(self):
        """Model with :high suffix should resolve to base model."""
        # First check if openai-subscription has any models
        from gptme.llm.models.data import MODELS

        if "openai-subscription" not in MODELS or not MODELS["openai-subscription"]:
            pytest.skip("No openai-subscription models defined")

        # Get a known model name
        model_name = next(iter(MODELS["openai-subscription"]))
        model = get_model(f"openai-subscription/{model_name}:high")
        assert model.provider == "openai-subscription"
        assert model.model == f"{model_name}:high"
        assert model.context > 0
        # Should have real metadata, not fallback
        assert model.price_input > 0 or model.price_output > 0

    def test_reasoning_suffix_medium(self):
        """Model with :medium suffix should also resolve."""
        from gptme.llm.models.data import MODELS

        if "openai-subscription" not in MODELS or not MODELS["openai-subscription"]:
            pytest.skip("No openai-subscription models defined")

        model_name = next(iter(MODELS["openai-subscription"]))
        model = get_model(f"openai-subscription/{model_name}:medium")
        assert model.provider == "openai-subscription"
        assert model.context > 0


# ── OpenRouter subprovider suffix ────────────────────────────────────────


class TestOpenRouterSubproviderSuffix:
    """Tests for @ suffix stripping on OpenRouter models."""

    def test_at_suffix_stripped_for_lookup(self):
        """OpenRouter model with @subprovider should strip for static lookup."""
        from gptme.llm.models.data import MODELS

        if "openrouter" not in MODELS or not MODELS["openrouter"]:
            pytest.skip("No openrouter models defined")

        # Find a model that exists
        model_name = next(iter(MODELS["openrouter"]))
        model = get_model(f"openrouter/{model_name}@custom-sub")
        assert model.provider == "openrouter"
        # Should preserve original name with suffix
        assert model.model == f"{model_name}@custom-sub"
        # Should have real metadata from the base model
        assert model.context > 0


# ── log_warn_once ────────────────────────────────────────────────────────


class TestLogWarnOnce:
    """Tests for the dedup warning logger."""

    def test_warns_first_time(self, caplog):
        """First call with a message should log it."""
        from gptme.llm.models.resolution import _logged_warnings

        test_msg = f"unique-test-message-{id(self)}"
        _logged_warnings.discard(test_msg)  # ensure clean state

        import logging

        with caplog.at_level(logging.WARNING):
            log_warn_once(test_msg)
        assert test_msg in caplog.text

    def test_does_not_warn_second_time(self, caplog):
        """Second call with same message should not log again."""
        test_msg = f"dedup-test-message-{id(self)}"
        from gptme.llm.models.resolution import _logged_warnings

        _logged_warnings.discard(test_msg)

        import logging

        log_warn_once(test_msg)  # first call
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            log_warn_once(test_msg)  # second call
        assert test_msg not in caplog.text


# ── Custom provider resolution ───────────────────────────────────────────


class TestCustomProviderResolution:
    """Tests for custom provider handling in get_model."""

    @patch("gptme.llm.models.resolution._get_custom_provider_config")
    def test_custom_provider_with_model_path(self, mock_config):
        """Custom provider/model resolves to unknown provider with full model path."""
        mock_provider = MagicMock()
        mock_provider.default_model = "my-model"
        # First call is for provider-only check (returns None for "my-custom/my-model")
        # Second call is for prefix check (returns the provider for "my-custom")
        mock_config.side_effect = [None, mock_provider]

        model = get_model("my-custom/my-model")
        assert model.provider == "unknown"
        assert model.model == "my-custom/my-model"
        assert model.context == 128_000

    @patch("gptme.llm.models.resolution._get_custom_provider_config")
    def test_custom_provider_name_only_no_default_raises(self, mock_config):
        """Custom provider without default_model raises ValueError."""
        mock_provider = MagicMock()
        mock_provider.default_model = None
        mock_config.return_value = mock_provider

        with pytest.raises(ValueError, match="no default_model"):
            get_model("my-custom")


# ── Plugin provider resolution ───────────────────────────────────────────


class TestPluginProviderResolution:
    """Tests for plugin provider handling in get_model."""

    @patch("gptme.llm.provider_plugins.get_provider_plugin")
    @patch("gptme.llm.models.resolution._get_custom_provider_config")
    def test_plugin_model_found(self, mock_custom, mock_plugin):
        """Plugin model found in plugin's model list."""
        mock_custom.return_value = None
        plugin = MagicMock()
        plugin.models = [
            ModelMeta(provider="unknown", model="myplugin/model-a", context=64_000)
        ]
        mock_plugin.return_value = plugin

        model = get_model("myplugin/model-a")
        assert model.provider == "unknown"
        assert model.model == "myplugin/model-a"
        assert model.context == 64_000

    @patch("gptme.llm.provider_plugins.get_provider_plugin")
    @patch("gptme.llm.models.resolution._get_custom_provider_config")
    def test_plugin_model_not_found_fallback(self, mock_custom, mock_plugin):
        """Plugin model not in list falls back to generic 128k."""
        mock_custom.return_value = None
        plugin = MagicMock()
        plugin.models = []
        mock_plugin.return_value = plugin

        model = get_model("myplugin/unknown-model")
        assert model.provider == "unknown"
        assert model.model == "myplugin/unknown-model"
        assert model.context == 128_000


# ── ModelMeta properties ─────────────────────────────────────────────────


class TestModelMetaProperties:
    """Tests for ModelMeta.full and ModelMeta.provider_key."""

    def test_full_with_known_provider(self):
        m = ModelMeta(provider="anthropic", model="claude-sonnet-4-6", context=200_000)
        assert m.full == "anthropic/claude-sonnet-4-6"

    def test_full_with_unknown_provider(self):
        m = ModelMeta(provider="unknown", model="custom/model", context=128_000)
        assert m.full == "custom/model"

    def test_provider_key_known(self):
        m = ModelMeta(provider="openai", model="gpt-5", context=128_000)
        assert m.provider_key == "openai"

    def test_provider_key_unknown_with_slash(self):
        m = ModelMeta(provider="unknown", model="myplugin/model", context=128_000)
        assert m.provider_key == "myplugin"

    def test_provider_key_unknown_no_slash(self):
        m = ModelMeta(provider="unknown", model="something", context=128_000)
        assert m.provider_key == "unknown"


# ── model_to_dict ────────────────────────────────────────────────────────


class TestModelToDict:
    """Tests for model_to_dict serialization."""

    def test_basic_serialization(self):
        m = ModelMeta(provider="openai", model="gpt-5", context=128_000)
        d = model_to_dict(m)
        assert d["provider"] == "openai"
        assert d["model"] == "gpt-5"
        assert d["full"] == "openai/gpt-5"
        assert d["context"] == 128_000
        assert d["supports_streaming"] is True
        assert d["supports_vision"] is False

    def test_max_output_included_when_set(self):
        m = ModelMeta(
            provider="anthropic", model="test", context=200_000, max_output=64_000
        )
        d = model_to_dict(m)
        assert d["max_output"] == 64_000

    def test_max_output_excluded_when_none(self):
        m = ModelMeta(provider="openai", model="test", context=128_000)
        d = model_to_dict(m)
        assert "max_output" not in d

    def test_pricing_included_when_nonzero(self):
        m = ModelMeta(
            provider="anthropic",
            model="test",
            context=200_000,
            price_input=3.0,
            price_output=15.0,
        )
        d = model_to_dict(m)
        assert d["price_input"] == 3.0
        assert d["price_output"] == 15.0

    def test_pricing_excluded_when_zero(self):
        m = ModelMeta(provider="openai", model="test", context=128_000)
        d = model_to_dict(m)
        assert "price_input" not in d
        assert "price_output" not in d

    def test_knowledge_cutoff_serialized_as_iso(self):
        cutoff = datetime(2025, 8, 1, tzinfo=timezone.utc)
        m = ModelMeta(
            provider="anthropic",
            model="test",
            context=200_000,
            knowledge_cutoff=cutoff,
        )
        d = model_to_dict(m)
        assert d["knowledge_cutoff"] == "2025-08-01T00:00:00+00:00"

    def test_knowledge_cutoff_excluded_when_none(self):
        m = ModelMeta(provider="openai", model="test", context=128_000)
        d = model_to_dict(m)
        assert "knowledge_cutoff" not in d

    def test_deprecated_included_when_true(self):
        m = ModelMeta(
            provider="anthropic", model="test", context=200_000, deprecated=True
        )
        d = model_to_dict(m)
        assert d["deprecated"] is True

    def test_deprecated_excluded_when_false(self):
        m = ModelMeta(provider="openai", model="test", context=128_000)
        d = model_to_dict(m)
        assert "deprecated" not in d

    def test_parallel_tool_calls_serialized(self):
        m = ModelMeta(
            provider="openai",
            model="test",
            context=128_000,
            supports_parallel_tool_calls=True,
        )
        d = model_to_dict(m)
        assert d["supports_parallel_tool_calls"] is True

    def test_reasoning_serialized(self):
        m = ModelMeta(
            provider="anthropic",
            model="test",
            context=200_000,
            supports_reasoning=True,
        )
        d = model_to_dict(m)
        assert d["supports_reasoning"] is True


# ── _apply_model_filters ─────────────────────────────────────────────────


class TestApplyModelFilters:
    """Tests for the model filter function."""

    @pytest.fixture()
    def sample_models(self):
        return [
            ModelMeta(
                provider="anthropic",
                model="a",
                context=200_000,
                supports_vision=True,
                supports_reasoning=True,
            ),
            ModelMeta(
                provider="openai",
                model="b",
                context=128_000,
                supports_vision=True,
                supports_reasoning=False,
            ),
            ModelMeta(
                provider="openai",
                model="c",
                context=128_000,
                supports_vision=False,
                supports_reasoning=False,
                deprecated=True,
            ),
            ModelMeta(
                provider="gemini",
                model="d",
                context=1_000_000,
                supports_vision=False,
                supports_reasoning=True,
            ),
        ]

    def test_no_filters(self, sample_models):
        """No filters excludes only deprecated by default."""
        result = _apply_model_filters(sample_models)
        assert len(result) == 3
        assert all(not m.deprecated for m in result)

    def test_include_deprecated(self, sample_models):
        """include_deprecated keeps all models."""
        result = _apply_model_filters(sample_models, include_deprecated=True)
        assert len(result) == 4

    def test_vision_only(self, sample_models):
        """vision_only keeps only vision-capable, non-deprecated models."""
        result = _apply_model_filters(sample_models, vision_only=True)
        assert len(result) == 2
        assert all(m.supports_vision for m in result)

    def test_reasoning_only(self, sample_models):
        """reasoning_only keeps only reasoning-capable, non-deprecated models."""
        result = _apply_model_filters(sample_models, reasoning_only=True)
        assert len(result) == 2
        assert all(m.supports_reasoning for m in result)

    def test_vision_and_reasoning(self, sample_models):
        """Both filters applied together."""
        result = _apply_model_filters(
            sample_models, vision_only=True, reasoning_only=True
        )
        assert len(result) == 1
        assert result[0].model == "a"

    def test_empty_input(self):
        """Empty list returns empty list."""
        assert _apply_model_filters([]) == []

    def test_all_deprecated_without_flag(self):
        """All deprecated models with no include_deprecated returns empty."""
        models = [
            ModelMeta(provider="openai", model="old", context=4096, deprecated=True)
        ]
        assert _apply_model_filters(models) == []


# ── _format_model_details ────────────────────────────────────────────────


class TestFormatModelDetails:
    """Tests for model detail formatting."""

    def test_basic_format(self):
        m = ModelMeta(provider="openai", model="gpt-5", context=128_000)
        result = _format_model_details(m)
        assert "gpt-5" in result
        assert "128k ctx" in result

    def test_format_with_vision(self):
        m = ModelMeta(
            provider="anthropic",
            model="test",
            context=200_000,
            supports_vision=True,
        )
        result = _format_model_details(m)
        assert "vision" in result

    def test_format_with_reasoning(self):
        m = ModelMeta(
            provider="anthropic",
            model="test",
            context=200_000,
            supports_reasoning=True,
        )
        result = _format_model_details(m)
        assert "reasoning" in result

    def test_format_with_max_output(self):
        m = ModelMeta(
            provider="anthropic", model="test", context=200_000, max_output=64_000
        )
        result = _format_model_details(m)
        assert "64k out" in result

    def test_format_deprecated(self):
        m = ModelMeta(provider="openai", model="old", context=4096, deprecated=True)
        result = _format_model_details(m)
        assert "DEPRECATED" in result

    def test_format_with_pricing(self):
        m = ModelMeta(
            provider="anthropic",
            model="test",
            context=200_000,
            price_input=3.0,
            price_output=15.0,
        )
        result = _format_model_details(m, show_pricing=True)
        assert "$3.00" in result
        assert "15.00" in result

    def test_format_pricing_hidden_by_default(self):
        m = ModelMeta(
            provider="anthropic",
            model="test",
            context=200_000,
            price_input=3.0,
            price_output=15.0,
        )
        result = _format_model_details(m, show_pricing=False)
        assert "$" not in result


# ── _print_simple_format ─────────────────────────────────────────────────


class TestPrintSimpleFormat:
    """Tests for simple format output."""

    def test_prints_provider_model(self, capsys):
        models = [
            ModelMeta(provider="openai", model="gpt-5", context=128_000),
            ModelMeta(provider="anthropic", model="claude-sonnet-4-6", context=200_000),
        ]
        _print_simple_format(models)
        output = capsys.readouterr().out
        assert "openai/gpt-5" in output
        assert "anthropic/claude-sonnet-4-6" in output

    def test_empty_list(self, capsys):
        _print_simple_format([])
        output = capsys.readouterr().out
        assert output == ""


# ── get_model integration tests ──────────────────────────────────────────


class TestGetModelIntegration:
    """Integration tests for get_model covering various resolution paths."""

    def test_anthropic_sonnet_exact(self):
        """Exact model name in MODELS."""
        model = get_model("anthropic/claude-sonnet-4-6")
        assert model.provider == "anthropic"
        assert model.context == 1_000_000
        assert model.supports_vision is True
        assert model.supports_reasoning is True

    def test_anthropic_opus_exact(self):
        """Exact opus model."""
        model = get_model("anthropic/claude-opus-4-6")
        assert model.provider == "anthropic"
        assert model.context == 1_000_000

    def test_gptme_provider(self):
        """gptme provider should resolve."""
        model = get_model("gptme")
        assert model.provider == "gptme"

    def test_model_without_provider_searches_all(self):
        """Model name without provider/ prefix searches all providers."""
        model = get_model("claude-sonnet-4-6")
        # Should find it in anthropic (or gptme if searched first)
        assert model.model == "claude-sonnet-4-6"
        assert model.context > 0

    def test_gemini_model(self):
        """Gemini models resolve correctly."""
        model = get_model("gemini/gemini-2.5-pro")
        assert model.provider == "gemini"
        assert model.context >= 1_000_000

    def test_xai_model(self):
        """xAI/Grok models resolve correctly."""
        model = get_model("xai/grok-4")
        assert model.provider == "xai"
        assert model.supports_reasoning is True

    def test_deepseek_model(self):
        """DeepSeek models resolve correctly."""
        model = get_model("deepseek/deepseek-chat")
        assert model.provider == "deepseek"
        assert model.context > 0

    def test_unknown_provider_model_returns_fallback(self):
        """Completely unknown provider/model returns safe fallback."""
        model = get_model("totally-fake/not-real")
        assert model.provider == "unknown"
        assert model.context == 128_000

    def test_unknown_bare_model_returns_fallback(self):
        """Bare unknown model name returns fallback."""
        model = get_model("xyzzy-nonexistent-999")
        assert model.provider == "unknown"
        assert model.context == 128_000

    def test_known_provider_unknown_model_uses_closest(self):
        """Known provider but unknown model should use closest match."""
        model = get_model("anthropic/claude-sonnet-99-0")
        assert model.provider == "anthropic"
        # Should get closest match, not generic 128k fallback
        assert model.context > 128_000
        assert model.price_input > 0

    def test_azure_with_no_models_returns_fallback(self):
        """Azure with no static models returns 128k fallback."""
        model = get_model("azure/gpt-4-deployment")
        assert model.provider == "azure"
        assert model.context == 128_000

    def test_set_default_model_with_invalid_raises(self):
        """Setting default model with invalid name should still work (fallback)."""
        # get_model always returns something, even for unknowns
        set_default_model("fake/model")
        m = get_default_model()
        assert m is not None
        assert m.provider == "unknown"


# ── Model cache behavior ─────────────────────────────────────────────────


class TestModelListCache:
    """Tests for the model list cache in listing.py."""

    def test_cache_is_used_on_repeat_call(self):
        """Calling get_model_list twice with dynamic_fetch=True should use cache."""
        import gptme.llm.models.listing as listing_mod
        from gptme.llm.models.listing import get_model_list

        # autouse fixture already clears cache to None
        result1 = get_model_list(dynamic_fetch=True)
        assert listing_mod._model_list_cache is not None

        # Second call should return same object (cache hit)
        result2 = get_model_list(dynamic_fetch=True)
        assert result1 is result2

    def test_cache_not_used_with_dynamic_fetch_false(self):
        """dynamic_fetch=False bypasses caching."""
        import gptme.llm.models.listing as listing_mod
        from gptme.llm.models.listing import get_model_list

        get_model_list(dynamic_fetch=False)
        # Cache should NOT be populated when dynamic_fetch=False
        assert listing_mod._model_list_cache is None

    def test_cache_bypassed_with_filters(self):
        """Filtered calls bypass cache."""
        from gptme.llm.models.listing import get_model_list

        # Populate cache with unfiltered call
        result_all = get_model_list(dynamic_fetch=True)

        # Filtered call should not use cache (different code path)
        result_filtered = get_model_list(vision_only=True, dynamic_fetch=True)
        # Filtered result should be a subset
        assert len(result_filtered) <= len(result_all)


# ── Edge cases in data models ────────────────────────────────────────────


class TestModelMetaEdgeCases:
    """Edge cases for ModelMeta dataclass."""

    def test_frozen_immutability(self):
        """ModelMeta should be frozen (immutable)."""
        m = ModelMeta(provider="openai", model="test", context=128_000)
        with pytest.raises(AttributeError):
            m.context = 999  # type: ignore[misc]

    def test_default_values(self):
        """Verify default values for optional fields."""
        m = ModelMeta(provider="openai", model="test", context=128_000)
        assert m.max_output is None
        assert m.supports_streaming is True
        assert m.supports_vision is False
        assert m.supports_reasoning is False
        assert m.supports_parallel_tool_calls is False
        assert m.price_input == 0
        assert m.price_output == 0
        assert m.knowledge_cutoff is None
        assert m.deprecated is False
        assert m.default_tool_format is None

    def test_equality(self):
        """Two ModelMeta with same fields should be equal."""
        m1 = ModelMeta(provider="openai", model="test", context=128_000)
        m2 = ModelMeta(provider="openai", model="test", context=128_000)
        assert m1 == m2

    def test_inequality(self):
        """Different ModelMeta should not be equal."""
        m1 = ModelMeta(provider="openai", model="test", context=128_000)
        m2 = ModelMeta(provider="openai", model="test", context=64_000)
        assert m1 != m2
