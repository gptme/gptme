"""Tests for auto-include lesson system with token budget."""

from pathlib import Path

from gptme.lessons.auto_include import (
    _estimate_tokens,
    _format_with_budget,
    _get_token_budget,
)
from gptme.lessons.parser import Lesson, LessonMetadata


def _make_lesson(title: str, body: str, path: str | Path = "/tmp/test.md") -> Lesson:
    """Create a test lesson."""
    return Lesson(
        title=title,
        description=title,
        body=body,
        path=Path(path) if isinstance(path, str) else path,
        metadata=LessonMetadata(keywords=[]),
        category="test",
    )


class _MockMatch:
    """Simple mock for match results."""

    def __init__(self, lesson, score=1.0, matched_by=None):
        self.lesson = lesson
        self.score = score
        self.matched_by = matched_by or []


def test_estimate_tokens_empty():
    assert _estimate_tokens("") == 1


def test_estimate_tokens_short():
    assert _estimate_tokens("hello") == 1  # 5//3 = 1
    assert _estimate_tokens("hello world") == 3  # 11//3 = 3


def test_estimate_tokens_long():
    text = "a" * 3000
    assert _estimate_tokens(text) == 1000  # 3000//3 = 1000


def test_format_with_budget_all_fit():
    """All lessons fit within budget."""
    lessons = [
        _make_lesson("Test 1", "short body"),
        _make_lesson("Test 2", "another short body"),
    ]
    matches = [
        _MockMatch(lesson=lesson, score=2.0 - i) for i, lesson in enumerate(lessons)
    ]
    content, dropped = _format_with_budget(matches, max_tokens=10000)
    assert dropped == 0
    assert "Test 1" in content
    assert "Test 2" in content


def test_format_with_budget_drops_lowest():
    """Lowest-scored lessons are dropped when budget is tight."""
    lessons = [
        _make_lesson("High Score", "body " * 100),  # ~200 chars, ~66 tokens
        _make_lesson("Low Score", "body " * 100),
    ]
    matches = [
        _MockMatch(lesson=lesson, score=2.0 - i) for i, lesson in enumerate(lessons)
    ]
    # Budget just enough for one lesson
    content, dropped = _format_with_budget(matches, max_tokens=100)
    # First lesson (highest score) should fit, second should be dropped
    assert dropped == 1
    assert "High Score" in content
    assert "Low Score" not in content


def test_format_with_budget_first_lesson_too_large():
    """First lesson is force-included even if it exceeds the budget (minimum 1)."""
    lessons = [
        _make_lesson("Huge Lesson", "body " * 10000),  # ~50000 chars, ~16666 tokens
    ]
    matches = [_MockMatch(lesson=lesson, score=10.0) for lesson in lessons]
    content, dropped = _format_with_budget(matches, max_tokens=100)
    # First/highest-scored lesson is always included regardless of size
    assert dropped == 0  # Only one lesson — nothing left to drop
    assert "Huge Lesson" in content


def test_format_with_budget_oversized_first_does_not_block_small_subsequent():
    """Oversized first lesson must not consume the budget for subsequent small lessons."""
    lessons = [
        _make_lesson("Huge Lesson", "body " * 10000),  # ~16666 tokens, well over budget
        _make_lesson("Tiny Lesson", "hi"),  # ~1 token
    ]
    matches = [
        _MockMatch(lesson=lesson, score=2.0 - i) for i, lesson in enumerate(lessons)
    ]
    # Budget of 1000 — first lesson far exceeds it, but second lesson is tiny
    content, dropped = _format_with_budget(matches, max_tokens=1000)
    # Tiny second lesson should still be included because it fits the subsequent budget
    assert dropped == 0
    assert "Huge Lesson" in content
    assert "Tiny Lesson" in content


def test_format_with_budget_drops_multiple():
    """Multiple low-scored lessons are dropped."""
    lessons = [
        _make_lesson("Best", "small body"),
        _make_lesson("Medium", "body " * 500),  # ~2500 chars, ~833 tokens
        _make_lesson("Worst", "body " * 500),
        _make_lesson("Worstest", "body " * 500),
    ]
    matches = [
        _MockMatch(lesson=lesson, score=5.0 - i) for i, lesson in enumerate(lessons)
    ]
    content, dropped = _format_with_budget(matches, max_tokens=1000)
    # Best should always fit (small). Medium might depending on total.
    # At least worst/worstest should be dropped.
    assert dropped >= 1
    assert "Best" in content


def test_format_with_budget_includes_metadata():
    """Check that lesson metadata is included in formatted output."""
    lesson = _make_lesson("Metadata Test", "body content")
    match = _MockMatch(lesson, matched_by=["keyword:test"])
    content, dropped = _format_with_budget([match], max_tokens=10000)
    assert dropped == 0
    assert "Metadata Test" in content  # title
    assert "/tmp/test.md" in content  # path
    assert "test" in content  # category
    assert "1 keyword(s)" in content  # match info


def test_get_token_budget_default():
    """Default token budget from the function."""
    budget = _get_token_budget()
    assert budget == 50000


def test_get_token_budget_env(monkeypatch):
    """Token budget can be configured via env var."""
    monkeypatch.setenv("GPTME_LESSONS_TOKEN_BUDGET", "10000")
    budget = _get_token_budget()
    assert budget == 10000


def test_get_token_budget_invalid_env(monkeypatch):
    """Invalid env var falls back to default."""
    monkeypatch.setenv("GPTME_LESSONS_TOKEN_BUDGET", "not-a-number")
    budget = _get_token_budget()
    assert budget == 50000
