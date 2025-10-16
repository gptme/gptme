"""Tests for lesson matcher."""

import pytest
from gptme.lessons.matcher import (
    LessonMatcher,
    MatchContext,
    MatchResult,
)
from gptme.lessons.parser import Lesson, LessonMetadata
from pathlib import Path


@pytest.fixture
def sample_lessons():
    """Create sample lessons for testing."""
    return [
        Lesson(
            title="Patch Lesson",
            category="tools",
            description="Patch best practices",
            body="# Patch Lesson\n\nContent",
            metadata=LessonMetadata(keywords=["patch", "file", "edit"]),
            path=Path("/lessons/patch.md"),
        ),
        Lesson(
            title="Shell Lesson",
            category="tools",
            description="Shell commands",
            body="# Shell Lesson\n\nContent",
            metadata=LessonMetadata(keywords=["shell", "command", "terminal"]),
            path=Path("/lessons/shell.md"),
        ),
        Lesson(
            title="Browser Lesson",
            category="tools",
            description="Browser usage",
            body="# Browser Lesson\n\nContent",
            metadata=LessonMetadata(keywords=["browser", "web", "http"]),
            path=Path("/lessons/browser.md"),
        ),
    ]


class TestMatchContext:
    """Tests for MatchContext dataclass."""

    def test_match_context_creation(self):
        """Test creating MatchContext."""
        context = MatchContext(message="test message")
        assert context.message == "test message"
        assert context.tools_used is None

    def test_match_context_with_tools(self):
        """Test MatchContext with tools."""
        context = MatchContext(message="test", tools_used=["shell", "patch"])
        assert context.tools_used == ["shell", "patch"]


class TestMatchResult:
    """Tests for MatchResult dataclass."""

    def test_match_result_creation(self, sample_lessons):
        """Test creating MatchResult."""
        result = MatchResult(
            lesson=sample_lessons[0],
            score=1.0,
            matched_by=["keyword:patch"],
        )
        assert result.lesson == sample_lessons[0]
        assert result.score == 1.0
        assert result.matched_by == ["keyword:patch"]


class TestLessonMatcher:
    """Tests for LessonMatcher class."""

    def test_matcher_single_keyword_match(self, sample_lessons):
        """Test matching with single keyword."""
        matcher = LessonMatcher()
        context = MatchContext(message="How do I use the patch tool?")

        results = matcher.match(sample_lessons, context)

        assert len(results) == 1
        assert results[0].lesson.title == "Patch Lesson"
        assert results[0].score == 1.0
        assert "keyword:patch" in results[0].matched_by

    def test_matcher_multiple_keyword_match(self, sample_lessons):
        """Test matching with multiple keywords."""
        matcher = LessonMatcher()
        context = MatchContext(message="Use patch to edit the file")

        results = matcher.match(sample_lessons, context)

        assert len(results) == 1
        assert results[0].lesson.title == "Patch Lesson"
        assert results[0].score == 3.0  # patch, file, edit
        assert len(results[0].matched_by) == 3

    def test_matcher_no_matches(self, sample_lessons):
        """Test matching with no keyword matches."""
        matcher = LessonMatcher()
        context = MatchContext(message="Something completely unrelated")

        results = matcher.match(sample_lessons, context)

        assert len(results) == 0

    def test_matcher_multiple_lessons_match(self, sample_lessons):
        """Test matching multiple lessons."""
        matcher = LessonMatcher()
        context = MatchContext(message="Use shell and browser tools")

        results = matcher.match(sample_lessons, context)

        assert len(results) == 2
        # Results should be sorted by score
        assert results[0].score >= results[1].score

    def test_matcher_case_insensitive(self, sample_lessons):
        """Test that matching is case insensitive."""
        matcher = LessonMatcher()
        context = MatchContext(message="PATCH the FILE")

        results = matcher.match(sample_lessons, context)

        assert len(results) == 1
        assert results[0].lesson.title == "Patch Lesson"

    def test_matcher_with_threshold(self, sample_lessons):
        """Test matching with score threshold."""
        matcher = LessonMatcher()
        context = MatchContext(message="patch file edit")

        # With threshold 2.0, should only include lessons with 2+ matches
        results = matcher.match(sample_lessons, context, threshold=2.0)

        assert len(results) == 1
        assert results[0].score > 2.0

    def test_matcher_sorting_by_score(self):
        """Test that results are sorted by score descending."""
        lessons = [
            Lesson(
                title="Lesson A",
                category="tools",
                description="Description A",
                body="Body A",
                metadata=LessonMetadata(keywords=["one"]),
                path=Path("/a.md"),
            ),
            Lesson(
                title="Lesson B",
                category="tools",
                description="Description B",
                body="Body B",
                metadata=LessonMetadata(keywords=["one", "two", "three"]),
                path=Path("/b.md"),
            ),
            Lesson(
                title="Lesson C",
                category="tools",
                description="Description C",
                body="Body C",
                metadata=LessonMetadata(keywords=["one", "two"]),
                path=Path("/c.md"),
            ),
        ]

        matcher = LessonMatcher()
        context = MatchContext(message="one two three")

        results = matcher.match(lessons, context)

        # Should be sorted: B (3), C (2), A (1)
        assert len(results) == 3
        assert results[0].lesson.title == "Lesson B"
        assert results[1].lesson.title == "Lesson C"
        assert results[2].lesson.title == "Lesson A"

    def test_match_keywords_explicit(self, sample_lessons):
        """Test match_keywords method."""
        matcher = LessonMatcher()
        keywords = ["shell", "terminal"]

        results = matcher.match_keywords(sample_lessons, keywords)

        assert len(results) == 1
        assert results[0].lesson.title == "Shell Lesson"
        assert results[0].score == 2.0
        assert len(results[0].matched_by) == 2

    def test_match_keywords_no_matches(self, sample_lessons):
        """Test match_keywords with no matches."""
        matcher = LessonMatcher()
        keywords = ["nonexistent", "missing"]

        results = matcher.match_keywords(sample_lessons, keywords)

        assert len(results) == 0

    def test_match_keywords_sorting(self):
        """Test that match_keywords sorts by score."""
        lessons = [
            Lesson(
                title="Lesson A",
                category="tools",
                description="Description A",
                body="Body A",
                metadata=LessonMetadata(keywords=["key1"]),
                path=Path("/a.md"),
            ),
            Lesson(
                title="Lesson B",
                category="tools",
                description="Description B",
                body="Body B",
                metadata=LessonMetadata(keywords=["key1", "key2", "key3"]),
                path=Path("/b.md"),
            ),
        ]

        matcher = LessonMatcher()
        keywords = ["key1", "key2", "key3"]

        results = matcher.match_keywords(lessons, keywords)

        assert len(results) == 2
        assert results[0].lesson.title == "Lesson B"  # Higher score
        assert results[1].lesson.title == "Lesson A"  # Lower score
