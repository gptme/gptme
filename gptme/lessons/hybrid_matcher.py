"""Hybrid lesson matching using semantic similarity + effectiveness."""

import importlib.util
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .matcher import LessonMatcher, MatchContext, MatchResult, _match_keyword
from .parser import Lesson

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# BM25 semantic scoring (no-embedder fallback)
# ---------------------------------------------------------------------------
# Raw BM25 scores scale with query length (a 50-token prompt tops out ~45,
# a 3000-token prompt ~1200), so an absolute floor cannot separate "relevant"
# from "shares a few common words."  Gate on how far a lesson *stands out*
# from the background z-score distribution of this query's own scores instead.
# Ported from gptme-contrib CC hook (PR #1371, merged 2026-08-05).

_BM25_K1 = 1.2
_BM25_B = 0.75
_BM25_WEIGHT = 0.4  # additive weight for the z-score contribution
_BM25_MIN_Z = 4.0  # minimum z-score to admit a lesson via BM25
_BM25_MIN_RAW = 40.0  # absolute floor — guards degenerate tiny-corpus cases
# A z-score over n samples cannot exceed (n-1)/sqrt(n), so a fixed z=4 is
# unreachable below ~17 scoring lessons.  Scale down proportionally.
_BM25_STANDOUT_FRACTION = 0.8


def _build_bm25_index(lessons: list["Lesson"]) -> dict:
    """Build BM25 index over lesson descriptions, titles, and keywords."""
    corpus: list[list[str]] = []
    for lesson in lessons:
        desc = lesson.metadata.description or lesson.description or ""
        doc = " ".join(
            [
                desc,
                lesson.title or "",
                " ".join(lesson.metadata.keywords or []),
            ]
        )
        corpus.append(re.findall(r"[a-z0-9]+", doc.lower()))

    N = len(corpus)
    avg_dl = sum(len(d) for d in corpus) / max(N, 1)
    df: dict[str, int] = {}
    for doc_tokens in corpus:
        for term in set(doc_tokens):
            df[term] = df.get(term, 0) + 1

    return {"corpus": corpus, "df": df, "N": N, "avg_dl": avg_dl}


def _bm25_score(query_terms: list[str], doc_terms: list[str], index: dict) -> float:
    """Compute BM25 score for query_terms against a document's term list."""
    k1, b = _BM25_K1, _BM25_B
    N, avg_dl = index["N"], index["avg_dl"]
    dl = len(doc_terms)
    if not dl or not query_terms:
        return 0.0

    tf: dict[str, int] = {}
    for term in doc_terms:
        tf[term] = tf.get(term, 0) + 1

    df = index["df"]
    score = 0.0
    for term in query_terms:
        if term not in tf:
            continue
        tf_td = tf[term]
        df_t = df.get(term, 0)
        idf = math.log((N - df_t + 0.5) / (df_t + 0.5) + 1)
        tf_norm = (tf_td * (k1 + 1)) / (tf_td + k1 * (1 - b + b * dl / max(avg_dl, 1)))
        score += idf * tf_norm

    return score


def _bm25_min_z(n_nonzero: int) -> float:
    """Minimum z-score a lesson must reach to count as a semantic match.

    When only 1-2 lessons overlap the query, admits them on the raw floor
    alone — the gate exists to stop the opposite failure where hundreds of
    lessons share common words and nothing is actually about the query.
    """
    if n_nonzero < 3:
        return -math.inf
    max_attainable = (n_nonzero - 1) / math.sqrt(n_nonzero)
    return min(_BM25_MIN_Z, _BM25_STANDOUT_FRACTION * max_attainable)


def _bm25_zscores(scores: list[float]) -> list[float]:
    """Standardize raw BM25 scores against the nonzero score distribution.

    Lessons with score 0 (no term overlap) get 0.0.  If fewer than two
    lessons score nonzero, or the spread is degenerate, every z is 0.0.
    """
    nonzero = [s for s in scores if s > 0]
    if len(nonzero) < 2:
        return [0.0] * len(scores)
    mean = sum(nonzero) / len(nonzero)
    var = sum((s - mean) ** 2 for s in nonzero) / len(nonzero)
    sd = math.sqrt(var)
    if sd <= 0:
        return [0.0] * len(scores)
    return [(s - mean) / sd if s > 0 else 0.0 for s in scores]


# ---------------------------------------------------------------------------

# Optional embedding support — availability checked lazily to avoid a 10+ second
# cumulative import of sentence_transformers/transformers/sklearn at CLI startup.
# The heavy modules are imported on first use inside HybridLessonMatcher.
EMBEDDINGS_AVAILABLE = (
    importlib.util.find_spec("sentence_transformers") is not None
    and importlib.util.find_spec("numpy") is not None
)
if not EMBEDDINGS_AVAILABLE:
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

    # Effectiveness state file path.
    # JSON file with {"arms": {"lesson_name.md": <arm>, ...}} where each arm may contain
    # Thompson-sampling fields ({"alpha": N, "beta": M}), LLM-judge verdict counters
    # ({"helpful": N, "harmful": M, "false_positive": K, "noop": J}), or both.
    # If omitted, a conventional default path or env var override may be used.
    effectiveness_state_file: str | None = None


def _default_effectiveness_state_file() -> str:
    """Return the default effectiveness state file path.

    Priority: GPTME_LESSONS_TS_STATE env var > XDG_STATE_HOME > ~/.local/state fallback.
    Always returns a path string; callers should handle missing files gracefully.
    """
    if env_path := os.getenv("GPTME_LESSONS_TS_STATE"):
        return env_path

    xdg_state_home = os.getenv("XDG_STATE_HOME")
    if xdg_state_home:
        return str(Path(xdg_state_home) / "gptme" / "lessons" / "bandit-state.json")

    return str(
        Path.home() / ".local" / "state" / "gptme" / "lessons" / "bandit-state.json"
    )


def _score_from_ts_arm(arm_data: dict[str, Any]) -> float | None:
    """Return posterior mean for a Thompson-sampling arm, if present."""
    if "alpha" not in arm_data and "beta" not in arm_data:
        return None

    alpha = float(arm_data.get("alpha", 1.0))
    beta = float(arm_data.get("beta", 1.0))
    total = alpha + beta
    return alpha / total if total > 0 else 0.5


def _score_from_judge_arm(arm_data: dict[str, Any]) -> float | None:
    """Return LLM-judge effectiveness score for a lesson arm, if present.

    Supported verdict counters:
    - helpful
    - harmful
    - false_positive
    - noop

    All non-helpful verdicts count against the lesson. This keeps the schema
    compatible with both the issue proposal (`false_positive`) and Bob's current
    workspace pipeline (`noop`).

    Uses Laplace (add-1) smoothing so sparse verdicts don't produce extreme 0.0/1.0
    scores. Mirrors the implicit Beta(1,1) prior in the TS path.
    """
    verdict_keys = ("helpful", "harmful", "false_positive", "noop")
    if not any(key in arm_data for key in verdict_keys):
        return None

    helpful = float(arm_data.get("helpful", 0.0))
    harmful = float(arm_data.get("harmful", 0.0))
    false_positive = float(arm_data.get("false_positive", 0.0))
    noop = float(arm_data.get("noop", 0.0))
    total = helpful + harmful + false_positive + noop
    # Add-1 smoothing: treat as Beta(1,1) prior — same implicit prior as TS arm.
    return (helpful + 1.0) / (total + 2.0)


def _load_effectiveness_scores(state_file: str) -> dict[str, float]:
    """Load lesson effectiveness scores from a bandit/judge state file.

    Supported arm schemas:
    - Thompson sampling: {"alpha": N, "beta": M}
    - LLM judge counts: {"helpful": N, "harmful": M, "false_positive": K}
    - Combined: both schemas present in the same arm

    When both TS and judge signals are present, the returned score is the simple
    average of the two. This preserves backward compatibility while allowing the
    higher-fidelity per-lesson judge signal proposed in #1574 to complement the
    existing session-level TS signal from #1573.
    """
    path = Path(state_file).expanduser()
    if not path.exists():
        logger.debug(f"Effectiveness state file not found: {path}")
        return {}

    try:
        data = json.loads(path.read_text())
        arms = data.get("arms", {})
        posteriors: dict[str, float] = {}
        for arm_name, arm_data in arms.items():
            try:
                if not isinstance(arm_data, dict):
                    raise TypeError(
                        f"arm data must be object, got {type(arm_data).__name__}"
                    )

                component_scores = [
                    score
                    for score in (
                        _score_from_ts_arm(arm_data),
                        _score_from_judge_arm(arm_data),
                    )
                    if score is not None
                ]
                if not component_scores:
                    continue

                posteriors[arm_name] = sum(component_scores) / len(component_scores)
            except (AttributeError, TypeError, ValueError) as e:
                logger.warning(f"Skipping malformed arm '{arm_name}': {e}")
        logger.info(f"Loaded {len(posteriors)} lesson effectiveness scores from {path}")
        return posteriors
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load effectiveness state: {e}")
        return {}


def _lesson_lookup_keys(lesson: Lesson) -> list[str]:
    """Return candidate identifiers for matching lesson effectiveness state."""
    keys: list[str] = []

    if lesson.metadata.id:
        keys.append(lesson.metadata.id)

    keys.append(str(lesson.path))
    keys.append(lesson.path.name)

    parts = lesson.path.parts
    if len(parts) >= 2:
        keys.append(f"{parts[-2]}/{parts[-1]}")

    # De-duplicate while preserving order
    return list(dict.fromkeys(keys))


class HybridLessonMatcher(LessonMatcher):
    """Hybrid lesson matcher combining keyword, semantic, and metadata signals."""

    def __init__(self, config: HybridConfig | None = None):
        """Initialize hybrid matcher.

        Args:
            config: Hybrid matching configuration
        """
        super().__init__()
        self.config = config or HybridConfig()

        # Initialize embedder if available and enabled.
        # sentence_transformers is imported lazily here (rather than at module top)
        # because it pulls in transformers + sklearn (~10s cumulative import time).
        self.embedder = None
        if EMBEDDINGS_AVAILABLE and self.config.enable_semantic:
            try:
                from sentence_transformers import SentenceTransformer

                self.embedder = SentenceTransformer("all-MiniLM-L6-v2")
                logger.info("Initialized sentence embedder for semantic matching")
            except Exception as e:
                logger.warning(f"Failed to initialize embedder: {e}")
                self.embedder = None

        # Load lesson effectiveness scores (TS / judge / combined)
        state_file = (
            self.config.effectiveness_state_file or _default_effectiveness_state_file()
        )
        self._ts_posteriors = _load_effectiveness_scores(state_file)

        # Cache of lesson embeddings keyed on the encoded lesson text. Lessons are
        # matched on every turn but their text is static, so re-encoding the same
        # candidates each turn is wasted compute. See _semantic_score.
        self._lesson_embed_cache: dict[str, Any] = {}

        # BM25 index cache — used as semantic fallback when embeddings are
        # unavailable.  Keyed on the tuple of lesson paths so it rebuilds
        # automatically when the lesson set changes.
        self._bm25_index: dict | None = None
        self._bm25_index_key: tuple[str, ...] = ()

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
        # Fallback to BM25+keyword when sentence embeddings are unavailable.
        # This mirrors the CC hook's scoring path so both runtimes apply the
        # same z-score gate rather than gptme falling back to keyword-only.
        if not self.embedder:
            return self._match_with_bm25(lessons, context, threshold)

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

    def _get_bm25_index(self, lessons: list[Lesson]) -> dict:
        """Return a cached BM25 index, rebuilding when the lesson set changes."""
        key = tuple(str(lesson.path) for lesson in lessons)
        if self._bm25_index is None or key != self._bm25_index_key:
            self._bm25_index = _build_bm25_index(lessons)
            self._bm25_index_key = key
        return self._bm25_index

    def _match_with_bm25(
        self,
        lessons: list[Lesson],
        context: MatchContext,
        threshold: float = 0.0,
    ) -> list[MatchResult]:
        """Keyword + BM25 z-score scoring — used when embeddings are unavailable.

        Matches the CC hook's scoring contract: each lesson receives a keyword
        score (1.0 per match) plus a BM25 contribution weighted by
        _BM25_WEIGHT * z-score, gated on _bm25_min_z.
        """
        index = self._get_bm25_index(lessons)
        query_terms = re.findall(r"[a-z0-9]+", context.message.lower())

        # Compute all BM25 scores up-front (z-gate is per-query, not per-lesson)
        bm_scores: list[float] = [
            _bm25_score(query_terms, doc_terms, index) for doc_terms in index["corpus"]
        ]
        bm_zs = _bm25_zscores(bm_scores)
        bm_n_nonzero = sum(1 for s in bm_scores if s > 0)
        bm_min_z = _bm25_min_z(bm_n_nonzero)

        # Score each lesson with keyword + BM25 (deduplicated by path)
        seen_paths: set[str] = set()
        results: list[MatchResult] = []
        message_lower = context.message.lower()

        for i, lesson in enumerate(lessons):
            resolved = os.path.realpath(lesson.path)
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)

            score = 0.0
            matched_by: list[str] = []

            for keyword in lesson.metadata.keywords:
                if _match_keyword(keyword, message_lower):
                    score += 1.0
                    matched_by.append(f"keyword:{keyword}")

            for pattern in lesson.metadata.patterns:
                from gptme.util.keyword_matching import _match_pattern

                if _match_pattern(pattern, message_lower):
                    score += 1.0
                    matched_by.append(f"pattern:{pattern[:30]}...")

            # Skill name matching (Anthropic format) — parity with LessonMatcher.match
            if lesson.metadata.name:
                name_lower = lesson.metadata.name.lower()
                name_variants = [
                    name_lower,
                    name_lower.replace("-", " "),
                    name_lower.replace("-", ""),
                ]
                for variant in name_variants:
                    if variant in message_lower:
                        score += 1.5
                        matched_by.append(f"skill:{lesson.metadata.name}")
                        break

            if context.tools_used and lesson.metadata.tools:
                tools_lower = {t.lower() for t in context.tools_used}
                for tool in lesson.metadata.tools:
                    if tool.lower() in tools_lower:
                        score += 2.0
                        matched_by.append(f"tool:{tool}")

            # BM25 contribution (z-score gated, scale-free)
            bm_score = bm_scores[i]
            bm_z = bm_zs[i]
            small_corpus = bm_n_nonzero < 3
            if bm_z >= bm_min_z and (
                bm_score >= _BM25_MIN_RAW or (small_corpus and bm_score > 0)
            ):
                if bm_n_nonzero >= 3:
                    bm_contribution = bm_z
                elif bm_n_nonzero == 2:
                    bm_contribution = max(bm_z, 0.5)
                else:
                    bm_contribution = 1.0
                score += _BM25_WEIGHT * max(bm_contribution, 0.0)
                matched_by.append(f"bm25:{bm_score:.2f}")

            if score > threshold:
                results.append(
                    MatchResult(lesson=lesson, score=score, matched_by=matched_by)
                )

        results.sort(key=lambda r: r.score, reverse=True)
        return results[: self.config.max_lessons]

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
            # Falls back to 0.5 (neutral) when no TS data is configured or available
            effectiveness_score = 0.5
            if self.config.enable_effectiveness:
                effectiveness_score = self._effectiveness_score(lesson)

            # Component 4: Recency score
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
        """Normalized keyword relevance score (0.0-1.0).

        Uses wildcard-aware matching via _match_keyword for consistency
        with the main matcher.
        """
        message_lower = context.message.lower()
        matches = sum(
            1 for kw in lesson.metadata.keywords if _match_keyword(kw, message_lower)
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

        import numpy as np

        # Use description field for semantic matching — it encodes "when to use" in prose,
        # which is more informative than raw body text for retrieval.
        # Prefer explicit frontmatter description, fall back to extracted first-paragraph,
        # then fall back to body[:500] so title-only embedding never silently degrades
        # lessons that have no description field.
        desc = lesson.metadata.description or lesson.description or lesson.body[:500]
        lesson_text = f"{lesson.title}\n{desc}"

        # Cache embeddings on the lesson text: matching runs every turn, but the
        # text is static, so re-encoding the same candidates each turn is wasted
        # compute. Keying on the text means an edited description naturally yields
        # a fresh embedding — no stale-cache risk.
        lesson_embed = self._lesson_embed_cache.get(lesson_text)
        if lesson_embed is None:
            lesson_embed = self.embedder.encode(lesson_text, convert_to_numpy=True)
            self._lesson_embed_cache[lesson_text] = lesson_embed

        # Cosine similarity (guard against zero-norm embeddings)
        query_norm = np.linalg.norm(query_embed)
        lesson_norm = np.linalg.norm(lesson_embed)
        if query_norm == 0.0 or lesson_norm == 0.0:
            return 0.0
        similarity = float(
            np.dot(query_embed, lesson_embed) / (query_norm * lesson_norm)
        )

        # Normalize from [-1, 1] to [0, 1]
        return (similarity + 1.0) / 2.0

    def _effectiveness_score(self, lesson: Lesson) -> float:
        """Effectiveness score from TS posteriors, LLM-judge verdicts, or their average (0.0-1.0).

        If an effectiveness state file is configured, looks up the lesson's score.
        Score may come from a TS posterior, an LLM-judge score, or the average of both
        when the arm carries combined data. Falls back to neutral 0.5 if no data is
        available for this lesson.
        """
        if not self._ts_posteriors:
            return 0.5

        for key in _lesson_lookup_keys(lesson):
            if key in self._ts_posteriors:
                return self._ts_posteriors[key]

        return 0.5

    def _recency_score(self, lesson: Lesson) -> float:
        """Recency score with exponential decay (0.0-1.0).

        Returns 1.0 (assume recent) since lesson files don't carry
        an ``updated`` timestamp.  If recency tracking is added to
        lesson metadata in the future, use exponential decay here:
        ``exp(-days_since_update / config.recency_decay_days)``.
        """
        return 1.0

    def _tool_bonus(self, lesson: Lesson, context: MatchContext) -> float:
        """Tool match bonus (0.0 or configured bonus)."""
        if not context.tools_used or not lesson.metadata.tools:
            return 0.0

        tools_lower = {t.lower() for t in context.tools_used}
        for tool in lesson.metadata.tools:
            if tool.lower() in tools_lower:
                return self.config.tool_bonus

        return 0.0
