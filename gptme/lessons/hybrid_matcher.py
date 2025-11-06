"""Hybrid lesson matching using semantic similarity + effectiveness."""

import logging
from dataclasses import dataclass
from typing import Any

from .matcher import LessonMatcher, MatchContext, MatchResult
from .parser import Lesson

logger = logging.getLogger(__name__)

# Optional embedding support
try:
    import numpy as np
    from sentence_transformers import (  # type: ignore[import-not-found]
        SentenceTransformer,
    )

    EMBEDDINGS_AVAILABLE = True
except ImportError:
    EMBEDDINGS_AVAILABLE = False
    logger.info("Embeddings not available, falling back to keyword-only matching")


@dataclass
class HybridConfig:
    """Configuration for hybrid lesson matching."""

    # Scoring weights (must sum to ~1.0 for interpretability)
    keyword_weight: float = 0.25
    semantic_weight: float = 0.40
    effectiveness_weight: float = 0.25
    recency_weight: float = 0.10
    tool_bonus: float = 0.20  # Additional bonus for tool matches

    # Retrieval parameters
    top_k: int = 20  # Candidate filtering (Stage 1)

    # Dynamic top-K selection (Phase 5.5)
    min_score_threshold: float = 0.6  # Minimum score for inclusion
    max_lessons: int = 10  # Maximum lessons to prevent context explosion

    # Recency decay
    recency_decay_days: int = 30  # Half-life for recency score

    # Enable/disable components
    enable_semantic: bool = True
    enable_effectiveness: bool = True
    enable_recency: bool = True


class HybridLessonMatcher(LessonMatcher):
    """Hybrid lesson matcher combining keyword, semantic, and metadata signals."""

    def __init__(self, config: HybridConfig | None = None):
        """Initialize hybrid matcher.

        Args:
            config: Hybrid matching configuration
        """
        super().__init__()
        self.config = config or HybridConfig()

        # Initialize embedder if available and enabled
        self.embedder = None
        if EMBEDDINGS_AVAILABLE and self.config.enable_semantic:
            try:
                self.embedder = SentenceTransformer("all-MiniLM-L6-v2")
                logger.info("Initialized sentence embedder for semantic matching")
            except Exception as e:
                logger.warning(f"Failed to initialize embedder: {e}")
                self.embedder = None

    def match(
        self, lessons: list[Lesson], context: MatchContext, threshold: float = 0.0
    ) -> list[MatchResult]:
        """Find matching lessons using hybrid scoring.

        If embeddings unavailable, falls back to parent keyword-only matching.

        Args:
            lessons: List of lessons to match against
            context: Context to match (message, tools, etc.)
            threshold: Minimum score threshold

        Returns:
            List of match results, sorted by hybrid score (descending)
        """
        # Fallback to keyword-only if semantic matching unavailable
        if not self.embedder:
            return super().match(lessons, context, threshold)

        # Stage 1: Fast candidate filtering (keyword + tool)
        candidates = self._get_candidates(lessons, context)

        if not candidates:
            return []

        # Stage 2: Hybrid scoring on candidates
        results = self._score_candidates(candidates, context)

        # Sort by score (descending)
        results.sort(key=lambda r: r.score, reverse=True)

        # Phase 5.5: Dynamic top-K selection
        # Strict threshold filtering - quality over quantity
        threshold = max(threshold, self.config.min_score_threshold)
        filtered = [r for r in results if r.score >= threshold]

        # No safeguard for minimum lessons - preventing cumulative degradation
        # Lessons are fetched EVERY TURN (not per-session), so forcing min_lessons
        # would accumulate sub-threshold lessons across turns:
        #   Turn 1: Include [0.65, 0.55] (1 good + 1 marginal)
        #   Turn 2: Include [0.70, 0.58] (1 good + 1 marginal)
        #   Turn N: Total 20 good + 20 marginal = 40 lessons
        #
        # Better: Trust the threshold. If 0-1 lessons match, that's fine.
        # Quality > quantity. Future turns will provide more relevant lessons.

        # Cap at max_lessons to prevent context explosion
        return filtered[: self.config.max_lessons]

    def _get_candidates(
        self, lessons: list[Lesson], context: MatchContext
    ) -> list[MatchResult]:
        """Stage 1: Fast candidate filtering using keywords and tools."""
        # Use parent's keyword matching
        results = super().match(lessons, context, threshold=0.0)
        return results[: self.config.top_k]

    def _score_candidates(
        self, candidates: list[MatchResult], context: MatchContext
    ) -> list[MatchResult]:
        """Stage 2: Hybrid scoring on filtered candidates."""
        if not self.embedder:
            return candidates

        # Generate query embedding
        query_embed = self.embedder.encode(context.message, convert_to_numpy=True)

        hybrid_results = []
        for match in candidates:
            lesson = match.lesson

            # Component 1: Keyword score (normalized)
            keyword_score = self._keyword_score(lesson, context)

            # Component 2: Semantic score
            semantic_score = 0.0
            if self.config.enable_semantic:
                semantic_score = self._semantic_score(query_embed, lesson, context)

            # Component 3: Effectiveness score
            # NOTE: Neutral until ACE metadata implemented (Phase 1 schema)
            effectiveness_score = 0.5
            if self.config.enable_effectiveness:
                effectiveness_score = self._effectiveness_score(lesson)

            # Component 4: Recency score
            # NOTE: Assume recent until ACE metadata implemented (Phase 1 schema)
            recency_score = 1.0
            if self.config.enable_recency:
                recency_score = self._recency_score(lesson)

            # Component 5: Tool bonus
            tool_bonus_score = self._tool_bonus(lesson, context)

            # Combine scores
            hybrid_score = (
                self.config.keyword_weight * keyword_score
                + self.config.semantic_weight * semantic_score
                + self.config.effectiveness_weight * effectiveness_score
                + self.config.recency_weight * recency_score
                + tool_bonus_score
            )

            # Create new result with hybrid score
            hybrid_results.append(
                MatchResult(
                    lesson=lesson,
                    score=hybrid_score,
                    matched_by=match.matched_by
                    + [
                        f"hybrid:kw={keyword_score:.2f}",
                        f"sem={semantic_score:.2f}",
                        f"eff={effectiveness_score:.2f}",
                        f"rec={recency_score:.2f}",
                    ],
                )
            )

        return hybrid_results

    def _keyword_score(self, lesson: Lesson, context: MatchContext) -> float:
        """Normalized keyword relevance score (0.0-1.0)."""
        message_lower = context.message.lower()
        matches = sum(
            1 for kw in lesson.metadata.keywords if kw.lower() in message_lower
        )
        total_keywords = (
            len(lesson.metadata.keywords) if lesson.metadata.keywords else 1
        )
        return matches / total_keywords

    def _semantic_score(
        self, query_embed: Any, lesson: Lesson, context: MatchContext
    ) -> float:
        """Cosine similarity between query and lesson (0.0-1.0)."""
        if not self.embedder:
            return 0.0

        # Generate lesson embedding from title and body
        lesson_text = f"{lesson.title}\n{lesson.body[:500]}"  # Limit to first 500 chars
        lesson_embed = self.embedder.encode(lesson_text, convert_to_numpy=True)

        # Cosine similarity
        similarity = float(
            np.dot(query_embed, lesson_embed)
            / (np.linalg.norm(query_embed) * np.linalg.norm(lesson_embed))
        )

        # Normalize from [-1, 1] to [0, 1]
        return (similarity + 1.0) / 2.0

    def _effectiveness_score(self, lesson: Lesson) -> float:
        """Effectiveness score from metadata (0.0-1.0).

        NOTE: Returns neutral 0.5 until ACE metadata schema implemented.
        Will use helpful_count/harmful_count when available (Phase 1).
        """
        # TODO: Implement when ACE metadata available
        # helpful = lesson.metadata.helpful_count
        # harmful = lesson.metadata.harmful_count
        # return helpful / (helpful + harmful + 1)
        return 0.5

    def _recency_score(self, lesson: Lesson) -> float:
        """Recency score with exponential decay (0.0-1.0).

        NOTE: Returns 1.0 (assume recent) until ACE metadata implemented.
        Will use updated timestamp when available (Phase 1).
        """
        # TODO: Implement when ACE metadata available
        # from datetime import datetime, timezone
        # updated = lesson.metadata.updated
        # now = datetime.now(timezone.utc)
        # days_since = (now - updated).days
        # return exp(-days_since / self.config.recency_decay_days)
        return 1.0

    def _tool_bonus(self, lesson: Lesson, context: MatchContext) -> float:
        """Tool match bonus (0.0 or configured bonus)."""
        if not context.tools_used or not lesson.metadata.tools:
            return 0.0

        for tool in lesson.metadata.tools:
            if tool in context.tools_used:
                return self.config.tool_bonus

        return 0.0
