"""
Tests for model-aware eval format routing.

Verifies that fable-5 and haiku-4.5 are routed from markdown→tool when
no explicit format is specified, while other models and explicit @format
specs are unaffected.
"""

import importlib
from unittest.mock import patch

import pytest
from click.testing import CliRunner, Result

from gptme.eval.main import main
from gptme.eval.types import (
    DEFAULT_TOOL_FORMAT_MODELS,
    ModelConfig,
    get_effective_format,
)


class TestGetEffectiveFormat:
    """Unit tests for get_effective_format()."""

    @pytest.mark.parametrize("model", sorted(DEFAULT_TOOL_FORMAT_MODELS))
    def test_markdown_routed_to_tool_for_affected_models(self, model: str):
        """Affected models get markdown→tool routing."""
        result = get_effective_format(model, "markdown")
        assert result == "tool", (
            f"{model} should be routed to 'tool' but got '{result}'"
        )

    @pytest.mark.parametrize("model", sorted(DEFAULT_TOOL_FORMAT_MODELS))
    @pytest.mark.parametrize("fmt", ["tool", "xml"])
    def test_non_markdown_formats_unchanged_for_affected_models(
        self, model: str, fmt: str
    ):
        """Non-markdown formats are never overridden, even for affected models."""
        result = get_effective_format(model, fmt)  # type: ignore[arg-type]
        assert result == fmt

    @pytest.mark.parametrize("fmt", ["markdown", "tool", "xml"])
    def test_unaffected_model_unchanged(self, fmt: str):
        """Models not in DEFAULT_TOOL_FORMAT_MODELS are always passed through."""
        result = get_effective_format("claude-sonnet-4-6", fmt)  # type: ignore[arg-type]
        assert result == fmt

    @pytest.mark.parametrize(
        "model",
        [
            "anthropic/claude-haiku-4-5",
            "openrouter/anthropic/claude-haiku-4-5-20251001",
            "openrouter/anthropic/claude-haiku-4.5",
        ],
    )
    def test_partial_model_name_match(self, model: str):
        """Routing handles provider prefixes, dated IDs, and punctuation variants."""
        assert get_effective_format(model, "markdown") == "tool"

    def test_unrelated_model_with_markdown(self):
        """A model whose name contains no affected substring keeps markdown."""
        result = get_effective_format("gpt-5.6-sol", "markdown")
        assert result == "markdown"


class TestModelConfigExpansion:
    """Integration tests: verify ModelConfig objects created in the expansion path."""

    def test_affected_model_no_explicit_format_gets_tool(self):
        """Simulate what main.py does in the auto-expansion path."""
        formats = ["markdown", "xml", "tool"]
        model = "claude-fable-5"
        configs = [
            ModelConfig(
                model=model,
                tool_format=get_effective_format(model, fmt),  # type: ignore[arg-type]
            )
            for fmt in formats
        ]
        tool_formats = [c.tool_format for c in configs]
        # Routing itself maps markdown → tool; the CLI deduplicates this expansion.
        assert tool_formats == ["tool", "xml", "tool"]

    @staticmethod
    def invoke_cli(*args: str) -> tuple[Result, list[ModelConfig]]:
        """Invoke the eval CLI without executing paid model calls."""
        eval_main = importlib.import_module("gptme.eval.main")
        captured_configs: list[ModelConfig] = []

        def fake_run_evals(_evals, model_configs, *_args, **_kwargs):
            captured_configs.extend(model_configs)
            return {}

        with (
            patch.object(eval_main, "run_evals", side_effect=fake_run_evals),
            patch.object(eval_main, "print_model_results", return_value=None),
            patch.object(eval_main, "print_model_results_table", return_value=None),
            patch.object(eval_main, "write_results", return_value=None),
        ):
            result = CliRunner().invoke(main, ["hello", *args])

        return result, captured_configs

    def test_affected_model_cli_expansion_has_no_duplicate_paid_runs(self):
        """The CLI sends each routed model/format combination to run_evals once."""
        result, captured_configs = self.invoke_cli("--model", "claude-fable-5")

        assert result.exit_code == 0, result.output
        assert captured_configs == [
            ModelConfig("claude-fable-5", "tool"),
            ModelConfig("claude-fable-5", "xml"),
        ]

    def test_repeated_and_explicit_specs_have_no_duplicate_paid_runs(self):
        """Deduplication applies across all model specifications."""
        result, captured_configs = self.invoke_cli(
            "--model",
            "claude-fable-5",
            "--model",
            "claude-fable-5@tool",
        )

        assert result.exit_code == 0, result.output
        assert captured_configs == [
            ModelConfig("claude-fable-5", "tool"),
            ModelConfig("claude-fable-5", "xml"),
        ]

    def test_explicit_tool_format_flag_bypasses_routing(self):
        """An explicit --tool-format value is never rewritten."""
        result, captured_configs = self.invoke_cli(
            "--model",
            "claude-fable-5",
            "--tool-format",
            "markdown",
        )

        assert result.exit_code == 0, result.output
        assert captured_configs == [ModelConfig("claude-fable-5", "markdown")]

    def test_explicit_at_format_spec_bypasses_routing(self):
        """Explicit model@format spec is NOT routed (handled before expansion)."""
        # This tests the design contract: from_spec with explicit @markdown must
        # NOT apply routing (routing only happens in the expansion path in main.py).
        mc = ModelConfig.from_spec("claude-fable-5@markdown")
        assert mc.model == "claude-fable-5"
        assert mc.tool_format == "markdown"  # explicit → no routing

    def test_unaffected_model_unchanged_in_expansion(self):
        """Unaffected model keeps all three formats in expansion."""
        formats = ["markdown", "xml", "tool"]
        model = "claude-sonnet-4-6"
        configs = [
            ModelConfig(
                model=model,
                tool_format=get_effective_format(model, fmt),  # type: ignore[arg-type]
            )
            for fmt in formats
        ]
        tool_formats = [c.tool_format for c in configs]
        assert tool_formats == ["markdown", "xml", "tool"]


class TestDefaultToolFormatModels:
    """Verify the constant itself is sane."""

    def test_known_models_present(self):
        assert "claude-fable-5" in DEFAULT_TOOL_FORMAT_MODELS
        assert "claude-haiku-4-5" in DEFAULT_TOOL_FORMAT_MODELS
        assert "claude-haiku-4.5" in DEFAULT_TOOL_FORMAT_MODELS

    def test_sonnet_not_in_set(self):
        # Sonnet has high pass rate on tool format but no systematic markdown failure
        assert "claude-sonnet-4-6" not in DEFAULT_TOOL_FORMAT_MODELS
