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
    assert callable(lessons.register_lesson_commands)
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


def test_workspace_env_cannot_redirect_lesson_cache(tmp_path):
    """Importing the index after workspace env loading must not trust its cache."""
    import os

    trusted = tmp_path / "trusted-cache"
    untrusted = tmp_path / "workspace-cache"
    lesson = tmp_path / "user-lessons" / "a.md"
    lesson.parent.mkdir()
    lesson.write_text("---\nmatch:\n  keywords: [alpha]\n---\n# Authentic\nReal body\n")
    code = r"""
import json, os, sys
from pathlib import Path
import gptme
# Simulate prepare_execution_environment loading a repository's .env before
# the first lesson index import. The cache is user-owned, like checkout files.
os.environ["XDG_CACHE_HOME"] = sys.argv[1]
from gptme.lessons import index
lesson = Path(sys.argv[2])
forged = index._lesson_to_json(index.parse_lesson(lesson))
forged["title"] = "Impersonated user lesson"
forged["body"] = "Repository-controlled instructions"
st = lesson.stat()
cache = Path(sys.argv[1]) / "gptme" / "lessons-cache.json"
cache.parent.mkdir(parents=True)
cache.write_text(json.dumps({"key": index._disk_cache_key(),
    "entries": {str(lesson): [st.st_mtime, st.st_size, st.st_dev, st.st_ino, forged]}}))
cache.chmod(0o600)
loaded = index.LessonIndex(lesson_dirs=[lesson.parent]).lessons
assert [item.title for item in loaded] == ["Authentic"]
assert index._disk_cache_path() == Path(sys.argv[3]) / "gptme" / "lessons-cache.json"
"""
    subprocess.run(
        [sys.executable, "-c", code, str(untrusted), str(lesson), str(trusted)],
        env={**os.environ, "XDG_CACHE_HOME": str(trusted), "GPTME_LESSON_CACHE": "1"},
        capture_output=True,
        text=True,
        check=True,
    )


def test_disk_cache_round_trip(tmp_path, monkeypatch):
    from gptme.lessons import index as index_mod

    _fresh_cache(index_mod, monkeypatch, tmp_path)
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir()
    (lessons_dir / "a.md").write_text(
        "---\nmatch:\n  keywords: [alpha]\n---\n# A\nbody\n"
    )

    def fresh_process_state():
        monkeypatch.setattr(index_mod, "_FILE_SIGNATURES", {})
        monkeypatch.setattr(index_mod, "_LESSON_CACHE", {})
        monkeypatch.setattr(index_mod, "_disk_cache_loaded", False)
        monkeypatch.setattr(index_mod, "_disk_cache_dirty", False)

    fresh_process_state()
    first = index_mod.LessonIndex(lesson_dirs=[lessons_dir])
    assert [lesson.title for lesson in first.lessons] == ["A"]
    assert (tmp_path / "cache" / "gptme" / "lessons-cache.json").exists()

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


@pytest.mark.parametrize("reload_from_disk", [False, True])
def test_saving_unrelated_lesson_preserves_cached_size(
    tmp_path, monkeypatch, reload_from_disk
):
    """Saving B must not pair A's stale body with A's edited file size."""
    import os

    from gptme.lessons import index as index_mod

    _fresh_cache(index_mod, monkeypatch, tmp_path)
    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    a = a_dir / "a.md"
    a.write_text("---\nmatch:\n  keywords: [alpha]\n---\n# Original\n")
    index_mod.LessonIndex(lesson_dirs=[a_dir])
    if reload_from_disk:
        _fresh_cache(index_mod, monkeypatch, tmp_path)
        index_mod._load_disk_cache()
    st = a.stat()
    a.write_text("---\nmatch:\n  keywords: [alpha]\n---\n# Updated longer title\n")
    os.utime(a, ns=(st.st_atime_ns, st.st_mtime_ns))
    (b_dir / "b.md").write_text("---\nmatch:\n  keywords: [beta]\n---\n# B\n")
    index_mod.LessonIndex(lesson_dirs=[b_dir])

    _fresh_cache(index_mod, monkeypatch, tmp_path)
    loaded = index_mod.LessonIndex(lesson_dirs=[a_dir])
    assert [lesson.title for lesson in loaded.lessons] == ["Updated longer title"]


@pytest.mark.parametrize("reload_from_disk", [False, True])
@pytest.mark.parametrize("link_kind", ["directory", "file"])
def test_cache_invalidated_when_symlink_target_changes(
    tmp_path, monkeypatch, reload_from_disk, link_kind
):
    """Equal mtime/size must not hide a different file behind the same path."""
    import os

    from gptme.lessons import index as index_mod

    _fresh_cache(index_mod, monkeypatch, tmp_path)
    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    a, b = a_dir / "a.md", b_dir / "a.md"
    a.write_text("---\nmatch:\n  keywords: [alpha]\n---\n# Release A\n")
    b.write_text("---\nmatch:\n  keywords: [alpha]\n---\n# Release B\n")
    st = a.stat()
    os.utime(b, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert a.stat().st_size == b.stat().st_size
    if link_kind == "directory":
        link = tmp_path / "current"
        link.symlink_to(a_dir, target_is_directory=True)
        lesson_dir = link
    else:
        lesson_dir = tmp_path / "current"
        lesson_dir.mkdir()
        link = lesson_dir / "a.md"
        link.symlink_to(a)
    first = index_mod.LessonIndex(lesson_dirs=[lesson_dir])
    assert [lesson.title for lesson in first.lessons] == ["Release A"]

    link.unlink()
    link.symlink_to(
        b_dir if link_kind == "directory" else b,
        target_is_directory=link_kind == "directory",
    )
    if reload_from_disk:
        _fresh_cache(index_mod, monkeypatch, tmp_path)
        second = index_mod.LessonIndex(lesson_dirs=[lesson_dir])
    else:
        first.refresh()
        second = first
    assert [lesson.title for lesson in second.lessons] == ["Release B"]
    assert second.lessons[0].path == lesson_dir / "a.md"


def test_manifest_materialization_persists_across_processes(tmp_path, monkeypatch):
    """Lazy skill bodies persist, hit on the next run and reparse after edits."""
    import json
    import os

    from gptme.lessons import index as index_mod

    _fresh_cache(index_mod, monkeypatch, tmp_path)
    skills = tmp_path / "skills"
    skill_file = skills / "deploy" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("---\nname: deploy\n---\n# Deployment\nOriginal body\n")
    (skills / "index.json").write_text(
        json.dumps(
            {
                "skills": [
                    {"name": "deploy", "path": "deploy", "keywords": ["deployment"]}
                ]
            }
        )
    )
    calls: list[Path] = []
    real_parse = index_mod.parse_lesson

    def counting_parse(path):
        calls.append(path)
        return real_parse(path)

    monkeypatch.setattr(index_mod, "parse_lesson", counting_parse)

    def materialize_fresh():
        _fresh_cache(index_mod, monkeypatch, tmp_path)
        index = index_mod.LessonIndex(lesson_dirs=[skills])
        assert index.lessons[0].is_stub
        return index.materialize_lesson(index.lessons[0])

    first = materialize_fresh()
    assert "Original body" in first.body
    assert calls == [skill_file]
    cache = index_mod._disk_cache_path()
    assert cache is not None and cache.exists()
    assert str(skill_file) in json.loads(cache.read_text())["entries"]

    calls.clear()
    second = materialize_fresh()
    assert second.body == first.body
    assert calls == []

    skill_file.write_text("---\nname: deploy\n---\n# Deployment\nUpdated skill body\n")
    st = skill_file.stat()
    os.utime(skill_file, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    third = materialize_fresh()
    assert "Updated skill body" in third.body
    assert calls == [skill_file]
    calls.clear()
    assert materialize_fresh().body == third.body
    assert calls == []


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


def test_hook_uses_user_config_lessons_outside_a_workspace(tmp_path, monkeypatch):
    """A globally installed hook must inject lessons from gptme's user config
    even when the session's cwd has no gptme.toml."""
    import json

    from click.testing import CliRunner

    from gptme.cli import cmd_hooks
    from gptme.cli.util import main
    from gptme.lessons import index as index_mod

    _fresh_cache(index_mod, monkeypatch, tmp_path)
    lessons = tmp_path / "shared-lessons"
    lessons.mkdir()
    (lessons / "persist.md").write_text(
        "---\nmatch:\n  keywords: [no-session-persistence]\n---\n# Keep transcripts\nNever disable.\n"
    )
    config = tmp_path / "config" / "gptme"
    config.mkdir(parents=True)
    (config / "config.toml").write_text(f'[lessons]\ndirs = ["{lessons}"]\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    # Session/throttle state is resolved at import time; isolate it explicitly.
    monkeypatch.setattr(cmd_hooks, "_STATE_DIR", tmp_path / "state")
    workdir = tmp_path / "no-workspace"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "user-dirs-test",
        "tool_name": "SendMessage",
        "tool_input": {"message": "add --no-session-persistence to both"},
    }
    # The hook switches message output to JSON; restore it so later tests on
    # this worker are not left rendering nothing.
    from gptme.message import get_output_format, set_output_format

    prev_format = get_output_format()
    try:
        result = CliRunner().invoke(main, ["hooks", "run"], input=json.dumps(payload))
    finally:
        set_output_format(prev_format)
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert "Keep transcripts" in out.get("additionalContext", "")
    cache_path = tmp_path / "cache" / "gptme" / "lessons-cache.json"
    assert index_mod._disk_cache_path() == cache_path
    assert str(lessons / "persist.md") in json.loads(cache_path.read_text())["entries"]


def test_pretooluse_matcher_covers_all_tools():
    from gptme.cli import cmd_hooks

    assert cmd_hooks._PRETOOLUSE_MATCHER == "*"


def test_unicode_keywords_keep_ignorecase_semantics():
    # str.lower() leaves final sigma alone; re.IGNORECASE folds it.
    assert _match_keyword("οσ", "ος")
    assert _match_keyword("Ä", "ä")


def _fresh_cache(index_mod, monkeypatch, tmp_path):
    # Production captures this before workspace env loading. Isolate explicitly
    # instead of changing the live env and accidentally touching the user's cache.
    monkeypatch.setattr(index_mod, "_trusted_cache_base", str(tmp_path / "cache"))
    monkeypatch.setenv("GPTME_LESSON_CACHE", "1")
    monkeypatch.setattr(index_mod, "_FILE_SIGNATURES", {})
    monkeypatch.setattr(index_mod, "_LESSON_CACHE", {})
    monkeypatch.setattr(index_mod, "_disk_cache_loaded", False)
    monkeypatch.setattr(index_mod, "_disk_cache_dirty", False)


def test_disk_cache_is_json_private_and_absolute(tmp_path, monkeypatch):
    import json
    import stat as stat_mod

    from gptme.lessons import index as index_mod

    _fresh_cache(index_mod, monkeypatch, tmp_path)
    d = tmp_path / "lessons"
    d.mkdir()
    (d / "a.md").write_text("---\nmatch:\n  keywords: [alpha]\n---\n# A\nbody\n")
    index_mod.LessonIndex(lesson_dirs=[d])
    path = tmp_path / "cache" / "gptme" / "lessons-cache.json"
    data = json.loads(path.read_text())  # plain JSON, no code
    assert all(Path(k).is_absolute() for k in data["entries"])
    assert stat_mod.S_IMODE(path.stat().st_mode) & 0o077 == 0


def test_disk_cache_ignored_if_writable_by_others(tmp_path, monkeypatch):
    from gptme.lessons import index as index_mod

    _fresh_cache(index_mod, monkeypatch, tmp_path)
    d = tmp_path / "lessons"
    d.mkdir()
    (d / "a.md").write_text("---\nmatch:\n  keywords: [alpha]\n---\n# A\nbody\n")
    index_mod.LessonIndex(lesson_dirs=[d])
    path = tmp_path / "cache" / "gptme" / "lessons-cache.json"
    path.chmod(0o666)
    _fresh_cache(index_mod, monkeypatch, tmp_path)
    index_mod._load_disk_cache()
    assert index_mod._LESSON_CACHE == {}


def test_corrupt_disk_cache_is_ignored(tmp_path, monkeypatch):
    from gptme.lessons import index as index_mod

    _fresh_cache(index_mod, monkeypatch, tmp_path)
    path = tmp_path / "cache" / "gptme" / "lessons-cache.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x80\x04not json")
    path.chmod(0o600)
    index_mod._load_disk_cache()
    assert index_mod._LESSON_CACHE == {}


def test_realpath_cache_cleared_between_scans(tmp_path):
    import os

    from gptme.lessons.index import LessonIndex, _realpath

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    link = tmp_path / "link"
    link.symlink_to(a)
    assert _realpath(link / "x.md") == os.path.realpath(link / "x.md")
    link.unlink()
    link.symlink_to(b)
    LessonIndex(
        lesson_dirs=[tmp_path / "empty-does-not-exist"]
    )  # a scan clears the cache
    assert _realpath(link / "x.md") == os.path.realpath(link / "x.md")


def _write_user_config(tmp_path, monkeypatch, files: dict[str, str]) -> None:
    config = tmp_path / "config" / "gptme"
    config.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (config / name).write_text(text)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


def test_user_lesson_dirs_follow_runtime_main_local_layering(tmp_path, monkeypatch):
    from gptme.cli import cmd_hooks

    runtime_dir, main_dir, local_dir = (tmp_path / n for n in ("rt", "main", "local"))
    for d in (runtime_dir, main_dir, local_dir):
        d.mkdir()
    _write_user_config(
        tmp_path,
        monkeypatch,
        {"config.runtime.toml": f'[lessons]\ndirs = ["{runtime_dir}"]\n'},
    )
    assert cmd_hooks._user_lesson_dirs() == [runtime_dir]
    _write_user_config(
        tmp_path, monkeypatch, {"config.toml": f'[lessons]\ndirs = ["{main_dir}"]\n'}
    )
    assert cmd_hooks._user_lesson_dirs() == [main_dir]
    _write_user_config(
        tmp_path,
        monkeypatch,
        {"config.local.toml": f'[lessons]\ndirs = ["{local_dir}"]\n'},
    )
    assert cmd_hooks._user_lesson_dirs() == [local_dir]


@pytest.mark.parametrize(
    "text", ["lessons = []\n", "[lessons]\ndirs = 3\n", "not toml [[[\n"]
)
def test_malformed_user_lesson_config_is_ignored(tmp_path, monkeypatch, text):
    from gptme.cli import cmd_hooks

    _write_user_config(tmp_path, monkeypatch, {"config.toml": text})
    assert cmd_hooks._user_lesson_dirs() == []


def test_clear_cache_also_drops_disk_cache(tmp_path, monkeypatch):
    from gptme.lessons import index as index_mod

    _fresh_cache(index_mod, monkeypatch, tmp_path)
    lessons = tmp_path / "lessons"
    lessons.mkdir()
    (lessons / "a.md").write_text("---\nmatch:\n  keywords: [x]\n---\n# A\n")
    index_mod.LessonIndex(lesson_dirs=[lessons])
    cache_file = index_mod._disk_cache_path()
    assert cache_file is not None and cache_file.exists()

    index_mod.clear_cache()
    assert not cache_file.exists()
    assert index_mod._LESSON_CACHE == {}
    index_mod._load_disk_cache()  # must not resurrect anything
    assert index_mod._LESSON_CACHE == {}


def test_disk_cache_write_does_not_follow_planted_symlink(tmp_path, monkeypatch):
    import os

    from gptme.lessons import index as index_mod

    _fresh_cache(index_mod, monkeypatch, tmp_path)
    cache_file = index_mod._disk_cache_path()
    assert cache_file is not None
    cache_file.parent.mkdir(parents=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    # The old writer used a predictable PID-based temp name with O_TRUNC.
    os.symlink(victim, cache_file.with_name(f"{cache_file.name}.{os.getpid()}.tmp"))

    lessons = tmp_path / "lessons"
    lessons.mkdir()
    (lessons / "a.md").write_text("---\nmatch:\n  keywords: [x]\n---\n# A\n")
    index_mod.LessonIndex(lesson_dirs=[lessons])
    assert victim.read_text() == "precious"
    assert cache_file.exists() and not cache_file.is_symlink()
