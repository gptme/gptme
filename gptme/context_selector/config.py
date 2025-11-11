"""Configuration schema for context selector."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ContextSelectorConfig:
    """Configuration for context selection behavior.

    This configuration controls which selection strategy is used,
    cost limits, and strategy-specific parameters.
    """

    enabled: bool = True
    strategy: Literal["rule", "llm", "hybrid"] = "hybrid"
    llm_model: str = "openai/gpt-4o-mini"
    max_candidates: int = 20  # Pre-filter size for hybrid
    max_selected: int = 5  # Final selection size
    cost_limit_daily: float = 0.30  # Daily cost limit in USD ($9/month)

    # Lesson-specific configuration
    lesson_use_yaml_metadata: bool = True
    lesson_priority_boost: dict[str, float] = field(
        default_factory=lambda: {"high": 2.0, "critical": 3.0}
    )

    # File-specific configuration
    file_mention_weight: float = 2.0
    file_recency_weight: float = 1.0

    @classmethod
    def from_dict(cls, config_dict: dict) -> "ContextSelectorConfig":
        """Create config from dictionary (typically from gptme.toml)."""
        return cls(
            **{k: v for k, v in config_dict.items() if k in cls.__dataclass_fields__}
        )
