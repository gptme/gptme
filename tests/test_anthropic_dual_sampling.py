"""Tests for Anthropic dual sampling detection."""

from unittest.mock import MagicMock

import pytest


# Test the _supports_dual_sampling function
class TestSupportsDualSampling:
    """Tests for _supports_dual_sampling function."""

    @pytest.fixture
    def mock_model_meta(self):
        """Create a mock ModelMeta."""
        meta = MagicMock()
        meta.supports_reasoning = False
        return meta

    @pytest.fixture
    def mock_model_meta_reasoning(self):
        """Create a mock ModelMeta with reasoning support."""
        meta = MagicMock()
        meta.supports_reasoning = True
        return meta

    def test_reasoning_model_no_dual_sampling(self, mock_model_meta_reasoning):
        """Reasoning models should not support dual sampling."""
        from gptme.llm.llm_anthropic import _supports_dual_sampling

        result = _supports_dual_sampling(
            "claude-3-7-sonnet-20250219", mock_model_meta_reasoning
        )
        assert result is False

    def test_known_model_supports_dual_sampling(self, mock_model_meta):
        """Known models in MODELS dict should support dual sampling."""
        from gptme.llm.llm_anthropic import _supports_dual_sampling

        # claude-3-5-sonnet-20241022 is a known model without reasoning
        result = _supports_dual_sampling("claude-3-5-sonnet-20241022", mock_model_meta)
        assert result is True

    def test_unknown_model_conservative_no_dual_sampling(self, mock_model_meta):
        """Unknown models should default to conservative behavior (no dual sampling)."""
        from gptme.llm.llm_anthropic import _supports_dual_sampling

        # A completely unknown model name
        result = _supports_dual_sampling("claude-unknown-model-xyz", mock_model_meta)
        assert result is False

    def test_date_suffix_variant_matches_base(self, mock_model_meta):
        """Model with date suffix should match base model configuration."""
        from gptme.llm.llm_anthropic import _supports_dual_sampling

        # claude-3-5-sonnet with a different date suffix
        # Should match claude-3-5-sonnet-20241022 (non-reasoning model)
        result = _supports_dual_sampling("claude-3-5-sonnet-20251231", mock_model_meta)
        assert result is True

    def test_reasoning_variant_no_dual_sampling(self, mock_model_meta):
        """Variant of a reasoning model should inherit reasoning restriction."""
        from gptme.llm.llm_anthropic import _supports_dual_sampling

        # claude-3-7-sonnet is a reasoning model
        # A variant with different date should also not support dual sampling
        mock_meta_for_variant = MagicMock()
        mock_meta_for_variant.supports_reasoning = (
            False  # Fallback doesn't know it's reasoning
        )

        result = _supports_dual_sampling(
            "claude-3-7-sonnet-20251231", mock_meta_for_variant
        )
        # Should match claude-3-7-sonnet base and inherit its reasoning config
        assert result is False

    def test_new_claude_model_format(self, mock_model_meta):
        """New Claude model naming format should be handled conservatively."""
        from gptme.llm.llm_anthropic import _supports_dual_sampling

        # Models like "claude-sonnet-4-5-20250929" don't match existing patterns
        result = _supports_dual_sampling("claude-sonnet-4-5-20250929", mock_model_meta)
        # Should be conservative since it doesn't match known patterns
        assert result is False
