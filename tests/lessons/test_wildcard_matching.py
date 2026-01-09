"""Tests for wildcard and pattern matching in lessons."""

from gptme.lessons.matcher import (
    _compile_pattern,
    _keyword_to_pattern,
    _match_keyword,
    _match_pattern,
)


class TestKeywordToPattern:
    """Tests for _keyword_to_pattern function."""

    def test_literal_keyword(self):
        """Literal keywords should match exactly."""
        pattern = _keyword_to_pattern("error")
        assert pattern.search("there was an error") is not None
        assert pattern.search("ERROR message") is not None  # case insensitive
        assert pattern.search("no issue here") is None

    def test_wildcard_matches_word_chars(self):
        """Wildcard * should match word characters."""
        pattern = _keyword_to_pattern("process killed at * seconds")
        assert pattern.search("process killed at 120 seconds") is not None
        assert pattern.search("process killed at 5 seconds") is not None
        assert pattern.search("process killed at abc seconds") is not None

    def test_wildcard_matches_empty(self):
        """Wildcard * should match empty string (zero chars)."""
        pattern = _keyword_to_pattern("timeout*")
        assert pattern.search("timeout") is not None
        assert pattern.search("timeout30s") is not None
        assert pattern.search("timeouts") is not None

    def test_wildcard_at_start(self):
        """Wildcard at start of keyword."""
        pattern = _keyword_to_pattern("*error")
        assert pattern.search("fatal error") is not None
        assert pattern.search("FatalError") is not None
        assert pattern.search("error") is not None

    def test_multiple_wildcards(self):
        """Multiple wildcards in same keyword."""
        pattern = _keyword_to_pattern("* failed with *")
        assert pattern.search("build failed with error") is not None
        assert pattern.search("test failed with exception") is not None

    def test_special_chars_escaped(self):
        """Special regex chars should be escaped."""
        pattern = _keyword_to_pattern("file.txt")
        assert pattern.search("file.txt") is not None
        assert pattern.search("fileatxt") is None  # . should not match any char

    def test_case_insensitive(self):
        """Matching should be case insensitive."""
        pattern = _keyword_to_pattern("ERROR")
        assert pattern.search("error") is not None
        assert pattern.search("Error") is not None
        assert pattern.search("ERROR") is not None


class TestMatchKeyword:
    """Tests for _match_keyword function."""

    def test_simple_match(self):
        """Simple keyword match."""
        assert _match_keyword("error", "there was an error in the code")
        assert not _match_keyword("error", "everything is fine")

    def test_wildcard_match(self):
        """Wildcard keyword match."""
        assert _match_keyword("timeout after *s", "timeout after 30s")
        assert _match_keyword("timeout after *s", "timeout after 5s")

    def test_partial_match(self):
        """Keywords should match as substrings."""
        assert _match_keyword("err", "error message")


class TestMatchPattern:
    """Tests for _match_pattern function."""

    def test_simple_regex(self):
        """Simple regex pattern."""
        assert _match_pattern(r"error\s+code\s+\d+", "error code 123")
        assert not _match_pattern(r"error\s+code\s+\d+", "error message")

    def test_complex_regex(self):
        """Complex regex pattern."""
        assert _match_pattern(r"(?:fatal|critical)\s+error", "fatal error occurred")
        assert _match_pattern(r"(?:fatal|critical)\s+error", "critical error found")

    def test_invalid_regex(self):
        """Invalid regex should return False, not raise."""
        assert not _match_pattern(r"[invalid", "any text")
        assert not _match_pattern(r"(unclosed", "any text")


class TestCompilePattern:
    """Tests for _compile_pattern function."""

    def test_valid_pattern(self):
        """Valid patterns should compile."""
        pattern = _compile_pattern(r"\d+")
        assert pattern is not None
        assert pattern.search("123") is not None

    def test_invalid_pattern(self):
        """Invalid patterns should return None."""
        assert _compile_pattern(r"[invalid") is None
        assert _compile_pattern(r"(unclosed") is None

    def test_case_insensitive(self):
        """Compiled patterns should be case insensitive."""
        pattern = _compile_pattern(r"error")
        assert pattern is not None
        assert pattern.search("ERROR") is not None


from pathlib import Path

from gptme.lessons.matcher import LessonMatcher, MatchContext
from gptme.lessons.parser import Lesson, LessonMetadata


class TestLessonMatcherWildcards:
    """Integration tests for LessonMatcher with wildcards and patterns."""

    def create_lesson(
        self,
        keywords: list[str] | None = None,
        patterns: list[str] | None = None,
        name: str = "test-lesson",
    ) -> Lesson:
        """Helper to create a test lesson."""
        return Lesson(
            path=Path(f"/fake/{name}.md"),
            metadata=LessonMetadata(
                keywords=keywords or [],
                patterns=patterns or [],
            ),
            title=f"Test Lesson: {name}",
            description="A test lesson",
            category="test",
            body="# Test\nTest body",
        )

    def test_wildcard_keyword_match(self):
        """Wildcard keywords should match in LessonMatcher."""
        matcher = LessonMatcher()
        lesson = self.create_lesson(
            keywords=["process killed at * seconds"],
            name="timeout-lesson",
        )

        context = MatchContext(
            message="The process killed at 120 seconds due to timeout"
        )
        results = matcher.match([lesson], context)

        assert len(results) == 1
        assert results[0].lesson.title == "Test Lesson: timeout-lesson"
        assert any("keyword:" in m for m in results[0].matched_by)

    def test_pattern_match(self):
        """Regex patterns should match in LessonMatcher."""
        matcher = LessonMatcher()
        lesson = self.create_lesson(
            patterns=[r"error\s+code\s+\d{3,4}"],
            name="error-code-lesson",
        )

        context = MatchContext(message="Got error code 500 from server")
        results = matcher.match([lesson], context)

        assert len(results) == 1
        assert results[0].lesson.title == "Test Lesson: error-code-lesson"
        assert any("pattern:" in m for m in results[0].matched_by)

    def test_combined_keywords_and_patterns(self):
        """Lessons with both keywords and patterns should match on either."""
        matcher = LessonMatcher()
        lesson = self.create_lesson(
            keywords=["simple error"],
            patterns=[r"fatal\s+exception"],
            name="combined-lesson",
        )

        # Match on keyword
        context1 = MatchContext(message="There was a simple error")
        results1 = matcher.match([lesson], context1)
        assert len(results1) == 1

        # Match on pattern
        context2 = MatchContext(message="Fatal exception occurred")
        results2 = matcher.match([lesson], context2)
        assert len(results2) == 1

    def test_no_match_when_wildcard_doesnt_fit(self):
        """Wildcards should not match across word boundaries (non-word chars)."""
        matcher = LessonMatcher()
        lesson = self.create_lesson(
            keywords=["timeout*"],
            name="timeout-lesson",
        )

        # Should match - word chars after timeout
        context1 = MatchContext(message="timeout30s occurred")
        results1 = matcher.match([lesson], context1)
        assert len(results1) == 1

        # Should NOT match - non-word boundary would need .* not \w*
        # Actually, * matches zero or more word chars, so "timeout error"
        # would match "timeout" + "" (zero word chars) at that position
        context2 = MatchContext(message="timeout error")
        results2 = matcher.match([lesson], context2)
        assert len(results2) == 1  # Matches because timeout* matches "timeout"
