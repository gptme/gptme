"""File-specific configuration for context selector."""

from dataclasses import dataclass

from .config import ContextSelectorConfig


@dataclass
class FileSelectorConfig(ContextSelectorConfig):
    """Configuration for file selection with file-specific boosts."""

    # Mention count boosts (files mentioned more frequently)
    mention_boost_thresholds: dict[int, float] = None  # type: ignore

    # Recency boosts (files modified recently)
    recency_boost_hours: dict[float, float] = None  # type: ignore

    # File type weights (prioritize certain file types)
    file_type_weights: dict[str, float] = None  # type: ignore

    def __post_init__(self):
        # Default mention count boosts
        if self.mention_boost_thresholds is None:
            self.mention_boost_thresholds = {
                10: 3.0,  # 10+ mentions: 3x boost
                5: 2.0,  # 5-9 mentions: 2x boost
                2: 1.5,  # 2-4 mentions: 1.5x boost
                1: 1.0,  # 1 mention: no boost
            }

        # Default recency boosts (hours since last modification)
        if self.recency_boost_hours is None:
            self.recency_boost_hours = {
                1.0: 1.3,  # Modified in last hour: 1.3x boost
                24.0: 1.1,  # Modified today: 1.1x boost
                168.0: 1.05,  # Modified this week: 1.05x boost
            }

        # Default file type weights
        if self.file_type_weights is None:
            self.file_type_weights = {
                "py": 1.3,  # Python files more relevant
                "md": 1.2,  # Markdown documentation
                "toml": 1.1,  # Config files
                "yaml": 1.1,
                "json": 1.0,
                "txt": 1.0,
            }

    def get_mention_boost(self, mention_count: int) -> float:
        """Get boost factor based on mention count."""
        for threshold in sorted(self.mention_boost_thresholds.keys(), reverse=True):
            if mention_count >= threshold:
                return self.mention_boost_thresholds[threshold]
        return 1.0

    def get_recency_boost(self, hours_since_modified: float) -> float:
        """Get boost factor based on recency."""
        for threshold in sorted(self.recency_boost_hours.keys()):
            if hours_since_modified <= threshold:
                return self.recency_boost_hours[threshold]
        return 1.0

    def get_file_type_weight(self, file_type: str) -> float:
        """Get weight factor based on file type."""
        return self.file_type_weights.get(file_type, 1.0)
