"""Tests for BM25 z-score semantic scoring in HybridLessonMatcher.

Verifies parity with the CC hook's BM25 gate (gptme-contrib#1371):
- _bm25_zscores standardizes against per-query distribution
- _bm25_min_z scales down for small corpora
- HybridLessonMatcher falls back to BM25+keyword (not keyword-only) when
  sentence_transformers are unavailable
"""

import math
from pathlib import Path

import pytest

from gptme.lessons.hybrid_matcher import (
    _BM25_MIN_Z,
    _BM25_STANDOUT_FRACTION,
    HybridConfig,
    HybridLessonMatcher,
    _bm25_min_z,
    _bm25_score,
    _bm25_zscores,
    _build_bm25_index,
)
from gptme.lessons.matcher import MatchContext
from gptme.lessons.parser import Lesson, LessonMetadata

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_lesson(
    name: str,
    description: str = "",
    keywords: list[str] | None = None,
    title: str = "",
) -> Lesson:
    return Lesson(
        path=Path(f"/fake/lessons/{name}.md"),
        metadata=LessonMetadata(
            keywords=keywords or [],
            description=description,
        ),
        title=title or name,
        description=description,
        category="test",
        body=f"# {name}\n{description}",
    )


def make_matcher_no_embedder() -> HybridLessonMatcher:
    """Return a HybridLessonMatcher with embeddings forcibly disabled."""
    m = HybridLessonMatcher(HybridConfig(enable_semantic=False))
    m.embedder = None  # ensure embedder is None regardless of env
    return m


# ---------------------------------------------------------------------------
# _bm25_zscores
# ---------------------------------------------------------------------------


class TestBm25Zscores:
    def test_empty(self):
        assert _bm25_zscores([]) == []

    def test_all_zero(self):
        # No nonzero scores → all z = 0.0
        assert _bm25_zscores([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]

    def test_single_nonzero(self):
        # Only one nonzero → degenerate, all z = 0.0
        result = _bm25_zscores([5.0, 0.0, 0.0])
        assert result == [0.0, 0.0, 0.0]

    def test_two_nonzero_ordered(self):
        # Two nonzero: z-scores are ±1 (symmetric). Higher raw → higher z.
        scores = [10.0, 2.0, 0.0]
        zs = _bm25_zscores(scores)
        assert zs[0] > zs[1]  # stronger match ranks higher
        assert zs[2] == 0.0  # zero-score gets 0.0

    def test_three_nonzero_standout(self):
        # The clear outlier should have a large positive z-score.
        scores = [100.0, 3.0, 3.0]
        zs = _bm25_zscores(scores)
        assert zs[0] > 0.0
        assert zs[0] > zs[1]
        assert zs[1] == zs[2]  # equal raw → equal z

    def test_all_equal_nonzero(self):
        # All equal non-zero → standard deviation is 0 → all z = 0.0
        result = _bm25_zscores([5.0, 5.0, 5.0])
        assert result == [0.0, 0.0, 0.0]

    def test_zero_scores_get_zero_z(self):
        scores = [50.0, 30.0, 0.0, 0.0]
        zs = _bm25_zscores(scores)
        assert zs[2] == 0.0
        assert zs[3] == 0.0


# ---------------------------------------------------------------------------
# _bm25_min_z
# ---------------------------------------------------------------------------


class TestBm25MinZ:
    def test_zero_nonzero(self):
        # n<3 → -inf (admit anything that passes the raw floor)
        assert _bm25_min_z(0) == -math.inf

    def test_one_nonzero(self):
        assert _bm25_min_z(1) == -math.inf

    def test_two_nonzero(self):
        assert _bm25_min_z(2) == -math.inf

    def test_three_nonzero_below_fixed(self):
        # With n=3, max attainable z ≈ 2/sqrt(3) ≈ 1.15, well below _BM25_MIN_Z=4.
        result = _bm25_min_z(3)
        max_attainable = (3 - 1) / math.sqrt(3)
        assert result == pytest.approx(_BM25_STANDOUT_FRACTION * max_attainable)
        assert result < _BM25_MIN_Z

    def test_large_corpus_caps_at_min_z(self):
        # With n=1000, max attainable ≫ _BM25_MIN_Z; result should be _BM25_MIN_Z.
        result = _bm25_min_z(1000)
        assert result == pytest.approx(_BM25_MIN_Z)

    def test_monotone(self):
        # Threshold should be non-decreasing with corpus size.
        thresholds = [_bm25_min_z(n) for n in range(3, 50)]
        for a, b in zip(thresholds, thresholds[1:]):
            assert a <= b + 1e-9


# ---------------------------------------------------------------------------
# _bm25_score
# ---------------------------------------------------------------------------


class TestBm25Score:
    def _index_from_docs(self, docs: list[str]) -> dict:
        """Build a minimal BM25 index from raw text strings."""
        import re

        corpus = [re.findall(r"[a-z0-9]+", d.lower()) for d in docs]
        N = len(corpus)
        avg_dl = sum(len(d) for d in corpus) / max(N, 1)
        df: dict[str, int] = {}
        for tokens in corpus:
            for t in set(tokens):
                df[t] = df.get(t, 0) + 1
        return {"corpus": corpus, "df": df, "N": N, "avg_dl": avg_dl}

    def test_empty_query(self):
        idx = self._index_from_docs(["hello world"])
        assert _bm25_score([], ["hello", "world"], idx) == 0.0

    def test_empty_doc(self):
        idx = self._index_from_docs([""])
        assert _bm25_score(["hello"], [], idx) == 0.0

    def test_exact_match_scores_positive(self):
        idx = self._index_from_docs(["git commit amend workflow"])
        score = _bm25_score(["git", "commit"], ["git", "commit", "amend"], idx)
        assert score > 0.0

    def test_no_overlap_zero(self):
        idx = self._index_from_docs(["database schema migration"])
        score = _bm25_score(["git", "commit"], ["database", "schema", "migration"], idx)
        assert score == 0.0

    def test_more_overlap_higher_score(self):
        idx = self._index_from_docs(["git commit amend", "database schema"])
        s1 = _bm25_score(["git", "commit"], ["git", "commit", "amend"], idx)
        s2 = _bm25_score(["git", "commit"], ["database", "schema"], idx)
        assert s1 > s2


# ---------------------------------------------------------------------------
# _build_bm25_index
# ---------------------------------------------------------------------------


class TestBuildBm25Index:
    def test_returns_expected_keys(self):
        lessons = [make_lesson("a", description="git commit amend guard")]
        idx = _build_bm25_index(lessons)
        assert "corpus" in idx
        assert "df" in idx
        assert "N" in idx
        assert "avg_dl" in idx

    def test_corpus_length_matches_lesson_count(self):
        lessons = [make_lesson(f"l{i}") for i in range(5)]
        idx = _build_bm25_index(lessons)
        assert idx["N"] == 5
        assert len(idx["corpus"]) == 5

    def test_keywords_included_in_index(self):
        lessons = [make_lesson("a", keywords=["amend", "git"])]
        idx = _build_bm25_index(lessons)
        assert "amend" in idx["df"]
        assert "git" in idx["df"]

    def test_empty_lessons(self):
        idx = _build_bm25_index([])
        assert idx["N"] == 0
        assert idx["corpus"] == []


# ---------------------------------------------------------------------------
# HybridLessonMatcher BM25 fallback (no embedder)
# ---------------------------------------------------------------------------


class TestHybridMatcherBm25Fallback:
    """When embeddings are unavailable, match() must use BM25+keyword scoring."""

    def _make_corpus(self) -> list[Lesson]:
        """A small diverse corpus for integration tests."""
        return [
            make_lesson(
                "git-amend",
                description="guard git commit amend shared worktree index sweep",
                keywords=["git commit --amend", "amend guard"],
            ),
            make_lesson(
                "bm25-scoring",
                description="bm25 relevance ranking term frequency inverse document frequency",
                keywords=[],
            ),
            make_lesson(
                "unrelated-topic",
                description="kubernetes pod resource limits memory cpu quota",
                keywords=[],
            ),
            make_lesson(
                "another-unrelated",
                description="ansible playbook idempotent deployment automation",
                keywords=[],
            ),
            make_lesson(
                "yet-another",
                description="docker image layer caching build performance",
                keywords=[],
            ),
        ]

    def test_no_embedder_calls_bm25_not_keyword_only(self):
        """BM25-matched lesson should appear even when it has no matching keyword."""
        corpus = self._make_corpus()
        matcher = make_matcher_no_embedder()
        context = MatchContext(
            message="bm25 term frequency relevance ranking lesson system"
        )
        results = matcher.match(corpus, context)
        slugs = [r.lesson.path.stem for r in results]
        # bm25-scoring has no keywords but IS the topic; it should surface via BM25
        assert "bm25-scoring" in slugs

    def test_bm25_tag_in_matched_by(self):
        """BM25-matched lessons should have a bm25:* tag in matched_by."""
        corpus = self._make_corpus()
        matcher = make_matcher_no_embedder()
        context = MatchContext(
            message="bm25 term frequency relevance ranking lesson system"
        )
        results = matcher.match(corpus, context)
        bm25_hit = next(
            (r for r in results if r.lesson.path.stem == "bm25-scoring"), None
        )
        assert bm25_hit is not None
        assert any(tag.startswith("bm25:") for tag in bm25_hit.matched_by)

    def test_keyword_match_still_works(self):
        """Keyword-backed lessons should still surface in the BM25 path."""
        corpus = self._make_corpus()
        matcher = make_matcher_no_embedder()
        context = MatchContext(message="I need to git commit --amend the last commit")
        results = matcher.match(corpus, context)
        slugs = [r.lesson.path.stem for r in results]
        assert "git-amend" in slugs

    def test_unrelated_prompt_suppresses_weak_matches(self):
        """A very focused prompt should not return every lesson in the corpus."""
        corpus = self._make_corpus()
        matcher = make_matcher_no_embedder()
        context = MatchContext(message="amend the last git commit safely")
        results = matcher.match(corpus, context)
        # Should NOT surface kubernetes or ansible lessons
        slugs = [r.lesson.path.stem for r in results]
        assert "unrelated-topic" not in slugs
        assert "another-unrelated" not in slugs

    def test_bm25_index_cached_across_calls(self):
        """Index should be rebuilt only when the lesson set changes."""
        corpus = self._make_corpus()
        matcher = make_matcher_no_embedder()
        ctx = MatchContext(message="git commit amend guard")
        matcher.match(corpus, ctx)
        index_id_first = id(matcher._bm25_index)
        matcher.match(corpus, ctx)
        assert id(matcher._bm25_index) == index_id_first  # same object reused

    def test_bm25_index_rebuilt_on_lesson_change(self):
        """Index should rebuild when a different lesson set is passed."""
        corpus = self._make_corpus()
        matcher = make_matcher_no_embedder()
        ctx = MatchContext(message="test query")
        matcher.match(corpus, ctx)
        new_corpus = corpus[:-1]  # remove one lesson
        matcher.match(new_corpus, ctx)
        assert matcher._bm25_index is not None
        assert matcher._bm25_index["N"] == len(new_corpus)

    def test_two_lesson_corpus_both_returned(self):
        """With only 2 highly relevant lessons, both should surface (n<3 gate)."""
        corpus = [
            make_lesson("a", description="git commit amend rewrite history"),
            make_lesson("b", description="git commit message conventional format"),
        ]
        matcher = make_matcher_no_embedder()
        ctx = MatchContext(message="how to write a git commit message")
        results = matcher.match(corpus, ctx)
        assert len(results) == 2

    def test_skill_name_match_in_bm25_path(self):
        """Skills matched by metadata.name must surface in the no-embedder path.

        Regression guard for the P1 Greptile finding: _match_with_bm25 previously
        omitted the name-variant matching present in LessonMatcher.match, causing
        name-only skills to score 0 and disappear from auto-injected lessons.
        """
        skill_lesson = Lesson(
            path=Path("/fake/lessons/python-repl.md"),
            metadata=LessonMetadata(keywords=[], name="python-repl"),
            title="Python REPL",
            description="",
            category="test",
            body="# Python REPL",
        )
        corpus = [skill_lesson] + self._make_corpus()
        matcher = make_matcher_no_embedder()

        # Plain name
        results = matcher.match(
            corpus, MatchContext(message="use the python-repl skill")
        )
        slugs = [r.lesson.path.stem for r in results]
        assert "python-repl" in slugs, "name match (hyphen) failed"

        # Hyphen→space variant
        results = matcher.match(corpus, MatchContext(message="run python repl now"))
        slugs = [r.lesson.path.stem for r in results]
        assert "python-repl" in slugs, "name match (space variant) failed"

        # Matched_by tag must reflect skill: prefix
        hit = next(r for r in results if r.lesson.path.stem == "python-repl")
        assert any(tag.startswith("skill:") for tag in hit.matched_by)

    def test_result_count_capped_by_max_lessons(self):
        """Should not exceed config.max_lessons even with a broad query."""
        corpus = [
            make_lesson(f"l{i}", description=f"lesson about topic {i} with details")
            for i in range(30)
        ]
        config = HybridConfig(max_lessons=5)
        matcher = make_matcher_no_embedder()
        matcher.config = config
        ctx = MatchContext(message="lesson about topic with details")
        results = matcher.match(corpus, ctx)
        assert len(results) <= 5
