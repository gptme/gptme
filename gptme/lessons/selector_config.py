"""Configuration for lesson-specific context selector behavior."""

from dataclasses import dataclass, field


@dataclass
class LessonSelectorConfig:
    """Configuration for lesson context selector.

    Controls how lessons are selected and ranked.
    """

    # Priority multipliers (boost score based on lesson priority)
    priority_boost: dict[str, float] = field(
        default_factory=lambda: {
            "critical": 3.0,
            "high": 2.0,
            "normal": 1.0,
            "low": 0.5,
        }
    )

    # Category weights (boost score based on lesson category)
    category_weight: dict[str, float] = field(
        default_factory=lambda: {
            "workflow": 1.5,
            "tools": 1.3,
            "patterns": 1.2,
            "social": 1.0,
            "strategic": 1.0,
        }
    )

    # Whether to use YAML metadata in scoring
    use_metadata: bool = True

    # Maximum number of lessons to select
    max_lessons: int = 5

    # Minimum score threshold (before metadata boost)
    min_score: float = 0.0

    def get_priority_boost(self, priority: str | None) -> float:
        """Get priority boost multiplier."""
        if not self.use_metadata or not priority:
            return 1.0
        return self.priority_boost.get(priority, 1.0)

    def get_category_weight(self, category: str | None) -> float:
        """Get category weight multiplier."""
        if not self.use_metadata or not category:
            return 1.0
        return self.category_weight.get(category, 1.0)

    def apply_metadata_boost(self, base_score: float, metadata: dict) -> float:
        """Apply metadata-based score boosts.

        Args:
            base_score: Base selection score
            metadata: Lesson metadata dict

        Returns:
            Boosted score
        """
        if not self.use_metadata:
            return base_score

        score = base_score

        # Apply priority boost
        priority = metadata.get("priority")
        score *= self.get_priority_boost(priority)

        # Apply category weight
        category = metadata.get("category")
        score *= self.get_category_weight(category)

        return score
