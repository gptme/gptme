"""Enhanced lesson matching with context selector support."""

import logging
from typing import TYPE_CHECKING

from ..context_selector.base import ContextSelector
from ..context_selector.config import ContextSelectorConfig
from ..context_selector.hybrid import HybridSelector
from ..context_selector.llm_based import LLMSelector
from ..context_selector.rule_based import RuleBasedSelector
from .matcher import LessonMatcher, MatchContext, MatchResult
from .parser import Lesson
from .selector_config import LessonSelectorConfig
from .selector_integration import LessonItem

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class EnhancedLessonMatcher(LessonMatcher):
    """Lesson matcher with context selector support.

    Extends the base LessonMatcher with LLM-based selection capabilities.
    """

    def __init__(
        self,
        selector_config: ContextSelectorConfig | None = None,
        lesson_config: LessonSelectorConfig | None = None,
        use_selector: bool = False,
    ):
        """Initialize enhanced matcher.

        Args:
            selector_config: Configuration for context selector
            lesson_config: Configuration for lesson-specific behavior
            use_selector: Whether to use context selector (default: False for backward compat)
        """
        super().__init__()
        self.use_selector = use_selector
        self.selector_config = selector_config or ContextSelectorConfig()
        self.lesson_config = lesson_config or LessonSelectorConfig()
        self._selector: ContextSelector | None = None

    def _get_selector(self) -> ContextSelector:
        """Get or create context selector based on configuration."""
        if self._selector is None:
            strategy = self.selector_config.strategy

            if strategy == "rule":
                self._selector = RuleBasedSelector(self.selector_config)
            elif strategy == "llm":
                self._selector = LLMSelector(self.selector_config)
            elif strategy == "hybrid":
                self._selector = HybridSelector(self.selector_config)
            else:
                raise ValueError(f"Unknown strategy: {strategy}")

        return self._selector

    async def match_with_selector(
        self,
        lessons: list[Lesson],
        context: MatchContext,
        max_results: int = 5,
    ) -> list[MatchResult]:
        """Match lessons using context selector.

        Args:
            lessons: List of lessons to match against
            context: Context to match (message, tools, etc.)
            max_results: Maximum number of results to return

        Returns:
            List of match results, sorted by relevance
        """
        # Wrap lessons in LessonItem for selector
        lesson_items = [LessonItem(lesson) for lesson in lessons]

        # Use selector to find best matches
        selector = self._get_selector()
        selected_items = await selector.select(
            query=context.message,
            candidates=lesson_items,  # type: ignore[arg-type]
            max_results=max_results,
        )

        # Convert back to MatchResult with metadata boosts
        results = []
        for i, item in enumerate(selected_items):
            # Type narrowing: we know these are LessonItem objects
            assert isinstance(item, LessonItem)
            # Base score decreases with rank (1.0 for first, 0.8 for second, etc.)
            base_score = 1.0 - (i * 0.2)

            # Apply metadata boosts (priority, category)
            boosted_score = self.lesson_config.apply_metadata_boost(
                base_score,
                item.metadata,
            )

            # Determine what matched
            matched_by = []
            if self.selector_config.strategy == "rule":
                # For rule-based, show keyword matches
                for keyword in item.lesson.metadata.keywords:
                    if keyword.lower() in context.message.lower():
                        matched_by.append(f"keyword:{keyword}")
            elif self.selector_config.strategy == "llm":
                matched_by.append("llm:semantic-match")
            else:  # hybrid
                matched_by.append("hybrid:rule+llm")

            # Add metadata indicators to matched_by
            priority = item.metadata.get("priority")
            if priority and priority != "normal":
                matched_by.append(f"priority:{priority}")

            results.append(
                MatchResult(
                    lesson=item.lesson,
                    score=boosted_score,
                    matched_by=matched_by or ["selector"],
                )
            )

        return results

    def match(
        self, lessons: list[Lesson], context: MatchContext, threshold: float = 0.0
    ) -> list[MatchResult]:
        """Find matching lessons.

        Uses context selector if enabled, otherwise falls back to keyword matching.

        Args:
            lessons: List of lessons to match against
            context: Context to match (message, tools, etc.)
            threshold: Minimum score threshold (only for keyword matching)

        Returns:
            List of match results, sorted by score (descending)
        """
        if self.use_selector:
            # Use async selector
            import asyncio

            try:
                return asyncio.run(
                    self.match_with_selector(lessons, context, max_results=5)
                )
            except Exception as e:
                logger.warning(
                    f"Selector failed, falling back to keyword matching: {e}"
                )
                # Fall through to keyword matching

        # Use parent class keyword matching
        return super().match(lessons, context, threshold)
