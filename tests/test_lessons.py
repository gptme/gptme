"""Comprehensive tests for the lesson system.

Tests cover:
- Parser: title/description extraction, frontmatter formats (lesson, skill, cursor)
- Matcher: keyword, pattern, tool, and skill matching with deduplication
- Keyword utilities: wildcard support, pattern compilation, edge cases
- Tool helpers: log parsing, tool extraction, message content extraction
"""

import tempfile
from pathlib import Path

import pytest

from gptme.lessons.matcher import LessonMatcher, MatchContext, MatchResult
from gptme.lessons.parser import (
    Lesson,
    LessonMetadata,
    _extract_description,
    _extract_title,
    _fix_unquoted_globs,
    _glob_to_keywords,
    _translate_cursor_metadata,
    parse_lesson,
)
from gptme.message import Message
from gptme.tools.lessons import (
    LessonSessionStats,
    _extract_message_content,
    _extract_recent_tools,
    _get_included_lessons_from_log,
    _get_session_stats,
    _reset_session_stats,
)
from gptme.util.keyword_matching import (
    _compile_pattern,
    _keyword_to_pattern,
    _match_keyword,
    _match_pattern,
)

# ──────────────────────────────────────────────
# Parser: title extraction
# ──────────────────────────────────────────────


class TestExtractTitle:
    def test_basic_title(self):
        assert _extract_title("# Hello World\n\nContent") == "Hello World"

    def test_title_with_extra_spaces(self):
        assert _extract_title("#   Spaced Title  \n") == "Spaced Title"

    def test_no_title(self):
        assert _extract_title("Just some text\n\nNo heading here") == "Untitled"

    def test_h2_not_treated_as_title(self):
        assert _extract_title("## Section\n\nNot a title") == "Untitled"

    def test_title_after_blank_lines(self):
        assert _extract_title("\n\n# Late Title\n") == "Late Title"

    def test_multiple_h1_returns_first(self):
        assert _extract_title("# First\n\n# Second\n") == "First"


# ──────────────────────────────────────────────
# Parser: description extraction
# ──────────────────────────────────────────────


class TestExtractDescription:
    def test_basic_description(self):
        content = "# Title\n\nThis is the description.\n\n## Next section"
        assert _extract_description(content) == "This is the description."

    def test_no_description(self):
        content = "# Title\n\n## Another heading"
        assert _extract_description(content) == ""

    def test_empty_content(self):
        assert _extract_description("") == ""

    def test_no_heading(self):
        # Without a heading, in_content never becomes True
        assert _extract_description("Some text\nMore text") == ""

    def test_description_with_leading_space(self):
        content = "# Title\n\n  Indented description  \n"
        assert _extract_description(content) == "Indented description"


# ──────────────────────────────────────────────
# Parser: glob to keywords
# ──────────────────────────────────────────────


class TestGlobToKeywords:
    def test_python_glob(self):
        result = _glob_to_keywords("**/*.py")
        assert "python" in result
        assert "python code" in result

    def test_typescript_glob(self):
        result = _glob_to_keywords("**/*.ts")
        assert "typescript" in result

    def test_react_tsx_glob(self):
        result = _glob_to_keywords("**/*.tsx")
        assert "react" in result
        assert "frontend" in result

    def test_api_directory(self):
        result = _glob_to_keywords("src/api/**/*.js")
        assert "api" in result
        assert "javascript" in result

    def test_test_directory(self):
        result = _glob_to_keywords("tests/**/*.py")
        assert "tests" in result
        assert "python" in result

    def test_unknown_extension(self):
        result = _glob_to_keywords("**/*.xyz")
        assert "xyz" in result

    def test_no_extension(self):
        result = _glob_to_keywords("src/api/")
        assert "api" in result

    def test_deduplication(self):
        result = _glob_to_keywords("**/*.py")
        # Should not have duplicates
        assert len(result) == len(set(result))


# ──────────────────────────────────────────────
# Parser: fix unquoted globs
# ──────────────────────────────────────────────


class TestFixUnquotedGlobs:
    def test_unquoted_glob(self):
        result = _fix_unquoted_globs("globs: *,**/*")
        assert '"*,**/*"' in result

    def test_already_quoted_glob(self):
        result = _fix_unquoted_globs('globs: "**/*.py"')
        # Should not double-quote
        assert result == 'globs: "**/*.py"'

    def test_no_globs_line(self):
        result = _fix_unquoted_globs("name: test\ndescription: hello")
        assert result == "name: test\ndescription: hello"

    def test_indented_globs(self):
        result = _fix_unquoted_globs("  globs: **/*.ts")
        assert '"**/*.ts"' in result


# ──────────────────────────────────────────────
# Parser: translate cursor metadata
# ──────────────────────────────────────────────


class TestTranslateCursorMetadata:
    def test_basic_cursor_rule(self):
        frontmatter = {
            "name": "Python Rule",
            "description": "Rules for Python",
            "globs": ["**/*.py"],
        }
        meta = _translate_cursor_metadata(frontmatter)
        assert meta.name == "Python Rule"
        assert meta.description == "Rules for Python"
        assert "python" in meta.keywords
        assert meta.globs == ["**/*.py"]

    def test_always_apply(self):
        frontmatter = {"name": "Global", "alwaysApply": True}
        meta = _translate_cursor_metadata(frontmatter)
        assert meta.always_apply is True
        assert "code" in meta.keywords

    def test_with_priority_and_triggers(self):
        frontmatter = {
            "name": "Test",
            "priority": "high",
            "triggers": ["file_change"],
        }
        meta = _translate_cursor_metadata(frontmatter)
        assert meta.priority == "high"
        assert meta.triggers == ["file_change"]

    def test_empty_globs_none(self):
        # YAML parses empty "globs:" as None
        frontmatter = {"name": "Test", "globs": None}
        meta = _translate_cursor_metadata(frontmatter)
        assert meta.keywords == []  # No keywords from None globs


# ──────────────────────────────────────────────
# Parser: parse_lesson full
# ──────────────────────────────────────────────


class TestParseLesson:
    def test_lesson_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tools" / "test.md"
            path.parent.mkdir()
            path.write_text(
                """\
---
match:
  keywords: [git commit, version control]
  tools: [shell]
status: active
---

# Git Commit Best Practices

Always write descriptive commit messages.

## Pattern
Use conventional commits format.
"""
            )
            lesson = parse_lesson(path)
            assert lesson.title == "Git Commit Best Practices"
            assert lesson.metadata.keywords == ["git commit", "version control"]
            assert lesson.metadata.tools == ["shell"]
            assert lesson.metadata.status == "active"
            assert lesson.category == "tools"

    def test_skill_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "skills" / "SKILL.md"
            path.parent.mkdir()
            path.write_text(
                """\
---
name: python-repl
description: Use Python REPL for quick computations
---

# Python REPL Skill

Execute Python code interactively.
"""
            )
            lesson = parse_lesson(path)
            assert lesson.metadata.name == "python-repl"
            assert (
                lesson.metadata.description == "Use Python REPL for quick computations"
            )

    def test_cursor_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rules" / "test.mdc"
            path.parent.mkdir()
            path.write_text(
                """\
---
name: Python Standards
description: Python coding standards
globs: ["**/*.py"]
alwaysApply: false
---

# Python Standards

Follow PEP 8.
"""
            )
            lesson = parse_lesson(path)
            assert lesson.metadata.name == "Python Standards"
            assert lesson.metadata.globs == ["**/*.py"]
            assert "python" in lesson.metadata.keywords

    def test_no_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "plain.md"
            path.write_text("# Just a Title\n\nSome content.\n")
            lesson = parse_lesson(path)
            assert lesson.title == "Just a Title"
            assert lesson.metadata.keywords == []
            assert lesson.metadata.status == "active"

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            parse_lesson(Path("/nonexistent/lesson.md"))

    def test_invalid_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.md"
            path.write_text("---\n[invalid yaml\n---\n\n# Title\n")
            with pytest.raises(ValueError, match="Invalid YAML"):
                parse_lesson(path)

    def test_patterns_field(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.md"
            path.write_text(
                """\
---
match:
  keywords: [test]
  patterns:
    - 'error.*timeout'
    - 'connection\\s+refused'
---

# Pattern Test

Content.
"""
            )
            lesson = parse_lesson(path)
            assert len(lesson.metadata.patterns) == 2
            assert "error.*timeout" in lesson.metadata.patterns

    def test_depends_field(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.md"
            path.write_text(
                """\
---
name: advanced-skill
depends:
  - python-repl
  - shell-basics
---

# Advanced Skill

Depends on other skills.
"""
            )
            lesson = parse_lesson(path)
            assert lesson.metadata.depends == ["python-repl", "shell-basics"]

    def test_id_field(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.md"
            path.write_text(
                """\
---
id: lesson-001
match:
  keywords: [test]
---

# ID Test

Has a stable ID.
"""
            )
            lesson = parse_lesson(path)
            assert lesson.metadata.id == "lesson-001"

    def test_keywords_as_string(self):
        """Test that a single keyword string is handled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.md"
            path.write_text(
                """\
---
match:
  keywords: single-keyword
---

# Single Keyword

Content.
"""
            )
            lesson = parse_lesson(path)
            assert lesson.metadata.keywords == ["single-keyword"]

    def test_empty_keywords_filtered(self):
        """Empty/None keywords should be filtered out."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.md"
            path.write_text(
                """\
---
match:
  keywords:
    - "valid keyword"
    - ""
    - "another valid"
---

# Filter Test

Content.
"""
            )
            lesson = parse_lesson(path)
            assert lesson.metadata.keywords == ["valid keyword", "another valid"]


# ──────────────────────────────────────────────
# Keyword matching utilities
# ──────────────────────────────────────────────


class TestKeywordToPattern:
    def test_simple_keyword(self):
        pattern = _keyword_to_pattern("error")
        assert pattern is not None
        assert pattern.search("an error occurred") is not None

    def test_wildcard_keyword(self):
        pattern = _keyword_to_pattern("timeout*")
        assert pattern is not None
        assert pattern.search("timeout30s") is not None
        assert pattern.search("timeout") is not None

    def test_mid_wildcard(self):
        pattern = _keyword_to_pattern("process killed at * seconds")
        assert pattern is not None
        assert pattern.search("process killed at 120 seconds") is not None

    def test_empty_keyword(self):
        assert _keyword_to_pattern("") is None
        assert _keyword_to_pattern("   ") is None

    def test_single_wildcard(self):
        """Single * matches everything — disabled to prevent over-matching."""
        assert _keyword_to_pattern("*") is None

    def test_case_insensitive(self):
        pattern = _keyword_to_pattern("Error")
        assert pattern is not None
        assert pattern.search("ERROR found") is not None
        assert pattern.search("error found") is not None


class TestMatchKeyword:
    def test_simple_match(self):
        assert _match_keyword("git commit", "I need to git commit my changes")

    def test_no_match(self):
        assert not _match_keyword("kubernetes", "I need to git commit my changes")

    def test_case_insensitive(self):
        assert _match_keyword("ERROR", "an error occurred")

    def test_empty_keyword(self):
        assert not _match_keyword("", "any text")

    def test_wildcard_match(self):
        assert _match_keyword("deploy*", "deploying to production")

    def test_substring_match(self):
        """Keywords match as substrings."""
        assert _match_keyword("err", "error occurred")


class TestMatchPattern:
    def test_regex_match(self):
        assert _match_pattern(r"error.*timeout", "got error: connection timeout")

    def test_regex_no_match(self):
        assert not _match_pattern(r"error.*timeout", "everything is fine")

    def test_invalid_regex(self):
        """Invalid regex returns False, doesn't raise."""
        assert not _match_pattern(r"[invalid", "test text")

    def test_empty_pattern(self):
        assert not _match_pattern("", "test text")


class TestCompilePattern:
    def test_valid_pattern(self):
        pattern = _compile_pattern(r"test\d+")
        assert pattern is not None
        assert pattern.search("test123") is not None

    def test_invalid_pattern(self):
        assert _compile_pattern(r"[unterminated") is None

    def test_empty_pattern(self):
        assert _compile_pattern("") is None
        assert _compile_pattern("   ") is None


# ──────────────────────────────────────────────
# Matcher: LessonMatcher.match
# ──────────────────────────────────────────────


def _make_lesson(
    *,
    title: str = "Test Lesson",
    keywords: list[str] | None = None,
    patterns: list[str] | None = None,
    tools: list[str] | None = None,
    name: str | None = None,
    path: str = "/tmp/test.md",
) -> Lesson:
    """Helper to create test lessons."""
    return Lesson(
        path=Path(path),
        metadata=LessonMetadata(
            keywords=keywords or [],
            patterns=patterns or [],
            tools=tools or [],
            name=name,
        ),
        title=title,
        description="Test description",
        category="test",
        body="Test body",
    )


class TestLessonMatcher:
    def setup_method(self):
        self.matcher = LessonMatcher()

    def test_keyword_match(self):
        lessons = [_make_lesson(keywords=["git commit", "version control"])]
        ctx = MatchContext(message="I need to git commit my changes")
        results = self.matcher.match(lessons, ctx)
        assert len(results) == 1
        assert results[0].score == 1.0
        assert "keyword:git commit" in results[0].matched_by

    def test_multiple_keyword_matches(self):
        lessons = [_make_lesson(keywords=["git commit", "version control"])]
        ctx = MatchContext(message="I need to git commit for version control")
        results = self.matcher.match(lessons, ctx)
        assert len(results) == 1
        assert results[0].score == 2.0  # Both keywords matched

    def test_pattern_match(self):
        lessons = [_make_lesson(patterns=[r"error.*timeout"])]
        ctx = MatchContext(message="got error: connection timeout")
        results = self.matcher.match(lessons, ctx)
        assert len(results) == 1
        assert any("pattern:" in m for m in results[0].matched_by)

    def test_tool_match(self):
        lessons = [_make_lesson(tools=["shell"])]
        ctx = MatchContext(message="running a command", tools_used=["shell"])
        results = self.matcher.match(lessons, ctx)
        assert len(results) == 1
        assert results[0].score == 2.0  # Tool matches score higher

    def test_skill_name_match(self):
        lessons = [_make_lesson(name="python-repl")]
        ctx = MatchContext(message="I'll use the python repl to compute this")
        results = self.matcher.match(lessons, ctx)
        assert len(results) == 1
        assert results[0].score == 1.5  # Skill matches score 1.5

    def test_skill_hyphen_variants(self):
        """Skill names with hyphens match space-separated variants."""
        lessons = [_make_lesson(name="shell-basics")]
        ctx = MatchContext(message="let me review shell basics first")
        results = self.matcher.match(lessons, ctx)
        assert len(results) == 1

    def test_no_match(self):
        lessons = [_make_lesson(keywords=["kubernetes", "k8s"])]
        ctx = MatchContext(message="writing python code")
        results = self.matcher.match(lessons, ctx)
        assert len(results) == 0

    def test_threshold(self):
        lessons = [
            _make_lesson(title="Low", keywords=["test"]),
            _make_lesson(
                title="High",
                keywords=["test"],
                tools=["shell"],
                path="/tmp/high.md",
            ),
        ]
        ctx = MatchContext(message="running a test", tools_used=["shell"])
        results = self.matcher.match(lessons, ctx, threshold=2.0)
        assert len(results) == 1
        assert results[0].lesson.title == "High"

    def test_sorted_by_score(self):
        lessons = [
            _make_lesson(title="Low", keywords=["test"], path="/tmp/low.md"),
            _make_lesson(
                title="High",
                keywords=["test", "shell"],
                tools=["shell"],
                path="/tmp/high.md",
            ),
        ]
        ctx = MatchContext(message="running a shell test", tools_used=["shell"])
        results = self.matcher.match(lessons, ctx)
        assert len(results) == 2
        assert results[0].lesson.title == "High"
        assert results[0].score > results[1].score

    def test_deduplication_by_path(self):
        """Duplicate lessons (same resolved path) should be deduplicated."""
        lesson = _make_lesson(keywords=["test"])
        lessons = [lesson, lesson]  # Same object = same path
        ctx = MatchContext(message="this is a test")
        results = self.matcher.match(lessons, ctx)
        assert len(results) == 1

    def test_no_tools_in_context(self):
        """Tool matching skipped when no tools in context."""
        lessons = [_make_lesson(tools=["shell"])]
        ctx = MatchContext(message="test", tools_used=None)
        results = self.matcher.match(lessons, ctx)
        assert len(results) == 0

    def test_empty_lessons(self):
        results = self.matcher.match([], MatchContext(message="test"))
        assert results == []


class TestLessonMatcherKeywords:
    def setup_method(self):
        self.matcher = LessonMatcher()

    def test_match_keywords(self):
        lessons = [_make_lesson(keywords=["git commit", "version control"])]
        results = self.matcher.match_keywords(lessons, ["git commit"])
        assert len(results) == 1

    def test_match_keywords_no_match(self):
        lessons = [_make_lesson(keywords=["docker"])]
        results = self.matcher.match_keywords(lessons, ["git commit"])
        assert len(results) == 0

    def test_match_keywords_deduplication(self):
        lesson = _make_lesson(keywords=["test"])
        results = self.matcher.match_keywords([lesson, lesson], ["test"])
        assert len(results) == 1

    def test_match_keywords_multiple_input(self):
        lessons = [_make_lesson(keywords=["git", "commit"])]
        results = self.matcher.match_keywords(lessons, ["git", "commit"])
        assert len(results) == 1
        assert results[0].score == 2.0


# ──────────────────────────────────────────────
# Tool helpers: log parsing
# ──────────────────────────────────────────────


class TestGetIncludedLessonsFromLog:
    def test_extracts_lesson_paths(self):
        log = [
            Message(
                role="system",
                content=(
                    "# Relevant Lessons\n\n"
                    "## Git Best Practices\n\n"
                    "*Path: /lessons/tools/git.md*\n\n"
                    "## Shell Safety\n\n"
                    "*Path: /lessons/tools/shell.md*\n"
                ),
            )
        ]
        paths = _get_included_lessons_from_log(log)
        assert paths == {"/lessons/tools/git.md", "/lessons/tools/shell.md"}

    def test_no_lessons_in_log(self):
        log = [
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi there"),
        ]
        assert _get_included_lessons_from_log(log) == set()

    def test_empty_log(self):
        assert _get_included_lessons_from_log([]) == set()

    def test_ignores_non_system_messages(self):
        log = [
            Message(
                role="user",
                content="# Relevant Lessons\n*Path: /fake.md*",
            )
        ]
        assert _get_included_lessons_from_log(log) == set()


class TestExtractRecentTools:
    def test_extracts_tool_names(self):
        log = [
            Message(
                role="assistant",
                content="Let me check:\n```shell\nls -la\n```\n",
            ),
            Message(
                role="assistant",
                content="Saving:\n```save test.py\nprint('hello')\n```\n",
            ),
        ]
        tools = _extract_recent_tools(log)
        assert "shell" in tools
        assert "save" in tools

    def test_deduplicates_tools(self):
        log = [
            Message(
                role="assistant",
                content="```shell\nls\n```\n\n```shell\npwd\n```\n",
            ),
        ]
        tools = _extract_recent_tools(log)
        assert tools.count("shell") == 1

    def test_skips_text_blocks(self):
        log = [
            Message(
                role="assistant",
                content="```text\nJust text\n```\n",
            ),
        ]
        tools = _extract_recent_tools(log)
        assert "text" not in tools

    def test_empty_log(self):
        assert _extract_recent_tools([]) == []

    def test_limit(self):
        """Only checks last N messages."""
        log = [
            Message(role="assistant", content=f"```tool{i}\ncmd\n```\n")
            for i in range(20)
        ]
        # limit=10 means only the last 10 messages are checked
        tools = _extract_recent_tools(log, limit=10)
        assert len(tools) == 10  # exactly 10 unique tools from last 10 messages
        assert "tool19" in tools  # last message's tool included
        assert "tool9" not in tools  # messages before the limit excluded


class TestExtractMessageContent:
    def test_combines_messages(self):
        log = [
            Message(role="user", content="What is git?"),
            Message(role="assistant", content="Git is version control."),
        ]
        content = _extract_message_content(log)
        assert "git" in content.lower()
        assert "version control" in content.lower()

    def test_ignores_system_messages(self):
        log = [
            Message(role="system", content="SECRET SYSTEM PROMPT"),
            Message(role="user", content="Hello"),
        ]
        content = _extract_message_content(log)
        assert "SECRET" not in content

    def test_empty_log(self):
        assert _extract_message_content([]) == ""

    def test_limit(self):
        log = [Message(role="user", content=f"Message {i}") for i in range(20)]
        content = _extract_message_content(log, limit=3)
        # Should only contain content from last 3 messages
        assert "Message 19" in content
        assert "Message 0" not in content


# ──────────────────────────────────────────────
# Session stats
# ──────────────────────────────────────────────


class TestSessionStats:
    def teardown_method(self):
        _reset_session_stats()

    def test_initial_stats(self):
        stats = LessonSessionStats()
        assert stats.total_matched == 0
        assert stats.unique_lessons == set()
        assert stats.lesson_titles == {}

    def test_stats_tracking(self):
        stats = LessonSessionStats()
        stats.unique_lessons.add("/path/to/lesson.md")
        stats.lesson_titles["/path/to/lesson.md"] = "Test Lesson"
        stats.total_matched = 1
        assert len(stats.unique_lessons) == 1
        assert stats.lesson_titles["/path/to/lesson.md"] == "Test Lesson"

    def test_get_and_reset(self):
        _reset_session_stats()
        stats = _get_session_stats()
        assert stats.total_matched == 0

        stats.total_matched = 5
        stats.unique_lessons.add("test.md")

        _reset_session_stats()
        new_stats = _get_session_stats()
        assert new_stats.total_matched == 0
        assert new_stats.unique_lessons == set()


# ──────────────────────────────────────────────
# Match result dataclass
# ──────────────────────────────────────────────


class TestMatchResult:
    def test_creation(self):
        lesson = _make_lesson()
        result = MatchResult(
            lesson=lesson, score=2.5, matched_by=["keyword:test", "tool:shell"]
        )
        assert result.score == 2.5
        assert len(result.matched_by) == 2
