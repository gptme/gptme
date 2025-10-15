"""Lesson matching based on keywords and context."""

import logging
from dataclasses import dataclass

from .cache import MatchCache, get_match_cache
from .parser import Lesson

logger = logging.getLogger(__name__)


@dataclass
class MatchContext:
    """Context for matching lessons."""

    message: str
    tools_used: list[str] | None = None

    def extract_keywords(self) -> list[str]:
        """Extract keywords from message for matching."""
        # Simple keyword extraction - could be enhanced
        words = self.message.lower().split()
        # Filter out common words
        common = {"the", "a", "an", "is", "are", "was", "were", "be", "been"}
        keywords = [w for w in words if w not in common and len(w) > 2]
        return keywords


@dataclass
class MatchResult:
    """Result of matching a lesson."""

    lesson: Lesson
    score: float
    matched_by: list[str]


class LessonMatcher:
    """Matches lessons based on keywords and context with caching."""

    def __init__(self, use_cache: bool = True):
        """Initialize matcher.

        Args:
            use_cache: Whether to use match result caching (default: True)
        """
        self.use_cache = use_cache
        self.match_cache: MatchCache | None
        if use_cache:
            self.match_cache = get_match_cache()
        else:
            self.match_cache = None

    def match(
        self,
        lessons: list[Lesson],
        context: MatchContext,
        threshold: float = 0.0,
    ) -> list[MatchResult]:
        """Match lessons based on context.

        Args:
            lessons: Available lessons
            context: Match context with message and tools
            threshold: Minimum score threshold (default: 0.0)

        Returns:
            Sorted list of match results (best first)
        """
        # Extract keywords from context
        keywords = context.extract_keywords()
        tools = context.tools_used or []

        # Check cache first
        if self.match_cache:
            cached_results = self.match_cache.get(keywords, tools)
            if cached_results:
                logger.debug(f"Match cache hit for {len(keywords)} keywords")
                # Filter cached results
                lesson_paths = {str(lesson.path) for lesson in lessons}
                valid_results = [
                    r
                    for r in cached_results
                    if str(r.lesson.path) in lesson_paths
                    and r.score > 0  # Only actual matches
                    and r.score >= threshold
                ]
                if valid_results:
                    return valid_results

        # Cache miss or disabled - compute matches
        results = []

        for lesson in lessons:
            score = 0.0
            matched_by = []

            # Match keywords
            lesson_keywords = [kw.lower() for kw in lesson.metadata.keywords]

            for keyword in keywords:
                for lesson_kw in lesson_keywords:
                    if keyword in lesson_kw or lesson_kw in keyword:
                        score += 1.0
                        matched_by.append(f"keyword:{lesson_kw}")
                        break

            # Match tools
            for tool in tools:
                tool_lower = tool.lower()
                for lesson_kw in lesson_keywords:
                    if tool_lower in lesson_kw or lesson_kw in tool_lower:
                        score += 2.0  # Tools are more specific
                        matched_by.append(f"tool:{tool}")
                        break

            # Only include if there was an actual match and meets threshold
            if score > 0 and score >= threshold:
                results.append(
                    MatchResult(
                        lesson=lesson,
                        score=score,
                        matched_by=list(set(matched_by)),  # Deduplicate
                    )
                )

        # Sort by score (descending)
        results.sort(key=lambda r: r.score, reverse=True)

        # Cache results
        if self.match_cache and results:
            self.match_cache.set(keywords, tools, results)
            logger.debug(f"Cached {len(results)} match results")

        return results

    def match_keywords(
        self, lessons: list[Lesson], keywords: list[str]
    ) -> list[MatchResult]:
        """Match lessons by explicit keywords.

        Args:
            lessons: List of lessons to match against
            keywords: Keywords to match

        Returns:
            List of match results
        """
        # Check cache first
        if self.match_cache:
            cached_results = self.match_cache.get(keywords, [])
            if cached_results:
                logger.debug("Match cache hit for explicit keywords")
                # Filter to lessons still in index
                lesson_paths = {str(lesson.path) for lesson in lessons}
                valid_results = [
                    r for r in cached_results if str(r.lesson.path) in lesson_paths
                ]
                if valid_results:
                    return valid_results

        # Compute matches
        results = []
        keywords_lower = [kw.lower() for kw in keywords]

        for lesson in lessons:
            score = 0.0
            matched_by = []

            lesson_keywords = [kw.lower() for kw in lesson.metadata.keywords]

            for keyword in keywords_lower:
                for lesson_kw in lesson_keywords:
                    if keyword == lesson_kw:
                        score += 1.0
                        matched_by.append(f"keyword:{lesson_kw}")
                        break

            # Only include if there was a match
            if score > 0:
                results.append(
                    MatchResult(
                        lesson=lesson,
                        score=score,
                        matched_by=list(set(matched_by)),
                    )
                )

        # Sort by score
        results.sort(key=lambda r: r.score, reverse=True)

        # Cache results
        if self.match_cache and results:
            self.match_cache.set(keywords, [], results)
            logger.debug(f"Cached {len(results)} keyword match results")

        return results
