"""Main task analysis interface for adaptive compression.

The TaskAnalyzer provides the primary API for analyzing tasks and
determining appropriate compression ratios.

Usage:
    analyzer = TaskAnalyzer()
    result = analyzer.analyze(task_description, workspace_context)
    ratio = result.compression_ratio
"""

from dataclasses import dataclass
from pathlib import Path

from .indicators import TaskIndicators
from .ratio_selector import (
    estimate_reduction,
    get_ratio_category,
    select_compression_ratio,
)
from .scorer import calculate_complexity_score, classify_complexity


@dataclass
class AnalysisResult:
    """Result of task complexity analysis."""

    indicators: TaskIndicators
    complexity_score: float  # 0.0-1.0
    complexity_category: str  # "focused", "mixed", "architecture"
    compression_ratio: float  # 0.10-0.50
    ratio_category: str  # "aggressive", "moderate", "conservative"
    estimated_reduction: float  # 0.0-1.0 (percentage)

    def to_dict(self) -> dict:
        """Convert to dictionary for logging/debugging."""
        return {
            "indicators": self.indicators.to_dict(),
            "complexity_score": self.complexity_score,
            "complexity_category": self.complexity_category,
            "compression_ratio": self.compression_ratio,
            "ratio_category": self.ratio_category,
            "estimated_reduction": self.estimated_reduction,
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        return (
            f"Task Complexity: {self.complexity_category} "
            f"(score: {self.complexity_score:.2f})\n"
            f"Compression: {self.ratio_category} "
            f"(ratio: {self.compression_ratio:.2f}, "
            f"~{self.estimated_reduction*100:.0f}% reduction)"
        )


class TaskAnalyzer:
    """Main task analysis interface for adaptive compression.

    Analyzes task characteristics and determines appropriate compression ratio.
    """

    def analyze(
        self,
        task_description: str | None = None,
        workspace_path: Path | None = None,
    ) -> AnalysisResult:
        """Analyze task and determine compression strategy.

        Args:
            task_description: Task description text (from prompt, issue, etc.)
            workspace_path: Path to workspace for context detection

        Returns:
            AnalysisResult with compression recommendation
        """
        # Extract indicators (Week 2 implementation)
        indicators = self._extract_indicators(task_description, workspace_path)

        # Calculate complexity score
        complexity_score = calculate_complexity_score(indicators)

        # Classify complexity
        complexity_category = classify_complexity(complexity_score)

        # Select compression ratio
        compression_ratio = select_compression_ratio(complexity_score)

        # Get ratio category
        ratio_category = get_ratio_category(compression_ratio)

        # Estimate reduction
        estimated_reduction = estimate_reduction(compression_ratio)

        return AnalysisResult(
            indicators=indicators,
            complexity_score=complexity_score,
            complexity_category=complexity_category,
            compression_ratio=compression_ratio,
            ratio_category=ratio_category,
            estimated_reduction=estimated_reduction,
        )

    def _extract_indicators(
        self,
        task_description: str | None,
        workspace_path: Path | None,
    ) -> TaskIndicators:
        """Extract task indicators from description and workspace.

        TODO (Week 2): Implement full extraction logic
        - Parse task description for patterns
        - Analyze workspace for references
        - Detect dependencies and scope

        For now, returns empty indicators (will use default compression).
        """
        # Placeholder implementation - Week 2 will implement full extraction
        return TaskIndicators()
