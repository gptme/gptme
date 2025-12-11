"""Adaptive context compressor using task complexity analysis.

This module provides adaptive compression that adjusts compression ratios
based on task complexity. It integrates the task analyzer to classify tasks
and select appropriate compression strategies.

The compression uses extractive summarization that:
- Preserves code blocks (always kept)
- Scores sentences by importance (positional, key terms, length)
- Selects top sentences to meet target ratio
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .task_analyzer import (
    TaskClassification,
    TaskFeatures,
    classify_task,
    extract_features,
    select_compression_ratio,
)

logger = logging.getLogger(__name__)


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


def extract_code_blocks(content: str) -> tuple[str, list[tuple[str, str]]]:
    """
    Extract code blocks from content, returning cleaned content and blocks.

    Args:
        content: Message content with potential code blocks

    Returns:
        Tuple of (content without code blocks, list of (marker, code block) tuples)
    """
    code_blocks: list[tuple[str, str]] = []
    code_block_pattern = r"```[\s\S]*?```"

    def replacer(match: re.Match[str]) -> str:
        marker = f"__CODE_BLOCK_{len(code_blocks)}__"
        code_blocks.append((marker, match.group(0)))
        return marker

    cleaned = re.sub(code_block_pattern, replacer, content)
    return cleaned, code_blocks


def score_sentence(
    sentence: str,
    position: int,
    total: int,
    task_type: str = "mixed",
) -> float:
    """
    Score sentence importance using task-aware heuristics.

    Higher scores for:
    - Sentences at beginning/end (positional bias)
    - Sentences with key terms
    - Shorter sentences (more information-dense)
    - Task-relevant keywords

    Args:
        sentence: The sentence to score
        position: Position in message (0-indexed)
        total: Total number of sentences
        task_type: Task classification for context-aware scoring

    Returns:
        Importance score (higher = more important)
    """
    score = 0.0

    # Positional bias: first and last sentences more important
    if position == 0:
        score += 2.0
    elif position == total - 1:
        score += 1.5
    elif position < 3:
        score += 1.0

    lower_sentence = sentence.lower()

    # Universal key terms
    key_terms = [
        "error",
        "fail",
        "success",
        "complete",
        "implement",
        "fix",
        "bug",
        "issue",
        "result",
        "output",
        "TODO",
        "FIXME",
        "NOTE",
        "WARNING",
        "important",
        "critical",
        "must",
        "should",
    ]
    for term in key_terms:
        if term.lower() in lower_sentence:
            score += 0.5

    # Task-specific term boosting
    task_terms: dict[str, list[str]] = {
        "diagnostic": ["error", "exception", "traceback", "stack", "debug", "log"],
        "fix": ["fix", "bug", "patch", "correct", "resolve", "repair"],
        "implementation": [
            "design",
            "architecture",
            "interface",
            "pattern",
            "structure",
            "component",
        ],
        "exploration": ["explore", "investigate", "research", "understand", "analyze"],
    }
    if task_type in task_terms:
        for term in task_terms[task_type]:
            if term in lower_sentence:
                score += 0.3

    # Length penalty: prefer shorter, denser sentences
    # But not too short (less than 10 chars is probably not useful)
    length = len(sentence)
    if length < 10:
        score -= 1.0
    elif length < 50:
        score += 0.3
    elif length > 200:
        score -= 0.2

    return score


def extractive_compress(
    content: str,
    target_ratio: float = 0.7,
    task_type: str = "mixed",
) -> str:
    """
    Compress content using extractive summarization.

    Preserves:
    - Code blocks (always kept)
    - Important sentences based on scoring
    - Overall structure

    Args:
        content: Content to compress
        target_ratio: Target length as ratio of original (0.7 = 30% reduction)
        task_type: Task classification for context-aware compression

    Returns:
        Compressed content
    """
    # Extract and preserve code blocks
    cleaned, code_blocks = extract_code_blocks(content)

    # Split into sentences (simple split on . ! ?)
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    if len(sentences) <= 3:
        # Too few sentences to compress meaningfully
        return content

    # Keep sentences that contain code block markers (don't score them)
    marker_sentences: list[tuple[int, str]] = []
    scoreable_sentences: list[tuple[int, str]] = []
    for i, sent in enumerate(sentences):
        if "__CODE_BLOCK_" in sent:
            marker_sentences.append((i, sent))
        else:
            scoreable_sentences.append((i, sent))

    # Score scoreable sentences
    scored = [
        (score_sentence(sent, i, len(sentences), task_type), i, sent)
        for i, sent in scoreable_sentences
    ]

    # Sort by score descending
    scored.sort(reverse=True)

    # Calculate how many sentences to keep
    original_scoreable_len = sum(len(sent) for _, sent in scoreable_sentences)
    code_block_len = sum(len(block) for _, block in code_blocks)
    marker_len = sum(len(sent) for _, sent in marker_sentences)

    # Target length minus code blocks and markers (those are always kept)
    available_len = (
        int((original_scoreable_len + code_block_len + marker_len) * target_ratio)
        - code_block_len
        - marker_len
    )
    available_len = max(available_len, 0)

    # Select top sentences up to target length
    selected_indices: set[int] = set()
    current_len = 0
    for _score, idx, sent in scored:
        if current_len + len(sent) > available_len:
            break
        selected_indices.add(idx)
        current_len += len(sent)

    # Reconstruct in original order
    # Combine marker sentences and selected scoreable sentences
    all_kept = [(idx, sent) for idx, sent in marker_sentences]
    all_kept.extend(
        [(idx, sent) for idx, sent in scoreable_sentences if idx in selected_indices]
    )
    all_kept.sort(key=lambda x: x[0])  # Sort by original position

    # Join sentences
    compressed = " ".join(sent for _, sent in all_kept)

    # Restore code blocks
    for marker, block in code_blocks:
        compressed = compressed.replace(marker, block)

    return compressed


class AdaptiveCompressor:
    """Adaptive context compressor using task analysis.

    This compressor analyzes task complexity to select appropriate
    compression ratios, providing aggressive compression for simple
    tasks and conservative compression for complex architectural work.

    Uses extractive summarization that:
    - Preserves all code blocks
    - Scores sentences by importance
    - Adapts scoring to task type

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

        # Perform extractive compression
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
        """Compress content using extractive summarization.

        Uses sentence-level importance scoring with task-aware heuristics:
        - Code blocks are always preserved
        - Sentences scored by position, key terms, and length
        - Task type influences which terms are prioritized
        - Top sentences selected to meet target ratio

        Args:
            content_pieces: List of content strings to compress
            ratio: Target compression ratio
            classification: Task classification for context

        Returns:
            Compressed content string
        """
        combined = "\n\n".join(content_pieces)

        # Skip compression if content is too short (need enough for meaningful extraction)
        if len(combined) < 100:
            return combined

        # Apply extractive compression with task type awareness
        task_type = classification.primary_type
        compressed = extractive_compress(combined, ratio, task_type)

        # Ensure we didn't accidentally expand
        if len(compressed) > len(combined):
            return combined

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

        lines.append("")
        lines.append("Compression Method: Extractive summarization")
        lines.append("- Code blocks preserved")
        lines.append("- Sentences scored by importance")
        lines.append(f"- Task-aware term boosting ({classification.primary_type})")

        lines.extend(["", classification.rationale])

        return "\n".join(lines)

    def _log_compression(
        self,
        classification: TaskClassification,
        ratio: float,
        rationale: str,
    ) -> None:
        """Log compression decision for debugging."""
        logger.info(
            f"Adaptive compression: type={classification.primary_type}, "
            f"ratio={ratio:.2f}, confidence={classification.confidence:.2f}"
        )
        logger.debug(f"Rationale:\n{rationale}")
