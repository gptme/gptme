"""Lesson matching based on context."""

import logging
from dataclasses import dataclass

from .parser import Lesson

logger = logging.getLogger(__name__)


@dataclass
class MatchContext:
    """Context for lesson matching."""

    message: str
    tools_used: list[str] | None = None
    # Future: files, working_dir, etc.


@dataclass
class MatchResult:
    """Result of lesson matching."""

    lesson: Lesson
    score: float
    matched_by: list[str]  # e.g., ['keyword:patch', 'keyword:file']


class LessonMatcher:
    """Match lessons based on context."""

    def match(
        self, lessons: list[Lesson], context: MatchContext, threshold: float = 0.0
    ) -> list[MatchResult]:
        """Find matching lessons and score them.

        Args:
            lessons: List of lessons to match against
            context: Context to match (message, tools, etc.)
            threshold: Minimum score threshold

        Returns:
            List of match results, sorted by score (descending)
        """
        results = []
        message_lower = context.message.lower()

        for lesson in lessons:
            score = 0.0
            matched_by = []

            # Keyword matching
            for keyword in lesson.metadata.keywords:
                if keyword.lower() in message_lower:
                    score += 1.0
                    matched_by.append(f"keyword:{keyword}")

            # Tool matching
            if context.tools_used and lesson.metadata.tools:
                for tool in lesson.metadata.tools:
                    if tool in context.tools_used:
                        score += 2.0  # Higher weight for tool matches
                        matched_by.append(f"tool:{tool}")

            # Future: glob matching, semantic matching

            if score > threshold:
                results.append(
                    MatchResult(lesson=lesson, score=score, matched_by=matched_by)
                )

        # Sort by score, descending
        results.sort(key=lambda r: r.score, reverse=True)
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
        results = []

        for lesson in lessons:
            matched_keywords = [kw for kw in keywords if kw in lesson.metadata.keywords]

            if matched_keywords:
                score = float(len(matched_keywords))
                matched_by = [f"keyword:{kw}" for kw in matched_keywords]
                results.append(
                    MatchResult(lesson=lesson, score=score, matched_by=matched_by)
                )

        results.sort(key=lambda r: r.score, reverse=True)
        return results
