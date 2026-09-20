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
        budget = 0.85   # Compaction budget: fraction (0<x≤1) or absolute tokens (>1)

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

    # Context budget: token count at which compaction is triggered.
    # - None (default): compute dynamically as min(0.9 × window, window − max_output − headroom)
    # - float 0 < x ≤ 1: fraction of the model context window (e.g. 0.85)
    # - int > 1: absolute token count (e.g. 300000)
    # Can also be set via GPTME_CONTEXT_BUDGET env var.
    # GPTME_CONTEXT_LENGTH overrides the *window* for local models; this controls the *budget*.
    budget: float | int | None = None

    @classmethod
    def from_dict(cls, config_dict: dict[str, Any]) -> "ContextConfig":
        """Create config from dictionary (typically from gptme.toml).

        Example::

            config = ContextConfig.from_dict({
                'enabled': True,
                'budget': 0.85,
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

        budget_raw = config_dict.get("budget")
        budget: float | int | None = None
        if budget_raw is not None:
            try:
                f = float(budget_raw)
                budget = int(f) if f > 1 else f
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"context.budget must be a fraction (0<x≤1) or absolute token count (>1), got {budget_raw!r}"
                ) from e

        return cls(
            enabled=config_dict.get("enabled", False),
            scout_model=config_dict.get("scout_model"),
            selector=selector,
            budget=budget,
        )
