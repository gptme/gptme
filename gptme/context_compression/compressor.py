"""Base compressor interface and implementations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CompressionResult:
    """Result of compression operation."""

    compressed: str
    original_length: int
    compressed_length: int
    compression_ratio: float


class ContextCompressor(ABC):
    """Abstract base class for context compressors."""

    @abstractmethod
    def compress(
        self,
        content: str,
        target_ratio: float = 0.7,
        context: str = "",
    ) -> CompressionResult:
        """
        Compress content preserving key information.

        Args:
            content: Text to compress
            target_ratio: Target compression ratio (0.7 = 30% reduction)
            context: Current conversation context for relevance scoring

        Returns:
            CompressionResult with compressed text and metrics
        """
        pass
