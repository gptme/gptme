"""Tests for auto-include lesson system with token budget."""

import json
from pathlib import Path

from gptme.lessons.auto_include import (
    _estimate_tokens,
    _format_with_budget,
    _get_token_budget,
    auto_include_lessons,
)
from gptme.lessons.index import LessonIndex, clear_cache
from gptme.lessons.parser import Lesson, LessonMetadata
from gptme.message import Message


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
    content, dropped, _ = _format_with_budget(matches, max_tokens=10000)
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
    content, dropped, _ = _format_with_budget(matches, max_tokens=100)
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
    content, dropped, _ = _format_with_budget(matches, max_tokens=100)
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
    content, dropped, _ = _format_with_budget(matches, max_tokens=1000)
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
    content, dropped, _ = _format_with_budget(matches, max_tokens=1000)
    # Best should always fit (small). Medium might depending on total.
    # At least worst/worstest should be dropped.
    assert dropped >= 1
    assert "Best" in content


def test_format_with_budget_includes_metadata():
    """Check that lesson metadata is included in formatted output."""
    lesson = _make_lesson("Metadata Test", "body content")
    match = _MockMatch(lesson, matched_by=["keyword:test"])
    content, dropped, _ = _format_with_budget([match], max_tokens=10000)
    assert dropped == 0
    assert "Metadata Test" in content  # title
    assert "/tmp/test.md" in content  # path
    assert "test" in content  # category
    assert "1 keyword(s)" in content  # match info


def test_format_with_budget_subsequent_tokens_excludes_first():
    """subsequent_tokens must not include the force-included first lesson.

    The warning log compares subsequent_tokens against max_tokens (which only
    governs non-first lessons). If subsequent_tokens included the first lesson
    the comparison would be misleading.
    """
    big_body = "word " * 5000  # ~8333 tokens, well over any subsequent budget
    lessons = [
        _make_lesson("First", big_body),
        _make_lesson("Second", "tiny"),
    ]
    matches = [
        _MockMatch(lesson=lesson, score=2.0 - i) for i, lesson in enumerate(lessons)
    ]
    _, dropped, subsequent_tokens = _format_with_budget(matches, max_tokens=10000)
    # Second lesson is tiny so it fits; first is excluded from subsequent_tokens count
    assert dropped == 0
    assert subsequent_tokens < 100  # only "tiny" second lesson counts


def test_get_token_budget_default(monkeypatch):
    """Default token budget from the function."""
    monkeypatch.delenv("GPTME_LESSONS_TOKEN_BUDGET", raising=False)
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


def test_get_token_budget_zero_env(monkeypatch):
    """Zero budget falls back to default (non-positive is not allowed)."""
    monkeypatch.setenv("GPTME_LESSONS_TOKEN_BUDGET", "0")
    budget = _get_token_budget()
    assert budget == 50000


def test_get_token_budget_negative_env(monkeypatch):
    """Negative budget falls back to default."""
    monkeypatch.setenv("GPTME_LESSONS_TOKEN_BUDGET", "-1000")
    budget = _get_token_budget()
    assert budget == 50000


def test_auto_include_materializes_manifest_backed_skill(tmp_path: Path, monkeypatch):
    """Matching a manifest-backed skill should load the full SKILL.md before injection."""
    skill_dir = tmp_path / "skills" / "python-repl"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        """---
name: python-repl
description: Use Python REPL for quick computations
keywords:
  - python repl
  - quick computation
---

# Python REPL Skill

Execute Python code interactively.
"""
    )
    (tmp_path / "skills" / "index.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "skills": [
                    {
                        "name": "python-repl",
                        "description": "Use Python REPL for quick computations",
                        "path": "python-repl",
                        "keywords": ["python repl", "quick computation"],
                    }
                ],
            }
        )
    )

    monkeypatch.setattr(
        LessonIndex,
        "_default_dirs",
        staticmethod(lambda: [tmp_path / "skills"]),
    )
    clear_cache()

    messages = [
        Message("system", "System prompt"),
        Message("user", "Use the python repl for a quick computation"),
    ]
    updated = auto_include_lessons(messages)

    assert len(updated) == 3
    lesson_msg = updated[1]
    assert "Python REPL Skill" in lesson_msg.content
    assert "Execute Python code interactively." in lesson_msg.content


# --- Randomized lesson dropout (causal LOO measurement) ---

from gptme.lessons.auto_include import (
    _apply_lesson_dropout,
    _get_dropout_epsilon,
    _get_dropout_log_dir,
    _get_dropout_session_id,
)


def test_dropout_epsilon_unset_is_zero(monkeypatch):
    monkeypatch.delenv("LESSON_DROPOUT_EPSILON", raising=False)
    assert _get_dropout_epsilon() == 0.0


def test_dropout_epsilon_parses_and_clamps(monkeypatch):
    monkeypatch.setenv("LESSON_DROPOUT_EPSILON", "0.25")
    assert _get_dropout_epsilon() == 0.25
    monkeypatch.setenv("LESSON_DROPOUT_EPSILON", "2.5")
    assert _get_dropout_epsilon() == 1.0
    monkeypatch.setenv("LESSON_DROPOUT_EPSILON", "0")
    assert _get_dropout_epsilon() == 0.0
    monkeypatch.setenv("LESSON_DROPOUT_EPSILON", "-0.3")
    assert _get_dropout_epsilon() == 0.0
    monkeypatch.setenv("LESSON_DROPOUT_EPSILON", "not-a-number")
    assert _get_dropout_epsilon() == 0.0


def test_dropout_session_id_prefers_env(monkeypatch):
    monkeypatch.setenv("GPTME_SESSION_ID", "sess-123")
    monkeypatch.delenv("CC_SESSION_ID", raising=False)
    assert _get_dropout_session_id() == "sess-123"
    monkeypatch.delenv("GPTME_SESSION_ID", raising=False)
    monkeypatch.setenv("CC_SESSION_ID", "cc-456")
    assert _get_dropout_session_id() == "cc-456"
    monkeypatch.delenv("CC_SESSION_ID", raising=False)
    # Falls back to a generated id (non-empty)
    assert _get_dropout_session_id()


def test_dropout_disabled_is_noop(monkeypatch, tmp_path):
    monkeypatch.delenv("LESSON_DROPOUT_EPSILON", raising=False)
    monkeypatch.setenv("LESSON_DROPOUT_LOG_DIR", str(tmp_path / "drop"))
    matches = [_MockMatch(_make_lesson("A", "body", "/tmp/a.md"))]
    result = _apply_lesson_dropout(matches)
    assert result == matches
    assert not (tmp_path / "drop").exists()  # nothing written


def test_dropout_epsilon_one_withholds_all_and_logs(monkeypatch, tmp_path):
    log_dir = tmp_path / "drop"
    monkeypatch.setenv("LESSON_DROPOUT_EPSILON", "1.0")
    monkeypatch.setenv("LESSON_DROPOUT_LOG_DIR", str(log_dir))
    monkeypatch.setenv("GPTME_SESSION_ID", "sess-all")
    monkeypatch.delenv("CC_SESSION_ID", raising=False)
    matches = [
        _MockMatch(_make_lesson("A", "abody", "/tmp/a.md")),
        _MockMatch(_make_lesson("B", "bbody", "/tmp/b.md")),
    ]
    result = _apply_lesson_dropout(matches)
    assert result == []  # all withheld

    log_file = log_dir / "sess-all.jsonl"
    assert log_file.exists()
    records = [json.loads(line) for line in log_file.read_text().splitlines() if line]
    assert len(records) == 1
    record = records[0]
    assert record["session_id"] == "sess-all"
    assert record["epsilon"] == 1.0
    withheld_paths = {w["path"] for w in record["withheld"]}
    assert withheld_paths == {"/tmp/a.md", "/tmp/b.md"}


def test_dropout_partial_is_consistent(monkeypatch, tmp_path):
    import random as _random

    log_dir = tmp_path / "drop"
    monkeypatch.setenv("LESSON_DROPOUT_EPSILON", "0.5")
    monkeypatch.setenv("LESSON_DROPOUT_LOG_DIR", str(log_dir))
    monkeypatch.setenv("GPTME_SESSION_ID", "sess-part")
    monkeypatch.delenv("CC_SESSION_ID", raising=False)
    matches = [
        _MockMatch(_make_lesson(f"L{i}", "body", f"/tmp/l{i}.md")) for i in range(20)
    ]
    _random.seed(42)
    kept = _apply_lesson_dropout(matches)

    # The withheld log plus the kept set must reconstruct the original set.
    log_file = log_dir / "sess-part.jsonl"
    records = [json.loads(line) for line in log_file.read_text().splitlines() if line]
    withheld_paths = {w["path"] for r in records for w in r["withheld"]}
    kept_paths = {str(m.lesson.path) for m in kept}
    all_paths = {str(m.lesson.path) for m in matches}
    assert kept_paths.isdisjoint(withheld_paths)
    assert kept_paths | withheld_paths == all_paths
    assert 0 < len(withheld_paths) < len(matches)  # genuinely partial


def test_dropout_log_dir_default(monkeypatch):
    monkeypatch.delenv("LESSON_DROPOUT_LOG_DIR", raising=False)
    assert _get_dropout_log_dir() == Path("state/lesson-dropout")


def test_dropout_empty_matches_still_logs_when_epsilon_positive(monkeypatch, tmp_path):
    """When epsilon>0 and no lessons match, a dropout log record must still be written
    so the analysis script can identify treatment-group sessions."""
    log_dir = tmp_path / "drop"
    monkeypatch.setenv("LESSON_DROPOUT_EPSILON", "0.25")
    monkeypatch.setenv("LESSON_DROPOUT_LOG_DIR", str(log_dir))
    monkeypatch.setenv("GPTME_SESSION_ID", "sess-empty")
    monkeypatch.delenv("CC_SESSION_ID", raising=False)

    # Make the matcher return no matches
    import gptme.lessons.matcher as matcher_module

    def empty_match(self, index, context):
        return []

    monkeypatch.setattr(matcher_module.LessonMatcher, "match", empty_match)

    messages = [
        Message("system", "System prompt"),
        Message("user", "Something that won't match any lesson"),
    ]
    result = auto_include_lessons(messages)

    # No lessons should be injected
    assert len(result) == 2  # unchanged

    # But a dropout log record MUST exist
    log_file = log_dir / "sess-empty.jsonl"
    assert log_file.exists(), (
        "No dropout log written when epsilon>0 and match list is empty"
    )
    records = [json.loads(line) for line in log_file.read_text().splitlines() if line]
    assert len(records) == 1
    assert records[0]["session_id"] == "sess-empty"
    assert records[0]["epsilon"] == 0.25
    assert records[0]["withheld"] == []  # empty withheld list
