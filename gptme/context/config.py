"""Unified context configuration."""

from dataclasses import dataclass, field
from typing import Any

from .selector.config import ContextSelectorConfig


@dataclass
class ContextConfig:
    """Unified configuration for context management.

    Structure:
        [context]
        enabled = true  # Master switch (replaces GPTME_FRESH)

        [context.selector]  # Nested ContextSelectorConfig
        enabled = true
        strategy = "hybrid"
        max_candidates = 30
        ...
    """

    # Master switch - replaces GPTME_FRESH env var
    enabled: bool = False  # Default: opt-in

    # Nested selector configuration
    selector: ContextSelectorConfig = field(default_factory=ContextSelectorConfig)

    # Context-scout pre-pass model (None = disabled)
    # When set, a cheap model identifies relevant files before each user turn.
    # Example: "openai/gpt-4.1-mini" or "anthropic/claude-haiku-4-5-20251001"
    # See: https://github.com/gptme/gptme/issues/3652
    scout_model: str | None = None

    @classmethod
    def from_dict(cls, config_dict: dict[str, Any]) -> "ContextConfig":
        """Create config from dictionary (typically from gptme.toml).

        Example::

            config = ContextConfig.from_dict({
                'enabled': True,
                'scout_model': 'openai/gpt-4.1-mini',
                'selector': {
                    'enabled': True,
                    'strategy': 'hybrid',
                    'max_candidates': 30,
                }
            })
        """
        # Extract selector config if present
        selector_dict = config_dict.get("selector", {})
        selector = (
            ContextSelectorConfig.from_dict(selector_dict)
            if selector_dict
            else ContextSelectorConfig()  # Use default instead of None
        )

        return cls(
            enabled=config_dict.get("enabled", False),
            scout_model=config_dict.get("scout_model"),
            selector=selector,
        )
