"""Unified context configuration."""

from dataclasses import dataclass

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
    selector: ContextSelectorConfig = None  # type: ignore

    def __post_init__(self):
        # Default selector config if not provided
        if self.selector is None:
            self.selector = ContextSelectorConfig()

    @classmethod
    def from_dict(cls, config_dict: dict) -> "ContextConfig":
        """Create config from dictionary (typically from gptme.toml).

        Example:
            config = ContextConfig.from_dict({
                'enabled': True,
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
            selector=selector,
        )
