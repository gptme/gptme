"""Lesson matching based on context."""

import logging
import os
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
        """Find matching lessons and skills and score them.

        Supports two formats:
        - Lessons: match by `keywords` in frontmatter
        - Skills (Anthropic format): match by `name` and `description` in frontmatter

        Deduplication: Lessons are deduplicated by resolved path (realpath) to handle:
        - Symlinks pointing to the same file
        - Same directory appearing multiple times in lesson_dirs
        - Multiple paths resolving to the same physical file

        Args:
            lessons: List of lessons/skills to match against
            context: Context to match (message, tools, etc.)
            threshold: Minimum score threshold

        Returns:
            List of match results, sorted by score (descending), deduplicated by path
        """
        results = []
        message_lower = context.message.lower()
        # Track seen lesson paths for deduplication (handles symlinks and duplicate dirs)
        seen_paths: set[str] = set()

        for lesson in lessons:
            # Deduplicate by resolved path to handle symlinks and duplicate directories
            resolved_path = os.path.realpath(lesson.path)
            if resolved_path in seen_paths:
                logger.debug(
                    f"Skipping duplicate lesson in matcher: {lesson.title} "
                    f"(resolves to already processed file)"
                )
                continue
            seen_paths.add(resolved_path)

            score = 0.0
            matched_by = []

            # Keyword matching (lesson format)
            for keyword in lesson.metadata.keywords:
                if keyword.lower() in message_lower:
                    score += 1.0
                    matched_by.append(f"keyword:{keyword}")

            # Skill name matching (Anthropic format)
            # Match if skill name appears in message
            if lesson.metadata.name:
                name_lower = lesson.metadata.name.lower()
                # Handle hyphenated names (e.g., "python-repl" matches "python repl")
                name_variants = [
                    name_lower,
                    name_lower.replace("-", " "),
                    name_lower.replace("-", ""),
                ]
                for variant in name_variants:
                    if variant in message_lower:
                        score += 1.5  # Slightly higher weight for name matches
                        matched_by.append(f"skill:{lesson.metadata.name}")
                        break

            # Skill description matching (Anthropic format)
            # Extract significant words from description and match
            if lesson.metadata.description and not matched_by:
                # Only use description matching if not already matched
                # (avoids duplicate matching from keywords, name, or tools)
                desc_words = self._extract_description_keywords(
                    lesson.metadata.description
                )
                matched_desc_words = [w for w in desc_words if w in message_lower]
                if len(matched_desc_words) >= 2:  # Require at least 2 matches
                    score += 0.5 * len(matched_desc_words)
                    matched_by.append(f"description:{','.join(matched_desc_words[:3])}")

            # Tool matching
            if context.tools_used and lesson.metadata.tools:
                for tool in lesson.metadata.tools:
                    if tool in context.tools_used:
                        score += 2.0  # Higher weight for tool matches
                        matched_by.append(f"tool:{tool}")

            if score > threshold:
                results.append(
                    MatchResult(lesson=lesson, score=score, matched_by=matched_by)
                )

        # Sort by score, descending
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def _extract_description_keywords(self, description: str) -> list[str]:
        """Extract significant keywords from skill description.

        Args:
            description: Skill description text

        Returns:
            List of significant keywords (lowercase)
        """
        # Common stop words to exclude
        stop_words = {
            "a",
            "an",
            "the",
            "and",
            "or",
            "but",
            "in",
            "on",
            "at",
            "to",
            "for",
            "of",
            "with",
            "by",
            "from",
            "as",
            "is",
            "was",
            "are",
            "were",
            "been",
            "be",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "must",
            "can",
            "this",
            "that",
            "these",
            "those",
            "it",
            "its",
        }

        # Split on non-word characters and filter
        import re

        words = re.split(r"\W+", description.lower())
        keywords = [
            w
            for w in words
            if len(w) > 2 and w not in stop_words  # Min 3 chars
        ]

        return keywords

    def match_keywords(
        self, lessons: list[Lesson], keywords: list[str]
    ) -> list[MatchResult]:
        """Match lessons by explicit keywords.

        Deduplication: Lessons are deduplicated by resolved path (realpath) to handle
        symlinks and duplicate directories, consistent with match().

        Args:
            lessons: List of lessons to match against
            keywords: Keywords to match

        Returns:
            List of match results, deduplicated by path
        """
        results = []
        # Track seen lesson paths for deduplication (consistent with match())
        seen_paths: set[str] = set()

        for lesson in lessons:
            # Deduplicate by resolved path to handle symlinks and duplicate directories
            resolved_path = os.path.realpath(lesson.path)
            if resolved_path in seen_paths:
                logger.debug(
                    f"Skipping duplicate lesson in match_keywords: {lesson.title}"
                )
                continue
            seen_paths.add(resolved_path)

            matched_keywords = [kw for kw in keywords if kw in lesson.metadata.keywords]

            if matched_keywords:
                score = float(len(matched_keywords))
                matched_by = [f"keyword:{kw}" for kw in matched_keywords]
                results.append(
                    MatchResult(lesson=lesson, score=score, matched_by=matched_by)
                )

        results.sort(key=lambda r: r.score, reverse=True)
        return results
