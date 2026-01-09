"""Tests for lesson matcher."""

from pathlib import Path

import pytest

from gptme.lessons.matcher import (
    LessonMatcher,
    MatchContext,
    MatchResult,
)
from gptme.lessons.parser import Lesson, LessonMetadata


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


class TestSkillMatching:
    """Tests for Anthropic skill format matching (name and description)."""

    @pytest.fixture
    def sample_skills(self):
        """Create sample skills for testing."""
        return [
            Lesson(
                title="Python REPL Skill",
                category="skills",
                description="Interactive Python REPL automation",
                body="# Python REPL Skill\n\nContent",
                metadata=LessonMetadata(
                    name="python-repl",
                    description="Interactive Python REPL automation with common helpers and best practices",
                ),
                path=Path("/skills/python-repl/SKILL.md"),
            ),
            Lesson(
                title="Context Optimization",
                category="skills",
                description="Optimize context window usage",
                body="# Context Optimization\n\nContent",
                metadata=LessonMetadata(
                    name="context-optimization",
                    description="Techniques for optimizing context window usage and token efficiency",
                ),
                path=Path("/skills/context-optimization/SKILL.md"),
            ),
            Lesson(
                title="Tool Design Skill",
                category="skills",
                description="Design effective tools",
                body="# Tool Design Skill\n\nContent",
                metadata=LessonMetadata(
                    name="tool-design",
                    description="Best practices for designing effective AI assistant tools",
                ),
                path=Path("/skills/tool-design/SKILL.md"),
            ),
        ]

    def test_skill_name_match_exact(self, sample_skills):
        """Test matching skill by exact name."""
        matcher = LessonMatcher()
        context = MatchContext(message="I need help with python-repl")

        results = matcher.match(sample_skills, context)

        assert len(results) == 1
        assert results[0].lesson.title == "Python REPL Skill"
        assert "skill:python-repl" in results[0].matched_by
        assert results[0].score >= 1.5  # name match weight

    def test_skill_name_match_with_spaces(self, sample_skills):
        """Test matching skill name with spaces instead of hyphens."""
        matcher = LessonMatcher()
        context = MatchContext(message="How do I use python repl?")

        results = matcher.match(sample_skills, context)

        assert len(results) == 1
        assert results[0].lesson.title == "Python REPL Skill"
        assert "skill:python-repl" in results[0].matched_by

    def test_skill_name_match_no_separator(self, sample_skills):
        """Test matching skill name without any separator."""
        matcher = LessonMatcher()
        context = MatchContext(message="pythonrepl tips")

        results = matcher.match(sample_skills, context)

        assert len(results) == 1
        assert results[0].lesson.title == "Python REPL Skill"

    def test_skill_description_match(self, sample_skills):
        """Test matching skill by description keywords."""
        matcher = LessonMatcher()
        # Use words from description: "token efficiency"
        context = MatchContext(
            message="I need to improve token efficiency in my prompts"
        )

        results = matcher.match(sample_skills, context)

        assert len(results) >= 1
        # Should match context-optimization based on description
        skill_names = [r.lesson.title for r in results]
        assert "Context Optimization" in skill_names

    def test_skill_name_preferred_over_description(self, sample_skills):
        """Test that name match is preferred over description match."""
        matcher = LessonMatcher()
        context = MatchContext(message="python-repl usage")

        results = matcher.match(sample_skills, context)

        # Name match should take precedence
        assert len(results) >= 1
        assert results[0].lesson.title == "Python REPL Skill"
        # Should have skill: match, not description: match
        matched_types = [m.split(":")[0] for m in results[0].matched_by]
        assert "skill" in matched_types

    def test_mixed_lessons_and_skills(self, sample_lessons, sample_skills):
        """Test matching with both lessons and skills."""
        matcher = LessonMatcher()
        all_content = sample_lessons + sample_skills
        context = MatchContext(message="I need to patch a file and use python-repl")

        results = matcher.match(all_content, context)

        # Should match both lesson (patch) and skill (python-repl)
        assert len(results) >= 2
        titles = [r.lesson.title for r in results]
        assert "Patch Lesson" in titles
        assert "Python REPL Skill" in titles

    def test_skill_no_match_when_unrelated(self, sample_skills):
        """Test that skills don't match on unrelated content."""
        matcher = LessonMatcher()
        context = MatchContext(message="What is the weather like today?")

        results = matcher.match(sample_skills, context)

        # Should have no matches
        assert len(results) == 0

    def test_extract_description_keywords(self):
        """Test the _extract_description_keywords helper method."""
        matcher = LessonMatcher()

        keywords = matcher._extract_description_keywords(
            "Interactive Python REPL automation with common helpers"
        )

        # Should extract meaningful words, not stop words
        assert "interactive" in keywords
        assert "python" in keywords
        assert "repl" in keywords
        assert "automation" in keywords
        assert "helpers" in keywords
        # Stop words should be excluded
        assert "with" not in keywords

    def test_description_requires_multiple_matches(self, sample_skills):
        """Test that description matching requires at least 2 keyword matches."""
        matcher = LessonMatcher()
        # Only one word from description
        context = MatchContext(message="Something about efficiency")

        results = matcher.match(sample_skills, context)

        # Should not match based on single description word
        # (unless also matched by name)
        for result in results:
            matched_types = [m.split(":")[0] for m in result.matched_by]
            if "description" in matched_types:
                # If matched by description, should have matched by name too
                # or multiple description words
                assert result.score > 0.5

    def test_deduplication_by_resolved_path(self, tmp_path):
        """Test that duplicate lessons (same resolved path) are deduplicated.

        This tests the fix for issue #1059 where lessons matching multiple
        keywords could appear multiple times in results.
        """
        # Create a lesson file
        lesson_dir = tmp_path / "lessons"
        lesson_dir.mkdir()
        lesson_file = lesson_dir / "test-lesson.md"
        lesson_file.write_text(
            """---
match:
  keywords:
    - "keyword one"
    - "keyword two"
status: active
---
# Test Lesson

Test content.
"""
        )

        # Parse the lesson twice (simulating duplicate entries in index)
        from gptme.lessons.parser import parse_lesson

        lesson1 = parse_lesson(lesson_file)
        lesson2 = parse_lesson(lesson_file)

        # Both should have the same resolved path
        assert lesson1.path == lesson2.path

        # Create list with duplicates (simulating a bug in indexing)
        lessons_with_duplicates = [lesson1, lesson2]

        # Match should deduplicate
        matcher = LessonMatcher()
        context = MatchContext(message="This contains keyword one and keyword two")

        results = matcher.match(lessons_with_duplicates, context)

        # Should only return ONE result despite two entries in input
        assert len(results) == 1
        assert results[0].lesson.title == "Test Lesson"
        # Should have both keywords in matched_by
        assert "keyword:keyword one" in results[0].matched_by
        assert "keyword:keyword two" in results[0].matched_by

    def test_match_keywords_deduplication(self, tmp_path):
        """Test that match_keywords also deduplicates by resolved path.

        Ensures consistency between match() and match_keywords() methods.
        """
        # Create a lesson file
        lesson_dir = tmp_path / "lessons"
        lesson_dir.mkdir()
        lesson_file = lesson_dir / "test-lesson.md"
        lesson_file.write_text(
            """---
match:
  keywords:
    - "keyword one"
    - "keyword two"
status: active
---
# Test Lesson

Test content.
"""
        )

        # Parse the lesson twice (simulating duplicate entries)
        from gptme.lessons.parser import parse_lesson

        lesson1 = parse_lesson(lesson_file)
        lesson2 = parse_lesson(lesson_file)

        # Create list with duplicates
        lessons_with_duplicates = [lesson1, lesson2]

        # match_keywords should also deduplicate
        matcher = LessonMatcher()
        results = matcher.match_keywords(
            lessons_with_duplicates, ["keyword one", "keyword two"]
        )

        # Should only return ONE result despite two entries in input
        assert len(results) == 1
        assert results[0].lesson.title == "Test Lesson"


class TestWildcardMatching:
    """Tests for wildcard keyword matching."""

    def test_wildcard_asterisk_match(self):
        """Test matching with * wildcard."""
        lessons = [
            Lesson(
                title="Timeout Lesson",
                category="tools",
                description="Handle timeouts",
                body="# Timeout Lesson\n\nContent",
                metadata=LessonMetadata(keywords=["process killed at * seconds"]),
                path=Path("/lessons/timeout.md"),
            ),
        ]

        matcher = LessonMatcher()
        context = MatchContext(message="Error: process killed at 120 seconds")

        results = matcher.match(lessons, context)

        assert len(results) == 1
        assert results[0].lesson.title == "Timeout Lesson"
        assert "wildcard:process killed at * seconds" in results[0].matched_by

    def test_wildcard_asterisk_multiple_values(self):
        """Test that * wildcard matches various values."""
        lessons = [
            Lesson(
                title="Timeout Lesson",
                category="tools",
                description="Handle timeouts",
                body="# Timeout Lesson\n\nContent",
                metadata=LessonMetadata(keywords=["timeout after * seconds"]),
                path=Path("/lessons/timeout.md"),
            ),
        ]

        matcher = LessonMatcher()

        # Should match various numbers
        for seconds in ["30", "60", "120", "3600"]:
            context = MatchContext(message=f"Error: timeout after {seconds} seconds")
            results = matcher.match(lessons, context)
            assert len(results) == 1, f"Failed for {seconds} seconds"

    def test_wildcard_question_mark(self):
        """Test matching with ? wildcard (single character)."""
        lessons = [
            Lesson(
                title="Version Lesson",
                category="tools",
                description="Version handling",
                body="# Version Lesson\n\nContent",
                metadata=LessonMetadata(keywords=["version ?.?"]),
                path=Path("/lessons/version.md"),
            ),
        ]

        matcher = LessonMatcher()

        # Should match single digit versions
        context = MatchContext(message="Upgrading to version 2.0")
        results = matcher.match(lessons, context)
        assert len(results) == 1

        # Should NOT match multi-digit versions
        context = MatchContext(message="Upgrading to version 10.5")
        results = matcher.match(lessons, context)
        assert len(results) == 0

    def test_literal_keyword_without_wildcard(self):
        """Test that keywords without wildcards still work."""
        lessons = [
            Lesson(
                title="Git Lesson",
                category="tools",
                description="Git commands",
                body="# Git Lesson\n\nContent",
                metadata=LessonMetadata(keywords=["git commit"]),
                path=Path("/lessons/git.md"),
            ),
        ]

        matcher = LessonMatcher()
        context = MatchContext(message="I need to git commit my changes")

        results = matcher.match(lessons, context)

        assert len(results) == 1
        assert "keyword:git commit" in results[0].matched_by


class TestPatternMatching:
    """Tests for regex pattern matching."""

    def test_pattern_basic_regex(self):
        """Test matching with basic regex pattern."""
        lessons = [
            Lesson(
                title="Timeout Lesson",
                category="tools",
                description="Handle timeouts",
                body="# Timeout Lesson\n\nContent",
                metadata=LessonMetadata(patterns=[r"timeout after \d+ seconds"]),
                path=Path("/lessons/timeout.md"),
            ),
        ]

        matcher = LessonMatcher()
        context = MatchContext(message="Error: timeout after 120 seconds")

        results = matcher.match(lessons, context)

        assert len(results) == 1
        assert results[0].lesson.title == "Timeout Lesson"
        assert r"pattern:timeout after \d+ seconds" in results[0].matched_by

    def test_pattern_case_insensitive(self):
        """Test that pattern matching is case insensitive."""
        lessons = [
            Lesson(
                title="Error Lesson",
                category="tools",
                description="Handle errors",
                body="# Error Lesson\n\nContent",
                metadata=LessonMetadata(patterns=[r"error: \w+"]),
                path=Path("/lessons/error.md"),
            ),
        ]

        matcher = LessonMatcher()

        # Should match regardless of case
        for msg in ["Error: something", "ERROR: SOMETHING", "error: whatever"]:
            context = MatchContext(message=msg)
            results = matcher.match(lessons, context)
            assert len(results) == 1, f"Failed for: {msg}"

    def test_pattern_no_match(self):
        """Test that non-matching patterns don't match."""
        lessons = [
            Lesson(
                title="Number Lesson",
                category="tools",
                description="Handle numbers",
                body="# Number Lesson\n\nContent",
                metadata=LessonMetadata(patterns=[r"exactly \d{4} items"]),
                path=Path("/lessons/number.md"),
            ),
        ]

        matcher = LessonMatcher()

        # Should NOT match wrong number of digits
        context = MatchContext(message="Found exactly 12 items")
        results = matcher.match(lessons, context)
        assert len(results) == 0

    def test_pattern_invalid_regex_ignored(self):
        """Test that invalid regex patterns are ignored with warning."""
        lessons = [
            Lesson(
                title="Bad Pattern Lesson",
                category="tools",
                description="Invalid pattern",
                body="# Bad Pattern Lesson\n\nContent",
                metadata=LessonMetadata(patterns=[r"[invalid regex"]),  # Missing ]
                path=Path("/lessons/bad.md"),
            ),
        ]

        matcher = LessonMatcher()
        context = MatchContext(message="[invalid regex example")

        # Should not crash, just skip the invalid pattern
        results = matcher.match(lessons, context)
        assert len(results) == 0

    def test_combined_keywords_and_patterns(self):
        """Test matching with both keywords and patterns."""
        lessons = [
            Lesson(
                title="Timeout Lesson",
                category="tools",
                description="Handle timeouts",
                body="# Timeout Lesson\n\nContent",
                metadata=LessonMetadata(
                    keywords=["timeout", "process killed"],
                    patterns=[r"killed at \d+ seconds"],
                ),
                path=Path("/lessons/timeout.md"),
            ),
        ]

        matcher = LessonMatcher()
        context = MatchContext(message="Error: process killed at 60 seconds")

        results = matcher.match(lessons, context)

        assert len(results) == 1
        assert results[0].score == 2.0  # keyword + pattern
        matched_types = [m.split(":")[0] for m in results[0].matched_by]
        assert "keyword" in matched_types
        assert "pattern" in matched_types

    def test_multiple_patterns(self):
        """Test matching with multiple patterns."""
        lessons = [
            Lesson(
                title="Error Lesson",
                category="tools",
                description="Handle errors",
                body="# Error Lesson\n\nContent",
                metadata=LessonMetadata(
                    patterns=[
                        r"error code \d+",
                        r"failed with status \d+",
                    ]
                ),
                path=Path("/lessons/error.md"),
            ),
        ]

        matcher = LessonMatcher()

        # Should match first pattern
        context = MatchContext(message="Received error code 500")
        results = matcher.match(lessons, context)
        assert len(results) == 1

        # Should match second pattern
        context = MatchContext(message="Request failed with status 404")
        results = matcher.match(lessons, context)
        assert len(results) == 1
