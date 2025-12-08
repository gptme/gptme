"""Adaptive context compressor using task complexity analysis.

This module provides adaptive compression that adjusts compression ratios
based on task complexity. It integrates the task analyzer to classify tasks
and select appropriate compression strategies.
"""

from dataclasses import dataclass
from pathlib import Path

from .task_analyzer import (
    TaskClassification,
    TaskFeatures,
    classify_task,
    extract_features,
    select_compression_ratio,
)


@dataclass
class CompressionResult:
    """Result of adaptive compression operation.

    Attributes:
        original_content: Original context content
        compressed_content: Compressed context content
        compression_ratio: Actual compression ratio achieved
        task_classification: Classification of the task
        rationale: Explanation of compression decisions
    """

    original_content: str
    compressed_content: str
    compression_ratio: float
    task_classification: TaskClassification
    rationale: str

    @property
    def tokens_saved(self) -> int:
        """Estimate tokens saved by compression."""
        original_len = len(self.original_content)
        compressed_len = len(self.compressed_content)
        # Rough estimate: 4 chars per token
        return (original_len - compressed_len) // 4


class AdaptiveCompressor:
    """Adaptive context compressor using task analysis.

    This compressor analyzes task complexity to select appropriate
    compression ratios, providing aggressive compression for simple
    tasks and conservative compression for complex architectural work.

    Example:
        >>> compressor = AdaptiveCompressor()
        >>> result = compressor.compress(
        ...     prompt="Fix the counter increment bug in utils.py",
        ...     context_files=["utils.py", "tests/test_utils.py"]
        ... )
        >>> print(f"Task type: {result.task_classification.primary_type}")
        >>> print(f"Ratio: {result.compression_ratio:.2f}")
        >>> print(f"Tokens saved: {result.tokens_saved}")
    """

    def __init__(
        self,
        workspace_root: Path | None = None,
        enable_logging: bool = True,
    ):
        """Initialize adaptive compressor.

        Args:
            workspace_root: Root directory of workspace for context analysis
            enable_logging: Whether to log compression decisions
        """
        self.workspace_root = workspace_root or Path.cwd()
        self.enable_logging = enable_logging

    def compress(
        self,
        prompt: str,
        context_files: list[str] | None = None,
        current_context: list[str] | None = None,
    ) -> CompressionResult:
        """Compress context adaptively based on task analysis.

        Args:
            prompt: Task prompt/description
            context_files: List of context items (file paths or content strings).
                The count affects task classification; content is used for compression.
            current_context: Current conversation context

        Returns:
            CompressionResult with compressed content and metadata
        """
        # Extract task features
        # Note: workspace_paths is used for metrics (file count, directory spread)
        # even when context_files contains content strings rather than actual paths
        workspace_paths = (
            [self.workspace_root / f for f in context_files] if context_files else None
        )
        features = extract_features(
            prompt=prompt,
            workspace_files=workspace_paths,
            current_context=current_context,
        )

        # Classify task
        classification = classify_task(features)

        # Select compression ratio
        ratio = select_compression_ratio(classification, features)

        # Perform compression (simplified - actual implementation would use
        # extractive summarization or other compression techniques)
        compressed = self._compress_content(
            context_files or [],
            ratio,
            classification,
        )

        # Generate rationale
        rationale = self._generate_rationale(classification, features, ratio)

        if self.enable_logging:
            self._log_compression(classification, ratio, rationale)

        # Combine into single content string
        original = "\n\n".join(context_files or [])

        return CompressionResult(
            original_content=original,
            compressed_content=compressed,
            compression_ratio=ratio,
            task_classification=classification,
            rationale=rationale,
        )

    def _compress_content(
        self,
        content_pieces: list[str],
        ratio: float,
        classification: TaskClassification,
    ) -> str:
        """Compress content using selected ratio.

        This is a simplified implementation. A production version would:
        - Use extractive summarization
        - Apply sentence-level selection
        - Preserve code blocks and structure
        - Handle different content types appropriately

        Args:
            content_pieces: List of content strings to compress
            ratio: Target compression ratio
            classification: Task classification for context

        Returns:
            Compressed content string
        """
        # Simplified: Just truncate to approximate ratio
        # Real implementation would use smart selection
        combined = "\n\n".join(content_pieces)
        target_length = int(len(combined) * ratio)

        # Preserve beginning (most important context)
        if classification.primary_type in ["diagnostic", "fix"]:
            # For focused tasks, keep beginning
            compressed = combined[:target_length]
        elif classification.primary_type == "implementation":
            # For architecture tasks, keep more complete sections
            # Split into sections and keep proportionally
            sections = combined.split("\n\n")
            target_sections = max(1, int(len(sections) * ratio))
            compressed = "\n\n".join(sections[:target_sections])
        else:
            # Mixed/exploration: balanced approach
            compressed = combined[:target_length]

        return compressed

    def _generate_rationale(
        self,
        classification: TaskClassification,
        features: TaskFeatures,
        ratio: float,
    ) -> str:
        """Generate human-readable rationale for compression decisions."""
        lines = [
            f"Task Type: {classification.primary_type}",
            f"Confidence: {classification.confidence:.2f}",
            f"Compression Ratio: {ratio:.2f}",
            "",
            "Key Factors:",
        ]

        if features.files_to_modify > 0:
            lines.append(f"- Files to modify: {features.files_to_modify}")

        if features.has_reference_impl:
            lines.append("- Reference implementation available")

        if features.import_depth > 2:
            lines.append(f"- Complex dependencies (depth: {features.import_depth})")

        lines.extend(["", classification.rationale])

        return "\n".join(lines)

    def _log_compression(
        self,
        classification: TaskClassification,
        ratio: float,
        rationale: str,
    ) -> None:
        """Log compression decision for debugging."""
        import logging

        logger = logging.getLogger(__name__)
        logger.info(
            f"Adaptive compression: type={classification.primary_type}, "
            f"ratio={ratio:.2f}, confidence={classification.confidence:.2f}"
        )
        logger.debug(f"Rationale:\n{rationale}")
