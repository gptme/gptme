"""Configuration for context compression."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class CompressionConfig:
    """Configuration for context compression."""

    enabled: bool = False
    compressor: Literal["extractive", "llmlingua", "hybrid"] = "extractive"
    mode: Literal["fixed", "adaptive"] = "fixed"  # Compression mode
    target_ratio: float = 0.7  # 30% reduction (used in fixed mode)
    min_section_length: int = 100  # Minimum chars to compress
    preserve_code: bool = True  # Keep code blocks intact
    preserve_headings: bool = True  # Keep markdown headings

    # Extractive-specific config
    embedding_model: str = "all-MiniLM-L6-v2"

    # Adaptive mode config
    log_analysis: bool = True  # Log task analysis decisions

    # Adaptive mode thresholds and ratio ranges
    complexity_thresholds: dict[str, float] = field(
        default_factory=lambda: {
            "focused": 0.3,  # Tasks with score < 0.3
            "architecture": 0.7,  # Tasks with score > 0.7
        }
    )
    ratio_ranges: dict[str, tuple[float, float]] = field(
        default_factory=lambda: {
            "focused": (0.10, 0.20),  # Aggressive compression for simple tasks
            "mixed": (0.20, 0.30),  # Moderate compression for mixed tasks
            "architecture": (0.30, 0.50),  # Conservative for architecture-heavy
        }
    )
    manual_override_ratio: float | None = (
        None  # Force specific ratio (ignores analysis)
    )

    @classmethod
    def from_dict(cls, config: dict) -> "CompressionConfig":
        """Create config from dictionary."""
        return cls(**{k: v for k, v in config.items() if k in cls.__annotations__})
