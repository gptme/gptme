"""Guards for the import and indexing work a Claude Code lesson hook pays per call."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from gptme.util.keyword_matching import _match_keyword


def test_import_gptme_stays_light():
    """`import gptme` (and the hook CLI) must not pull in the command/LLM/tool stack."""
    code = (
        "import sys, gptme, gptme.cli.util; "
        "heavy = [m for m in ('gptme.commands', 'gptme.llm', 'gptme.tools', "
        "'gptme.message', 'rich.console', 'urllib.request') if m in sys.modules]; "
        "print(','.join(heavy))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == ""


def test_lesson_commands_still_registered():
    from gptme.commands import _command_registry

    assert "lesson" in _command_registry and "skills" in _command_registry


def test_lazy_lessons_package_exports():
    import gptme.lessons as lessons

    assert lessons.LessonIndex.__name__ == "LessonIndex"
    assert callable(lessons.parse_lesson)
    with pytest.raises(AttributeError):
        _ = lessons.does_not_exist


@pytest.mark.parametrize(
    ("keyword", "text", "expected"),
    [
        ("trajectory", "Delete TRAJECTORY files", True),
        ("no-session-persistence", "add --no-session-persistence here", True),
        ("err", "berry", True),  # substring semantics, as before
        ("a.b", "axb", False),  # literal, not regex
        ("timeout*", "timeouts happened", True),
        ("*", "anything", False),
        ("", "anything", False),
    ],
)
def test_literal_keywords_match_like_the_regex(keyword, text, expected):
    assert _match_keyword(keyword, text) is expected


def test_disk_cache_round_trip(tmp_path, monkeypatch):
    from gptme.lessons import index as index_mod

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir()
    (lessons_dir / "a.md").write_text(
        "---\nmatch:\n  keywords: [alpha]\n---\n# A\nbody\n"
    )

    def fresh_process_state():
        monkeypatch.setattr(index_mod, "_LESSON_CACHE", {})
        monkeypatch.setattr(index_mod, "_disk_cache_loaded", False)
        monkeypatch.setattr(index_mod, "_disk_cache_dirty", False)

    fresh_process_state()
    first = index_mod.LessonIndex(lesson_dirs=[lessons_dir])
    assert [lesson.title for lesson in first.lessons] == ["A"]
    assert (tmp_path / "cache" / "gptme" / "lessons-cache.pickle").exists()

    fresh_process_state()
    calls: list[Path] = []
    real_parse = index_mod.parse_lesson

    def counting_parse(p):
        calls.append(p)
        return real_parse(p)

    monkeypatch.setattr(index_mod, "parse_lesson", counting_parse)
    second = index_mod.LessonIndex(lesson_dirs=[lessons_dir])
    assert [lesson.title for lesson in second.lessons] == ["A"]
    assert calls == []  # served from the disk cache

    # An edited file is re-parsed, not served stale.
    fresh_process_state()
    path = lessons_dir / "a.md"
    path.write_text("---\nmatch:\n  keywords: [alpha]\n---\n# A2\nbody\n")
    import os

    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    third = index_mod.LessonIndex(lesson_dirs=[lessons_dir])
    assert [lesson.title for lesson in third.lessons] == ["A2"]


def test_disk_cache_can_be_disabled(tmp_path, monkeypatch):
    from gptme.lessons import index as index_mod

    monkeypatch.setenv("GPTME_LESSON_CACHE", "0")
    assert index_mod._disk_cache_path() is None


def test_realpath_matches_os_realpath(tmp_path):
    from gptme.lessons.index import _realpath

    real = tmp_path / "real"
    real.mkdir()
    (real / "x.md").write_text("x")
    link_dir = tmp_path / "link"
    link_dir.symlink_to(real)
    (real / "target.md").write_text("t")
    (real / "alias.md").symlink_to(real / "target.md")
    import os

    for p in (real / "x.md", link_dir / "x.md", real / "alias.md"):
        assert _realpath(Path(p)) == os.path.realpath(p)
