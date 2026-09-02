"""Tests for auto-include lesson system with token budget."""

import json
import os
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

    # Ensure LessonIndex returns at least one lesson so the early-return guard
    # at "if not index.lessons" does not fire before _apply_lesson_dropout.
    import gptme.lessons.auto_include as auto_include_module

    class FakeLesson:
        path = "test.md"
        title = "Test Lesson"
        category = "test"
        body = "Some content"
        is_stub = False
        match_strength = 1

    class FakeIndex:
        lessons = [FakeLesson()]

        def materialize_lesson(self, _lesson):
            return _lesson

    # Patch at point of use: auto_include does "from .index import LessonIndex"
    # so we must patch auto_include.LessonIndex, not index.LessonIndex.
    monkeypatch.setattr(auto_include_module, "LessonIndex", lambda: FakeIndex())

    # Make the matcher return no matches
    import gptme.lessons.matcher as matcher_module

    monkeypatch.setattr(
        matcher_module.LessonMatcher,
        "match",
        lambda self, index, context: [],
    )

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


# --- Lesson policy manifest (Stage 1 shadow logging) ---

import gptme.lessons.auto_include as _auto_include_mod
from gptme.lessons.auto_include import (
    _classify_lesson,
    _get_policy_manifest_path,
    _load_policy_manifest,
)


def _write_manifest(path: Path, content: str) -> None:
    path.write_text(content)


def _reset_manifest_cache(monkeypatch) -> None:
    monkeypatch.setattr(_auto_include_mod, "_policy_manifest_cache", None)
    monkeypatch.setattr(_auto_include_mod, "_policy_manifest_cache_key", None)


def test_policy_manifest_path_default(monkeypatch):
    monkeypatch.delenv("LESSON_POLICY_MANIFEST_PATH", raising=False)
    assert _get_policy_manifest_path() == Path("state/lesson-policy/manifest.yaml")


def test_policy_manifest_path_override(monkeypatch, tmp_path):
    override = str(tmp_path / "custom.yaml")
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", override)
    assert _get_policy_manifest_path() == Path(override)


def test_load_policy_manifest_missing_returns_default(monkeypatch, tmp_path):
    _reset_manifest_cache(monkeypatch)
    monkeypatch.setenv(
        "LESSON_POLICY_MANIFEST_PATH", str(tmp_path / "nonexistent.yaml")
    )
    manifest = _load_policy_manifest()
    assert manifest["version"] == 1
    assert manifest["validated_core"] == []
    assert manifest["holdout_population"] == []


def test_load_policy_manifest_valid(monkeypatch, tmp_path):
    _reset_manifest_cache(monkeypatch)
    manifest_file = tmp_path / "manifest.yaml"
    _write_manifest(
        manifest_file,
        """\
version: 2
updated_at: '2026-08-01T00:00:00Z'
validated_core:
- patterns/persistent-learning
exempt:
- safety/critical-rule
holdout_population:
- code/some-lesson
""",
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))
    manifest = _load_policy_manifest()
    assert manifest["version"] == 2
    assert "patterns/persistent-learning" in manifest["validated_core"]
    assert "safety/critical-rule" in manifest["exempt"]
    assert "code/some-lesson" in manifest["holdout_population"]


def _make_manifest_file(tmp_path: Path, **categories) -> Path:
    """Write a minimal manifest YAML and return its path."""
    lines = ["version: 1", "updated_at: ''"]
    for cat in ("validated_core", "exempt", "holdout_population"):
        lines.append(f"{cat}:")
        lines.extend(f"- {item}" for item in categories.get(cat, []))
    p = tmp_path / "manifest.yaml"
    p.write_text("\n".join(lines) + "\n")
    return p


def test_classify_lesson_holdout(monkeypatch, tmp_path):
    _reset_manifest_cache(monkeypatch)
    p = _make_manifest_file(
        tmp_path, holdout_population=["patterns/persistent-learning"]
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(p))
    policy_class, version = _classify_lesson(
        str(tmp_path / "lessons" / "patterns" / "persistent-learning.md")
    )
    assert policy_class == "holdout"
    assert version == 1


def test_classify_lesson_validated_core(monkeypatch, tmp_path):
    _reset_manifest_cache(monkeypatch)
    p = _make_manifest_file(tmp_path, validated_core=["code/important"])
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(p))
    policy_class, version = _classify_lesson("lessons/code/important.md")
    assert policy_class == "validated_core"


def test_classify_lesson_exempt(monkeypatch, tmp_path):
    _reset_manifest_cache(monkeypatch)
    p = _make_manifest_file(tmp_path, exempt=["safety/critical"])
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(p))
    policy_class, _ = _classify_lesson(
        str(tmp_path / "lessons" / "safety" / "critical.md")
    )
    assert policy_class == "exempt"


def test_classify_lesson_unknown(monkeypatch, tmp_path):
    _reset_manifest_cache(monkeypatch)
    p = _make_manifest_file(tmp_path)  # empty manifest (manifest EXISTS, lesson absent)
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(p))
    policy_class, _ = _classify_lesson("lessons/new/brand-new.md")
    assert policy_class == "unknown"


def test_classify_lesson_no_manifest_defaults_to_holdout(monkeypatch, tmp_path):
    """No manifest file → default evaluation population is holdout, not unknown."""
    _reset_manifest_cache(monkeypatch)
    monkeypatch.setenv(
        "LESSON_POLICY_MANIFEST_PATH", str(tmp_path / "nonexistent.yaml")
    )
    policy_class, _ = _classify_lesson("lessons/any/lesson.md")
    assert policy_class == "holdout"


def test_load_policy_manifest_invalid_yaml_structure(monkeypatch, tmp_path):
    """Non-dict manifest YAML falls back to missing-default (holdout, not unknown)."""
    _reset_manifest_cache(monkeypatch)
    manifest_file = tmp_path / "manifest.yaml"
    manifest_file.write_text("- just\n- a\n- list\n")
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))
    manifest = _load_policy_manifest()
    assert manifest["version"] == 1
    assert manifest["validated_core"] == []
    assert manifest["holdout_population"] == []
    # Load failures should also default to holdout (not unknown)
    assert manifest.get("_manifest_missing") is True


def test_classify_lesson_load_failure_defaults_to_holdout(monkeypatch, tmp_path):
    """When manifest exists but can't be parsed, lessons default to holdout."""
    _reset_manifest_cache(monkeypatch)
    manifest_file = tmp_path / "manifest.yaml"
    manifest_file.write_text("- just\n- a\n- list\n")  # non-dict YAML
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))
    policy_class, _ = _classify_lesson("lessons/any/lesson.md")
    assert policy_class == "holdout"


def test_classify_lesson_malformed_category_value(monkeypatch, tmp_path, caplog):
    """Non-list category values are skipped safely and emit an operator warning."""
    _reset_manifest_cache(monkeypatch)
    manifest_file = tmp_path / "manifest.yaml"
    # validated_core is a string (malformed), holdout_population is correct
    manifest_file.write_text(
        "version: 1\nupdated_at: ''\nvalidated_core: 'not-a-list'\nexempt: []\nholdout_population:\n- patterns/foo\n"
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))
    # The malformed validated_core is skipped; holdout_population matches correctly
    policy_class, _ = _classify_lesson("lessons/patterns/foo.md")
    assert policy_class == "holdout"
    assert "validated_core has unexpected type (str)" in caplog.text
    # A non-matching lesson gets unknown (manifest loaded successfully)
    _reset_manifest_cache(monkeypatch)
    policy_class2, _ = _classify_lesson("lessons/other/bar.md")
    assert policy_class2 == "unknown"


def test_classify_lesson_custom_root_no_root_returns_unknown(monkeypatch, tmp_path):
    """Without a declared root, a path with no 'lessons' component conservatively
    returns unknown/holdout rather than attempting suffix enumeration (which would
    accept unrelated custom-root lessons)."""
    _reset_manifest_cache(monkeypatch)
    p = _make_manifest_file(
        tmp_path, holdout_population=["patterns/persistent-learning"]
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(p))
    # Path has no 'lessons' component and no root is declared — suffix enumeration
    # is unsafe; must return unknown (manifest exists, lesson simply unclassifiable
    # without a root anchor).
    policy_class, _ = _classify_lesson("/opt/guidance/patterns/persistent-learning.md")
    assert policy_class == "unknown"


def test_classify_lesson_custom_root_with_root_declared(monkeypatch, tmp_path):
    """When the manifest declares a root, custom paths are classified via exact
    relative-path lookup, including nested categories."""
    _reset_manifest_cache(monkeypatch)
    root_dir = tmp_path / "guidance"
    root_dir.mkdir()
    manifest_file = tmp_path / "manifest.yaml"
    manifest_file.write_text(
        f"version: 1\nupdated_at: ''\nroot: {root_dir}\n"
        "validated_core: []\nexempt: []\nholdout_population:\n"
        "- patterns/persistent-learning\n"
        "- patterns/sub/persistent-learning\n"
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))
    # Flat category
    policy_class, _ = _classify_lesson(
        str(root_dir / "patterns" / "persistent-learning.md")
    )
    assert policy_class == "holdout"
    # Nested category: exact relative path 'patterns/sub/persistent-learning' matches
    _reset_manifest_cache(monkeypatch)
    policy_class2, _ = _classify_lesson(
        str(root_dir / "patterns" / "sub" / "persistent-learning.md")
    )
    assert policy_class2 == "holdout"


def test_classify_lesson_non_string_category_entries_ignored(monkeypatch, tmp_path):
    """Non-string category list elements (YAML mappings, nested lists) are skipped
    without raising TypeError, so dropout records are not suppressed."""
    _reset_manifest_cache(monkeypatch)
    manifest_file = tmp_path / "manifest.yaml"
    # A YAML list that mixes strings with a mapping entry (malformed but valid YAML)
    manifest_file.write_text(
        "version: 1\n"
        "updated_at: ''\n"
        "validated_core:\n"
        "- code/important\n"
        "- {nested: mapping}\n"  # non-string element — must not raise
        "exempt: []\n"
        "holdout_population: []\n"
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))
    # The valid string entry must still match; the mapping entry must be silently skipped
    policy_class, _ = _classify_lesson("lessons/code/important.md")
    assert policy_class == "validated_core"
    # A lesson not in any category resolves to unknown (no TypeError raised)
    policy_class2, _ = _classify_lesson("lessons/other/foo.md")
    assert policy_class2 == "unknown"


def test_classify_lesson_custom_root_overlapping_suffix_with_root(
    monkeypatch, tmp_path
):
    """With a declared root, exact relative-path lookup is used — 'validated_core'
    entry 'sub/foo' cannot shadow the intended 'patterns/sub/foo' entry in
    holdout_population because only one candidate key is produced.

    Regression for the earlier suffix-priority bug: without root anchoring the
    suffix-enumeration path would check both keys and the shorter one in a
    higher-priority class could win. With root declared, only the exact relative
    path is tried, so the correct class is returned.
    """
    _reset_manifest_cache(monkeypatch)
    root_dir = tmp_path / "guidance"
    root_dir.mkdir()
    manifest_file = tmp_path / "manifest.yaml"
    manifest_file.write_text(
        f"version: 1\nupdated_at: ''\nroot: {root_dir}\n"
        "validated_core:\n- sub/persistent-learning\n"
        "exempt: []\n"
        "holdout_population:\n- patterns/sub/persistent-learning\n"
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))
    # Exact relative path from root is 'patterns/sub/persistent-learning' → holdout
    policy_class, _ = _classify_lesson(
        str(root_dir / "patterns" / "sub" / "persistent-learning.md")
    )
    assert policy_class == "holdout"


def test_load_policy_manifest_empty_mapping_is_not_missing(monkeypatch, tmp_path):
    """A valid-but-empty manifest ({}) exists, so lessons should classify as
    'unknown', not fall back to the missing-manifest 'holdout' default."""
    _reset_manifest_cache(monkeypatch)
    manifest_file = tmp_path / "manifest.yaml"
    manifest_file.write_text("{}\n")
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))
    manifest = _load_policy_manifest()
    assert manifest.get("_manifest_missing") is not True
    policy_class, _ = _classify_lesson("lessons/any/lesson.md")
    assert policy_class == "unknown"


def test_dropout_log_withheld_has_policy_fields(monkeypatch, tmp_path):
    """Withheld entries in dropout log carry policy_class and policy_version."""
    import random as _random

    _reset_manifest_cache(monkeypatch)
    log_dir = tmp_path / "drop"
    p = _make_manifest_file(
        tmp_path, holdout_population=["category/lesson-a", "category/lesson-b"]
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(p))
    monkeypatch.setenv("LESSON_DROPOUT_EPSILON", "1.0")  # withhold all
    monkeypatch.setenv("LESSON_DROPOUT_LOG_DIR", str(log_dir))
    monkeypatch.setenv("GPTME_SESSION_ID", "sess-withheld-policy")
    monkeypatch.delenv("CC_SESSION_ID", raising=False)

    matches = [
        _MockMatch(_make_lesson("A", "body", "lessons/category/lesson-a.md")),
        _MockMatch(_make_lesson("B", "body", "lessons/category/lesson-b.md")),
    ]
    _random.seed(0)
    kept = _apply_lesson_dropout(matches)
    assert kept == []  # all withheld at epsilon=1.0

    records = [
        json.loads(line)
        for line in (log_dir / "sess-withheld-policy.jsonl").read_text().splitlines()
        if line
    ]
    assert len(records) == 1
    rec = records[0]
    assert rec["matched"] == []
    assert len(rec["withheld"]) == 2
    for entry in rec["withheld"]:
        assert entry["policy_class"] == "holdout"
        assert entry["policy_version"] == 1
        assert "path" in entry
        assert "title" in entry


def test_dropout_log_matched_has_policy_fields(monkeypatch, tmp_path):
    """Kept (matched) entries in dropout log carry policy_class and policy_version."""
    import random as _random

    _reset_manifest_cache(monkeypatch)
    log_dir = tmp_path / "drop"
    p = _make_manifest_file(tmp_path, validated_core=["category/kept"])
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(p))
    monkeypatch.setenv("LESSON_DROPOUT_EPSILON", "0.25")
    monkeypatch.setenv("LESSON_DROPOUT_LOG_DIR", str(log_dir))
    monkeypatch.setenv("GPTME_SESSION_ID", "sess-matched-policy")
    monkeypatch.delenv("CC_SESSION_ID", raising=False)

    # Patch random to always keep (return > epsilon)
    monkeypatch.setattr(_random, "random", lambda: 0.9)

    matches = [
        _MockMatch(_make_lesson("Kept", "body", "lessons/category/kept.md")),
    ]
    kept = _apply_lesson_dropout(matches)
    assert len(kept) == 1  # not withheld

    records = [
        json.loads(line)
        for line in (log_dir / "sess-matched-policy.jsonl").read_text().splitlines()
        if line
    ]
    assert len(records) == 1
    rec = records[0]
    assert rec["withheld"] == []
    assert len(rec["matched"]) == 1
    entry = rec["matched"][0]
    assert entry["policy_class"] == "validated_core"
    assert entry["policy_version"] == 1
    assert "path" in entry
    assert "title" in entry
    assert entry["effective_epsilon"] == 0.05  # validated_core default, not global 0.25


# --- Manifest root-anchored classification (Greptile finding) ---


def _make_manifest_file_with_root(tmp_path: Path, root: str, **categories) -> Path:
    """Write a manifest YAML with a root field and return its path."""
    lines = ["version: 1", "updated_at: ''", f"root: {root}"]
    for cat in ("validated_core", "exempt", "holdout_population"):
        lines.append(f"{cat}:")
        lines.extend(f"- {item}" for item in categories.get(cat, []))
    p = tmp_path / "manifest.yaml"
    p.write_text("\n".join(lines) + "\n")
    return p


def test_classify_lesson_malformed_root_is_ignored(monkeypatch, tmp_path):
    """A non-string `root` value in the manifest (e.g. integer, list) must not raise
    TypeError — it should be treated as if no root was declared, falling through to
    the lessons-component heuristic or conservative unknown return."""
    _reset_manifest_cache(monkeypatch)
    manifest_file = tmp_path / "manifest.yaml"
    # root is an integer — invalid but legal YAML
    manifest_file.write_text(
        "version: 1\nupdated_at: ''\nroot: 42\n"
        "validated_core:\n- category/lesson\n"
        "exempt: []\nholdout_population: []\n"
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))
    # Should not raise; malformed root is skipped → falls through to lessons heuristic
    policy_class, _ = _classify_lesson("lessons/category/lesson.md")
    assert policy_class == "validated_core"
    # Path with no lessons component → conservative unknown (no suffix enumeration)
    _reset_manifest_cache(monkeypatch)
    policy_class2, _ = _classify_lesson("/opt/custom/category/lesson.md")
    assert policy_class2 == "unknown"


def test_classify_lesson_root_check_precedes_lessons_component(monkeypatch, tmp_path):
    """When manifest declares a root, it takes precedence over the 'lessons' heuristic.

    A path that contains a 'lessons' component but is outside the declared root
    must NOT match manifest entries. This prevents a lesson from an outside workspace
    that happens to have a 'lessons/' directory from inheriting entries intended for
    this root.
    """
    _reset_manifest_cache(monkeypatch)
    root_dir = tmp_path / "root-a"
    root_dir.mkdir()
    p = _make_manifest_file_with_root(
        tmp_path,
        root=str(root_dir),
        validated_core=["patterns/foo"],
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(p))
    # This path has a 'lessons' component AND shares the suffix 'patterns/foo',
    # but it is NOT under the declared root — must return unknown, not validated_core.
    outside_path = tmp_path / "other-workspace" / "lessons" / "patterns" / "foo.md"
    outside_path.parent.mkdir(parents=True)
    policy_class, _ = _classify_lesson(str(outside_path))
    assert policy_class == "unknown"


def test_classify_lesson_manifest_root_exact_match(monkeypatch, tmp_path):
    """When manifest declares a root, classify via exact relative-path lookup."""
    _reset_manifest_cache(monkeypatch)
    root_dir = tmp_path / "custom-lessons"
    root_dir.mkdir()
    p = _make_manifest_file_with_root(
        tmp_path,
        root=str(root_dir),
        validated_core=["patterns/foo"],
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(p))
    policy_class, _ = _classify_lesson(str(root_dir / "patterns" / "foo.md"))
    assert policy_class == "validated_core"


def test_classify_lesson_manifest_root_outside_root_returns_unknown(
    monkeypatch, tmp_path
):
    """Path outside the manifest root must not match manifest entries (no suffix false-positive)."""
    _reset_manifest_cache(monkeypatch)
    root_a = tmp_path / "root-a"
    root_a.mkdir()
    root_b = tmp_path / "root-b"
    root_b.mkdir()
    # Manifest is anchored to root_a with key "patterns/foo"
    p = _make_manifest_file_with_root(
        tmp_path,
        root=str(root_a),
        validated_core=["patterns/foo"],
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(p))
    # Lesson at root_b shares the same relative suffix — must NOT inherit the class
    policy_class, _ = _classify_lesson(str(root_b / "patterns" / "foo.md"))
    assert policy_class == "unknown"  # manifest exists but path is outside root


def test_classify_lesson_manifest_root_prevents_short_key_false_positive(
    monkeypatch, tmp_path
):
    """Short stem-only manifest key must not match a lesson at a different custom root."""
    _reset_manifest_cache(monkeypatch)
    root_a = tmp_path / "root-a"
    root_a.mkdir()
    root_b = tmp_path / "root-b"
    root_b.mkdir()
    # Manifest anchored to root_a with a stem-only key "foo"
    p = _make_manifest_file_with_root(
        tmp_path,
        root=str(root_a),
        validated_core=["foo"],
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(p))
    # A lesson at root_b with the same stem must not inherit validated_core
    policy_class, _ = _classify_lesson(str(root_b / "sub" / "foo.md"))
    assert policy_class == "unknown"


def test_classify_lesson_relative_manifest_root(monkeypatch, tmp_path):
    """A relative `root:` in the manifest is resolved against the manifest
    file's directory (not CWD) so absolute lesson paths under that root are
    classified correctly regardless of where the hook runs.

    Regression for: `Path(abs_lesson).relative_to(Path("lessons"))` raises
    ValueError because an absolute target can't be made relative to a relative
    base — causing every valid in-root lesson to fall through to 'unknown'.

    Second regression (this test): resolving relative roots against CWD causes
    `unknown` classification when the hook runs from a workspace subdirectory
    rather than the project root, because CWD/lessons ≠ manifest_parent/lessons.
    The fix anchors to the manifest file's parent, which is CWD-independent.
    """
    _reset_manifest_cache(monkeypatch)
    # lessons_dir is a sibling of the manifest file (both under tmp_path).
    # A relative `root: lessons` in the manifest resolves to tmp_path/lessons
    # when anchored to the manifest's parent — correct regardless of CWD.
    lessons_dir = tmp_path / "lessons"
    (lessons_dir / "patterns").mkdir(parents=True)
    lesson = lessons_dir / "patterns" / "foo.md"
    lesson.write_text("# Foo\n")

    manifest_file = tmp_path / "manifest.yaml"
    # `root: lessons` — a relative path; anchored to manifest_file.parent = tmp_path
    manifest_file.write_text(
        "version: 1\nupdated_at: ''\nroot: lessons\n"
        "validated_core:\n- patterns/foo\n"
        "exempt: []\nholdout_population: []\n"
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))
    # Deliberately do NOT chdir to tmp_path — the fix must work from any CWD.

    policy_class, _ = _classify_lesson(str(lesson))
    assert policy_class == "validated_core"


def test_classify_lesson_relative_root_cwd_independent(monkeypatch, tmp_path):
    """Relative `root:` resolves against the manifest file's location, not CWD.

    Regression: when the hook runs from a workspace subdirectory, the old
    `resolve()` (CWD-anchored) mapped `root: lessons` to
    `<subdirectory>/lessons`, while lesson paths were rooted at the workspace
    root. Every in-root lesson was misclassified as `unknown`. This test
    verifies correct classification even when CWD is a subdirectory.
    """
    _reset_manifest_cache(monkeypatch)
    lessons_dir = tmp_path / "lessons"
    (lessons_dir / "patterns").mkdir(parents=True)
    lesson = lessons_dir / "patterns" / "foo.md"
    lesson.write_text("# Foo\n")

    manifest_file = tmp_path / "manifest.yaml"
    manifest_file.write_text(
        "version: 1\nupdated_at: ''\nroot: lessons\n"
        "validated_core:\n- patterns/foo\n"
        "exempt: []\nholdout_population: []\n"
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))

    # Simulate running from a workspace subdirectory — CWD ≠ tmp_path.
    # Under the old CWD-anchored resolve(), `root: lessons` would map to
    # `<subdir>/lessons`, causing `relative_to` to raise ValueError and
    # the lesson to be classified as `unknown`.
    subdir = tmp_path / "gptme" / "lessons"
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)

    policy_class, _ = _classify_lesson(str(lesson))
    assert policy_class == "validated_core"


def test_classify_lesson_cached_manifest_cwd_change_stable(monkeypatch, tmp_path):
    """CWD change after manifest is cached must not break relative-root anchoring.

    Regression for: when LESSON_POLICY_MANIFEST_PATH is relative and the process
    CWD changes between manifest-load time and classify time, the old
    `_get_policy_manifest_path().resolve()` call at classify time resolved against
    the *new* CWD, producing the wrong anchor directory and misclassifying every
    in-root lesson as `unknown`.

    The fix: resolve the manifest path once at load time and store it as
    `_manifest_abs_path` in the cached dict. Classify time reads that stored path.

    Layout: manifest at workspace root (so `root: lessons` anchors to workspace/lessons).
    The manifest PATH env var is relative — that is the precondition that triggers the bug.
    """
    _reset_manifest_cache(monkeypatch)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    lessons_dir = workspace / "lessons"
    (lessons_dir / "patterns").mkdir(parents=True)
    lesson = lessons_dir / "patterns" / "foo.md"
    lesson.write_text("# Foo\n")

    # Manifest at workspace root; `root: lessons` resolves to workspace/lessons
    # when anchored to manifest_file.parent = workspace.
    manifest_file = workspace / "manifest.yaml"
    manifest_file.write_text(
        "version: 1\nupdated_at: ''\nroot: lessons\n"
        "validated_core:\n- patterns/foo\n"
        "exempt: []\nholdout_population: []\n"
    )

    # Use a RELATIVE manifest path — the bug only triggers when the path is relative.
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", "manifest.yaml")
    monkeypatch.chdir(
        workspace
    )  # CWD = workspace: "manifest.yaml" resolves correctly here

    # Load and cache the manifest while CWD = workspace.
    _load_policy_manifest()

    # Simulate CWD change (e.g. hook is called later from a different directory).
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    # Without the fix: `_get_policy_manifest_path().resolve()` now resolves to
    # `<other_dir>/manifest.yaml` (wrong base), so
    # `manifest_root = <other_dir>/lessons` and `relative_to` raises ValueError →
    # lesson is classified as "unknown" instead of "validated_core".
    policy_class, _ = _classify_lesson(str(lesson))
    assert policy_class == "validated_core"


def test_policy_manifest_cache_reloads_after_file_change(monkeypatch, tmp_path):
    """A long-lived process sees policy updates without requiring a restart."""
    _reset_manifest_cache(monkeypatch)
    manifest_file = tmp_path / "manifest.yaml"
    manifest_file.write_text(
        "version: 1\nvalidated_core: []\nexempt: []\nholdout_population: []\n"
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))
    assert _classify_lesson("lessons/patterns/foo.md")[0] == "unknown"

    manifest_file.write_text(
        "version: 2\nvalidated_core:\n- patterns/foo\n"
        "exempt: []\nholdout_population: []\n"
    )
    assert _classify_lesson("lessons/patterns/foo.md") == ("validated_core", 2)


def test_policy_manifest_cache_reload_survives_cwd_change(monkeypatch, tmp_path):
    """A relative manifest path keeps its load-time anchor during reload."""
    _reset_manifest_cache(monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest_file = workspace / "manifest.yaml"
    manifest_file.write_text(
        "version: 1\nvalidated_core: []\nexempt: []\nholdout_population: []\n"
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", "manifest.yaml")
    monkeypatch.chdir(workspace)
    assert _classify_lesson("lessons/patterns/foo.md")[0] == "unknown"

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)
    manifest_file.write_text(
        "version: 2\nvalidated_core:\n- patterns/foo\n"
        "exempt: []\nholdout_population: []\n"
    )

    assert _classify_lesson("lessons/patterns/foo.md") == ("validated_core", 2)


def test_policy_manifest_cache_reloads_same_size_preserved_mtime(monkeypatch, tmp_path):
    """Metadata-preserving rewrites still invalidate the process cache."""
    _reset_manifest_cache(monkeypatch)
    manifest_file = tmp_path / "manifest.yaml"
    original = (
        "version: 1\nvalidated_core:\n- patterns/foo\n"
        "exempt: []\nholdout_population: []\n"
    )
    replacement = original.replace("patterns/foo", "patterns/bar")
    assert len(original) == len(replacement)
    manifest_file.write_text(original)
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))
    assert _classify_lesson("lessons/patterns/foo.md")[0] == "validated_core"

    original_stat = manifest_file.stat()
    manifest_file.write_text(replacement)
    os.utime(
        manifest_file,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )

    assert _classify_lesson("lessons/patterns/bar.md")[0] == "validated_core"


def test_classify_lesson_relative_path_uses_declared_root(monkeypatch, tmp_path):
    """Relative lesson paths share the manifest-root anchor, not process CWD."""
    _reset_manifest_cache(monkeypatch)
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir()
    manifest_file = tmp_path / "manifest.yaml"
    manifest_file.write_text(
        "version: 1\nroot: lessons\nvalidated_core:\n- patterns/foo\n"
        "exempt: []\nholdout_population: []\n"
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert _classify_lesson("patterns/foo.md")[0] == "validated_core"


def test_classify_lesson_absolute_path_without_root_is_workspace_anchored(
    monkeypatch, tmp_path
):
    """No-root absolute paths match only the manifest workspace's lessons tree."""
    _reset_manifest_cache(monkeypatch)
    manifest_file = _make_manifest_file(tmp_path, validated_core=["patterns/important"])
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))

    local = tmp_path / "lessons" / "patterns" / "important.md"
    assert _classify_lesson(str(local))[0] == "validated_core"
    assert (
        _classify_lesson("/other/workspace/lessons/patterns/important.md")[0]
        == "unknown"
    )


def test_classify_lesson_absolute_root_with_dotdot_resolves(monkeypatch, tmp_path):
    """Absolute root containing `..` must be resolved before relative_to comparison.

    Without .resolve(), Path('/opt/../lessons').relative_to('/opt/lessons') raises
    ValueError (lexical comparison, no normalization), misclassifying valid lessons.
    """
    _reset_manifest_cache(monkeypatch)

    lessons_dir = tmp_path / "lessons"
    (lessons_dir / "patterns").mkdir(parents=True)
    lesson = lessons_dir / "patterns" / "foo.md"
    lesson.write_text("# Foo\n")

    # Construct an absolute root with a `..` component that resolves to lessons_dir.
    other = tmp_path / "other"
    other.mkdir()
    dotdot_root = str(other / ".." / "lessons")  # absolute but not normalized

    manifest_file = tmp_path / "manifest.yaml"
    manifest_file.write_text(
        f"version: 1\nupdated_at: ''\nroot: {dotdot_root}\n"
        "validated_core:\n- patterns/foo\n"
        "exempt: []\nholdout_population: []\n"
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))

    policy_class, _ = _classify_lesson(str(lesson))
    assert policy_class == "validated_core"


def test_classify_lesson_symlinked_path_component_matches_root(monkeypatch, tmp_path):
    """A symlinked component in an *absolute* lesson path must still match the root.

    `manifest_root` is always `.resolve()`d, but an absolute lesson path is used
    as-is. When the lesson is reached through a symlink (a symlinked workspace
    root, `/tmp` on macOS, `$HOME` behind a symlink) the two sides spell the same
    file differently, `relative_to()` raises ValueError, and every in-root lesson
    is silently mislabelled "unknown" in the shadow log.
    """
    _reset_manifest_cache(monkeypatch)

    real = tmp_path / "real"
    (real / "lessons" / "patterns").mkdir(parents=True)
    (real / "lessons" / "patterns" / "foo.md").write_text("# Foo\n")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    manifest_file = _make_manifest_file_with_root(
        real, "lessons", validated_core=["patterns/foo"]
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))

    # Sanity: the real path classifies correctly.
    assert _classify_lesson(str(real / "lessons" / "patterns" / "foo.md"))[0] == (
        "validated_core"
    )

    # The same file reached via the symlink must classify identically.
    _reset_manifest_cache(monkeypatch)
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))
    assert _classify_lesson(str(link / "lessons" / "patterns" / "foo.md"))[0] == (
        "validated_core"
    )


def test_classify_lesson_symlinked_manifest_and_lesson_matches_root(
    monkeypatch, tmp_path
):
    """Reaching *both* manifest and lesson through the same symlink must classify.

    This is the realistic deployment shape (whole workspace behind a symlink):
    the manifest path is resolved at load time while the lesson path is not, so
    the asymmetry bites even when caller paths are internally consistent.
    """
    _reset_manifest_cache(monkeypatch)

    real = tmp_path / "real"
    (real / "lessons" / "patterns").mkdir(parents=True)
    (real / "lessons" / "patterns" / "foo.md").write_text("# Foo\n")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    _make_manifest_file_with_root(real, "lessons", validated_core=["patterns/foo"])
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(link / "manifest.yaml"))

    assert _classify_lesson(str(link / "lessons" / "patterns" / "foo.md"))[0] == (
        "validated_core"
    )


def test_classify_lesson_absolute_lesson_path_with_dotdot_matches_root(
    monkeypatch, tmp_path
):
    """An absolute lesson path containing `..` must normalize before comparison.

    The root side is resolved, the lesson side was not — so `..` in the lesson
    path produced a spurious "unknown".
    """
    _reset_manifest_cache(monkeypatch)

    lessons_dir = tmp_path / "lessons"
    (lessons_dir / "patterns").mkdir(parents=True)
    (lessons_dir / "patterns" / "foo.md").write_text("# Foo\n")
    (tmp_path / "other").mkdir()

    manifest_file = _make_manifest_file_with_root(
        tmp_path, "lessons", validated_core=["patterns/foo"]
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))

    dotdot_lesson = str(tmp_path / "other" / ".." / "lessons" / "patterns" / "foo.md")
    assert _classify_lesson(dotdot_lesson)[0] == "validated_core"


def test_classify_lesson_symlinked_lesson_file_outside_root_still_matches(
    monkeypatch, tmp_path
):
    """A lesson file that is a symlink *out of* the root keeps its discovered class.

    LessonIndex indexes symlinked lesson files under the directory they were
    discovered in (deduping by realpath), so classification must use the
    discovery path, not the symlink target. Guards against "just resolve()
    everything", which would send such lessons to "unknown".
    """
    _reset_manifest_cache(monkeypatch)

    lessons_dir = tmp_path / "lessons" / "patterns"
    lessons_dir.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    (external / "foo.md").write_text("# Foo\n")
    (lessons_dir / "foo.md").symlink_to(external / "foo.md")

    manifest_file = _make_manifest_file_with_root(
        tmp_path, "lessons", validated_core=["patterns/foo"]
    )
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(manifest_file))

    assert _classify_lesson(str(lessons_dir / "foo.md"))[0] == "validated_core"


# --- Class-aware dropout (Phase 1: differential epsilon) ---

from gptme.lessons.auto_include import (
    _get_dropout_epsilon_for_class,
    _get_dropout_epsilon_validated_core,
)


def test_dropout_epsilon_validated_core_default(monkeypatch):
    monkeypatch.delenv("LESSON_DROPOUT_EPSILON_VALIDATED_CORE", raising=False)
    assert _get_dropout_epsilon_validated_core() == 0.05


def test_dropout_epsilon_validated_core_env_override(monkeypatch):
    monkeypatch.setenv("LESSON_DROPOUT_EPSILON_VALIDATED_CORE", "0.10")
    assert _get_dropout_epsilon_validated_core() == 0.10
    monkeypatch.setenv("LESSON_DROPOUT_EPSILON_VALIDATED_CORE", "0.0")
    assert _get_dropout_epsilon_validated_core() == 0.0
    monkeypatch.setenv("LESSON_DROPOUT_EPSILON_VALIDATED_CORE", "2.0")
    assert _get_dropout_epsilon_validated_core() == 1.0
    monkeypatch.setenv("LESSON_DROPOUT_EPSILON_VALIDATED_CORE", "not-a-float")
    assert _get_dropout_epsilon_validated_core() == 0.05  # fallback to default


def test_dropout_epsilon_for_class_exempt_is_always_zero(monkeypatch):
    """exempt lessons must never be withheld regardless of global epsilon."""
    assert _get_dropout_epsilon_for_class("exempt", 0.20) == 0.0
    assert _get_dropout_epsilon_for_class("exempt", 1.0) == 0.0
    assert _get_dropout_epsilon_for_class("exempt", 0.0) == 0.0


def test_dropout_epsilon_for_class_validated_core_uses_lower_epsilon(monkeypatch):
    monkeypatch.delenv("LESSON_DROPOUT_EPSILON_VALIDATED_CORE", raising=False)
    # validated_core uses the lower default (0.05), not global_epsilon (0.20)
    eff = _get_dropout_epsilon_for_class("validated_core", 0.20)
    assert eff == 0.05
    assert eff < 0.20


def test_dropout_epsilon_for_class_holdout_uses_global(monkeypatch):
    assert _get_dropout_epsilon_for_class("holdout", 0.20) == 0.20
    assert _get_dropout_epsilon_for_class("unknown", 0.15) == 0.15


def test_dropout_exempt_lesson_never_withheld(monkeypatch, tmp_path):
    """An exempt lesson must never be withheld even at epsilon=1.0."""
    import random as _random

    import gptme.lessons.auto_include as _mod

    log_dir = tmp_path / "drop"
    exempt_path = "/tmp/exempt_lesson.md"

    # Inject a manifest that classifies this path as exempt.
    # Monkeypatch _load_policy_manifest directly — the cache-key comparison
    # inside the real loader won't match an injected key, so we bypass it.
    manifest = {
        "version": 1,
        "validated_core": [],
        "exempt": ["exempt_lesson"],
        "holdout_population": [],
        "root": "/tmp",
    }
    monkeypatch.setattr(_mod, "_load_policy_manifest", lambda: manifest)

    monkeypatch.setenv("LESSON_DROPOUT_EPSILON", "1.0")
    monkeypatch.setenv("LESSON_DROPOUT_LOG_DIR", str(log_dir))
    monkeypatch.setenv("GPTME_SESSION_ID", "sess-exempt-test")
    monkeypatch.delenv("CC_SESSION_ID", raising=False)

    _random.seed(99)
    matches = [_MockMatch(_make_lesson("Exempt", "body", exempt_path))]
    result = _apply_lesson_dropout(matches)

    # The exempt lesson must be kept even at epsilon=1.0
    assert len(result) == 1, "Exempt lesson was withheld — should be kept"


def test_dropout_withheld_records_contain_effective_epsilon(monkeypatch, tmp_path):
    """Withheld records must include effective_epsilon for per-class analysis."""
    import random as _random

    log_dir = tmp_path / "drop"
    monkeypatch.setenv(
        "LESSON_DROPOUT_EPSILON", "1.0"
    )  # withhold everything non-exempt
    monkeypatch.setenv("LESSON_DROPOUT_LOG_DIR", str(log_dir))
    monkeypatch.setenv("GPTME_SESSION_ID", "sess-eff-eps")
    monkeypatch.delenv("CC_SESSION_ID", raising=False)

    _random.seed(0)
    matches = [_MockMatch(_make_lesson("H", "body", "/tmp/holdout.md"))]
    _apply_lesson_dropout(matches)

    log_file = log_dir / "sess-eff-eps.jsonl"
    records = [json.loads(line) for line in log_file.read_text().splitlines() if line]
    assert records, "No log written"
    withheld = records[0]["withheld"]
    assert withheld, "No withheld lessons logged"
    assert "effective_epsilon" in withheld[0], (
        "effective_epsilon missing from withheld record — needed for per-class analysis"
    )


def test_dropout_kept_validated_core_records_override_epsilon(monkeypatch, tmp_path):
    """Kept validated_core entries must persist the class epsilon, not global.

    Greptile P1 on gptme/gptme#3700: a non-default
    LESSON_DROPOUT_EPSILON_VALIDATED_CORE was recorded only on withheld
    lessons, so kept observations could not reconstruct the assignment
    probability used for the coin flip.
    """
    import random as _random

    import gptme.lessons.auto_include as _mod

    _reset_manifest_cache(monkeypatch)
    log_dir = tmp_path / "drop"
    p = _make_manifest_file(tmp_path, validated_core=["category/kept-core"])
    monkeypatch.setenv("LESSON_POLICY_MANIFEST_PATH", str(p))
    monkeypatch.setenv("LESSON_DROPOUT_EPSILON", "0.25")
    monkeypatch.setenv("LESSON_DROPOUT_EPSILON_VALIDATED_CORE", "0.10")
    monkeypatch.setenv("LESSON_DROPOUT_LOG_DIR", str(log_dir))
    monkeypatch.setenv("GPTME_SESSION_ID", "sess-kept-eff-eps")
    monkeypatch.delenv("CC_SESSION_ID", raising=False)
    monkeypatch.setattr(_random, "random", lambda: 0.9)  # always keep

    matches = [
        _MockMatch(_make_lesson("Kept", "body", "lessons/category/kept-core.md")),
    ]
    kept = _mod._apply_lesson_dropout(matches)
    assert len(kept) == 1

    records = [
        json.loads(line)
        for line in (log_dir / "sess-kept-eff-eps.jsonl").read_text().splitlines()
        if line
    ]
    assert len(records) == 1
    rec = records[0]
    assert rec["epsilon"] == 0.25  # global switch
    assert rec["withheld"] == []
    assert len(rec["matched"]) == 1
    entry = rec["matched"][0]
    assert entry["policy_class"] == "validated_core"
    assert entry["effective_epsilon"] == 0.10, (
        "kept validated_core must record the class-specific epsilon "
        f"(got {entry.get('effective_epsilon')!r}, global was 0.25)"
    )
