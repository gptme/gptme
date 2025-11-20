"""Compression plugin for context management.

Implements context compression through reasoning stripping and other techniques.
"""

from typing import Any

from ..compress import strip_reasoning
from ..plugin import ContextPlugin, TransformResult


class CompressionPlugin(ContextPlugin):
    """Plugin for compressing context by stripping reasoning tags.

    Removes <think> and <thinking> blocks from content to reduce token usage
    while preserving substantive content.
    """

    @property
    def name(self) -> str:
        """Return plugin name."""
        return "compression"

    def transform(self, content: str, context: dict[str, Any]) -> TransformResult:
        """Transform content by stripping reasoning tags.

        Args:
            content: Content to compress
            context: Context dict with optional 'model' key

        Returns:
            TransformResult with compressed content and token savings
        """
        model = context.get("model", "gpt-4")
        stripped_content, tokens_saved = strip_reasoning(content, model)

        return TransformResult(
            content=stripped_content,
            tokens_saved=tokens_saved,
            metadata={"compression_method": "reasoning_stripping"},
        )

    def get_config_schema(self) -> dict[str, Any]:
        """Return configuration schema for compression plugin."""
        return {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Model name for token counting",
                    "default": "gpt-4",
                }
            },
        }

    def validate_config(self, config: dict[str, Any]) -> bool:
        """Validate compression plugin configuration."""
        if "model" in config and not isinstance(config["model"], str):
            return False
        return True
