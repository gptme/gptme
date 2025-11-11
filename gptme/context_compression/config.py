"""Configuration for context compression."""

from dataclasses import dataclass
from typing import Literal


@dataclass
class CompressionConfig:
    """Configuration for context compression."""

    enabled: bool = False
    compressor: Literal["extractive", "llmlingua", "hybrid"] = "extractive"
    target_ratio: float = 0.7  # 30% reduction
    min_section_length: int = 100  # Minimum chars to compress
    preserve_code: bool = True  # Keep code blocks intact
    preserve_headings: bool = True  # Keep markdown headings

    # Extractive-specific config
    embedding_model: str = "all-MiniLM-L6-v2"

    @classmethod
    def from_dict(cls, config: dict) -> "CompressionConfig":
        """Create config from dictionary."""
        return cls(**{k: v for k, v in config.items() if k in cls.__annotations__})
