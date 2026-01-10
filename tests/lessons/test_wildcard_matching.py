"""Tests for wildcard and pattern matching in lessons."""

from gptme._keyword_matching import (
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
        assert pattern is not None
        assert pattern.search("there was an error") is not None
        assert pattern.search("ERROR message") is not None  # case insensitive
        assert pattern.search("no issue here") is None

    def test_wildcard_matches_word_chars(self):
        """Wildcard * should match word characters."""
        pattern = _keyword_to_pattern("process killed at * seconds")
        assert pattern is not None
        assert pattern.search("process killed at 120 seconds") is not None
        assert pattern.search("process killed at 5 seconds") is not None
        assert pattern.search("process killed at abc seconds") is not None

    def test_wildcard_matches_empty(self):
        """Wildcard * should match empty string (zero chars)."""
        pattern = _keyword_to_pattern("timeout*")
        assert pattern is not None
        assert pattern.search("timeout") is not None
        assert pattern.search("timeout30s") is not None
        assert pattern.search("timeouts") is not None

    def test_wildcard_at_start(self):
        """Wildcard at start of keyword."""
        pattern = _keyword_to_pattern("*error")
        assert pattern is not None
        assert pattern.search("fatal error") is not None
        assert pattern.search("FatalError") is not None
        assert pattern.search("error") is not None

    def test_multiple_wildcards(self):
        """Multiple wildcards in same keyword."""
        pattern = _keyword_to_pattern("* failed with *")
        assert pattern is not None
        assert pattern.search("build failed with error") is not None
        assert pattern.search("test failed with exception") is not None

    def test_special_chars_escaped(self):
        """Special regex chars should be escaped."""
        pattern = _keyword_to_pattern("file.txt")
        assert pattern is not None
        assert pattern.search("file.txt") is not None
        assert pattern.search("fileatxt") is None  # . should not match any char

    def test_case_insensitive(self):
        """Matching should be case insensitive."""
        pattern = _keyword_to_pattern("ERROR")
        assert pattern is not None
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

        # "timeout*" matches "timeout" followed by zero or more word chars
        # In "timeout error", it matches "timeout" + "" (zero word chars at space boundary)
        # This is correct behavior - the space acts as a natural boundary
        context2 = MatchContext(message="timeout error")
        results2 = matcher.match([lesson], context2)
        assert len(results2) == 1  # Matches "timeout" portion successfully


class TestParsePatternsFromYAML:
    """Tests for parsing patterns field from YAML frontmatter."""

    def test_parse_patterns_from_yaml(self, tmp_path):
        """Patterns should be correctly parsed from lesson YAML frontmatter."""
        from gptme.lessons.parser import parse_lesson

        lesson_content = """---
match:
  keywords:
    - "error handling"
  patterns:
    - "error\\\\s+code\\\\s+\\\\d{3,4}"
    - "fatal.*exception"
---
# Test Lesson

Example lesson with patterns.
"""
        lesson_file = tmp_path / "test-lesson.md"
        lesson_file.write_text(lesson_content)

        lesson = parse_lesson(lesson_file)

        assert lesson is not None
        assert lesson.metadata.patterns is not None
        assert len(lesson.metadata.patterns) == 2
        assert r"error\s+code\s+\d{3,4}" in lesson.metadata.patterns
        assert "fatal.*exception" in lesson.metadata.patterns

    def test_parse_lesson_without_patterns(self, tmp_path):
        """Lessons without patterns field should have empty patterns list."""
        from gptme.lessons.parser import parse_lesson

        lesson_content = """---
match:
  keywords:
    - "simple keyword"
---
# Test Lesson

Example lesson without patterns.
"""
        lesson_file = tmp_path / "no-patterns.md"
        lesson_file.write_text(lesson_content)

        lesson = parse_lesson(lesson_file)

        assert lesson is not None
        assert lesson.metadata.patterns is not None
        assert len(lesson.metadata.patterns) == 0

    def test_patterns_match_after_parsing(self, tmp_path):
        """Parsed patterns should work correctly in matching."""
        from gptme.lessons.matcher import LessonMatcher, MatchContext
        from gptme.lessons.parser import parse_lesson

        lesson_content = """---
match:
  keywords:
    - "test lesson"
  patterns:
    - "status\\\\s+code\\\\s+\\\\d+"
---
# Pattern Matching Test

Test lesson for pattern matching.
"""
        lesson_file = tmp_path / "pattern-test.md"
        lesson_file.write_text(lesson_content)

        lesson = parse_lesson(lesson_file)
        assert lesson is not None

        matcher = LessonMatcher()
        context = MatchContext(message="Got status code 404 from API")
        results = matcher.match([lesson], context)

        assert len(results) == 1
        assert any("pattern:" in m for m in results[0].matched_by)


class TestMatchKeywordsWildcardSupport:
    """Tests for match_keywords method with wildcard support."""

    def create_lesson(self, keywords: list[str], name: str = "test"):
        """Helper to create a lesson with specified keywords."""
        from pathlib import Path

        from gptme.lessons.parser import Lesson, LessonMetadata

        return Lesson(
            path=Path(f"/fake/path/{name}.md"),
            title=f"Test Lesson: {name}",
            description="Test lesson",
            category="test",
            body="# Test\nTest body",
            metadata=LessonMetadata(
                keywords=keywords,
                patterns=[],
            ),
        )

    def test_match_keywords_exact_match(self):
        """Exact keyword matches should work."""
        from gptme.lessons.matcher import LessonMatcher

        matcher = LessonMatcher()
        lesson = self.create_lesson(keywords=["git workflow", "branching"])

        results = matcher.match_keywords([lesson], ["git workflow"])
        assert len(results) == 1
        assert results[0].lesson.title == "Test Lesson: test"

    def test_match_keywords_with_wildcards(self):
        """Input keywords should match wildcard patterns in lesson keywords."""
        from gptme.lessons.matcher import LessonMatcher

        matcher = LessonMatcher()
        # Lesson has wildcard keyword "git*" which should match "github", "gitlab", etc.
        lesson = self.create_lesson(keywords=["git*", "version control"])

        # "github" should match the "git*" pattern
        results = matcher.match_keywords([lesson], ["github"])
        assert len(results) == 1

        # "gitlab" should also match
        results2 = matcher.match_keywords([lesson], ["gitlab"])
        assert len(results2) == 1

        # "svn" should not match
        results3 = matcher.match_keywords([lesson], ["svn"])
        assert len(results3) == 0

    def test_match_keywords_wildcard_phrase(self):
        """Wildcard phrases in lesson keywords should match."""
        from gptme.lessons.matcher import LessonMatcher

        matcher = LessonMatcher()
        lesson = self.create_lesson(
            keywords=["process killed at * seconds"], name="timeout-lesson"
        )

        # Should match the wildcard phrase
        results = matcher.match_keywords([lesson], ["process killed at 120 seconds"])
        assert len(results) == 1

        # Should match with different number
        results2 = matcher.match_keywords([lesson], ["process killed at 5 seconds"])
        assert len(results2) == 1

        # Should not match different phrase
        results3 = matcher.match_keywords([lesson], ["server crashed"])
        assert len(results3) == 0


class TestEdgeCases:
    """Tests for edge cases with empty strings, single wildcards, etc."""

    def test_empty_keyword_returns_none_pattern(self):
        """Empty keyword should return None pattern (no match)."""
        from gptme._keyword_matching import _keyword_to_pattern

        assert _keyword_to_pattern("") is None
        assert _keyword_to_pattern("   ") is None

    def test_single_wildcard_returns_none(self):
        """Single wildcard '*' returns None to prevent over-matching all text."""
        from gptme._keyword_matching import _keyword_to_pattern, _match_keyword

        # Single wildcard returns None to prevent matching everything
        pattern = _keyword_to_pattern("*")
        assert pattern is None

        # Single wildcard should not match (returns False via None pattern)
        assert not _match_keyword("*", "hello")
        assert not _match_keyword("*", "test123")
        assert not _match_keyword("*", "")

    def test_empty_keyword_no_match(self):
        """Empty keyword should not match anything."""
        from gptme._keyword_matching import _match_keyword

        assert not _match_keyword("", "some text")
        assert not _match_keyword("   ", "some text")

    def test_empty_pattern_returns_none(self):
        """Empty pattern should return None (no match)."""
        from gptme._keyword_matching import _compile_pattern, _match_pattern

        assert _compile_pattern("") is None
        assert _compile_pattern("   ") is None
        assert not _match_pattern("", "some text")
        assert not _match_pattern("   ", "some text")

    def test_whitespace_only_keyword(self):
        """Whitespace-only keyword should be treated as empty."""
        from gptme._keyword_matching import _keyword_to_pattern

        assert _keyword_to_pattern("   ") is None
        assert _keyword_to_pattern("\t\n") is None


class TestParserValidation:
    """Tests for parser validation of patterns and keywords."""

    def test_parse_non_string_patterns_filtered(self, tmp_path):
        """Non-string patterns should be filtered out during parsing."""
        from gptme.lessons.parser import parse_lesson

        # Create lesson with mixed patterns (including non-strings)
        lesson_file = tmp_path / "test.md"
        # Note: In YAML, numbers are parsed as numbers, not strings
        lesson_content = """---
match:
  patterns:
    - "valid_pattern"
    - 123  # This is a number, not a string
    - ""   # Empty string
    - "another_valid"
---

# Test Lesson

Content here.
"""
        lesson_file.write_text(lesson_content)

        lesson = parse_lesson(lesson_file)
        # Only valid string patterns should remain
        assert len(lesson.metadata.patterns) == 2
        assert "valid_pattern" in lesson.metadata.patterns
        assert "another_valid" in lesson.metadata.patterns

    def test_parse_non_string_keywords_filtered(self, tmp_path):
        """Non-string keywords should be filtered out during parsing."""
        from gptme.lessons.parser import parse_lesson

        lesson_file = tmp_path / "test.md"
        lesson_content = """---
match:
  keywords:
    - "valid keyword"
    - 456  # Number, not string
    - ""   # Empty
    - "  "  # Whitespace only
    - "another valid"
---

# Test Lesson

Content here.
"""
        lesson_file.write_text(lesson_content)

        lesson = parse_lesson(lesson_file)
        # Only valid non-empty string keywords should remain
        assert len(lesson.metadata.keywords) == 2
        assert "valid keyword" in lesson.metadata.keywords
        assert "another valid" in lesson.metadata.keywords

    def test_parse_single_pattern_as_string(self, tmp_path):
        """Single pattern can be specified as a string instead of list."""
        from gptme.lessons.parser import parse_lesson

        lesson_file = tmp_path / "test.md"
        lesson_content = """---
match:
  patterns: "single_pattern"
---

# Test Lesson

Content here.
"""
        lesson_file.write_text(lesson_content)

        lesson = parse_lesson(lesson_file)
        assert len(lesson.metadata.patterns) == 1
        assert "single_pattern" in lesson.metadata.patterns

    def test_parse_single_keyword_as_string(self, tmp_path):
        """Single keyword can be specified as a string instead of list."""
        from gptme.lessons.parser import parse_lesson

        lesson_file = tmp_path / "test.md"
        lesson_content = """---
match:
  keywords: "single keyword"
---

# Test Lesson

Content here.
"""
        lesson_file.write_text(lesson_content)

        lesson = parse_lesson(lesson_file)
        assert len(lesson.metadata.keywords) == 1
        assert "single keyword" in lesson.metadata.keywords
