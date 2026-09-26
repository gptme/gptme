"""Unified context configuration."""

import math
from dataclasses import dataclass, field
from typing import Any

from .selector.config import ContextSelectorConfig


def parse_context_budget(
    value: object, field_name: str = "context.budget"
) -> float | int:
    """Validate and normalize a fractional or absolute context budget."""
    error = (
        f"{field_name} must be a fraction (0<x≤1) or absolute token count "
        f"(>1), got {value!r}"
    )
    if isinstance(value, bool):
        raise ValueError(error)
    if not isinstance(value, int | float | str):
        raise ValueError(error)
    try:
        parsed = float(value)
    except ValueError as e:
        raise ValueError(error) from e
    if (
        not math.isfinite(parsed)
        or parsed <= 0
        or (parsed > 1 and not parsed.is_integer())
    ):
        raise ValueError(error)
    return int(parsed) if parsed > 1 else parsed


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
    # - None (default): min(0.9 × window, window − max_output − headroom, 256k),
    #   unless model metadata declares an evidence-backed override
    # - float 0 < x ≤ 1: fraction of the model context window (e.g. 0.85)
    # - int > 1: absolute token count (e.g. 300000)
    # Can also be set via GPTME_CONTEXT_BUDGET env var.
    # GPTME_CONTEXT_LENGTH overrides the *window* for local models; this controls the *budget*.
    budget: float | int | None = None

    # Optional instructions appended to the compaction checkpoint prompt.
    # Set via [context] compact_instructions = "..." in gptme.toml.
    # Also appended when the user invokes /compact <instructions>.
    compact_instructions: str | None = None

    # Tokens of recent history to keep verbatim after the checkpoint (Phase 2).
    # Default 20000 ≈ Claude Code / OpenCode's keep-recent window.
    # Set 0 to disable (checkpoint only, no recent tail).
    keep_recent_tokens: int = 20_000

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
        budget = parse_context_budget(budget_raw) if budget_raw is not None else None

        keep_recent_raw = config_dict.get("keep_recent_tokens", 20_000)
        try:
            keep_recent = int(keep_recent_raw)
            if keep_recent < 0:
                raise ValueError
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"context.keep_recent_tokens must be a non-negative integer, got {keep_recent_raw!r}"
            ) from e

        return cls(
            enabled=config_dict.get("enabled", False),
            scout_model=config_dict.get("scout_model"),
            selector=selector,
            budget=budget,
            compact_instructions=config_dict.get("compact_instructions"),
            keep_recent_tokens=keep_recent,
        )
