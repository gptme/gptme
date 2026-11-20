"""Lesson discovery uses each conversation's workspace, without host chdir."""

from contextvars import Context
from pathlib import Path

import pytest

from gptme.config import Config, get_project_config, set_config
from gptme.config.user import UserConfig
from gptme.lessons.index import LessonIndex
from gptme.memory.match import _lesson_search_dirs
from gptme.tools.shell import set_workspace_cwd


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    roots = [tmp_path / name for name in ("a", "b", "host")]
    relative_dirs = [
        "lessons",
        ".gptme/lessons",
        "skills",
        ".gptme/skills",
        ".cursor",
        "custom",
        "extra",
        "plugins/demo/lessons",
    ]
    for root in roots:
        for relative in relative_dirs:
            (root / relative).mkdir(parents=True, exist_ok=True)
        (root / "plugins/demo/tools").mkdir()
        (root / "plugins/demo/__init__.py").write_text("")
        (root / "gptme.toml").write_text(
            '[lessons]\ndirs = ["custom"]\n[plugins]\npaths = ["plugins/demo"]\n'
        )
    monkeypatch.chdir(roots[-1])
    monkeypatch.setenv("GPTME_LESSONS_EXTRA_DIRS", "extra")
    return roots, relative_dirs


@pytest.mark.parametrize("memory_fallback", [False, True])
def test_lesson_roots_stay_in_the_session(workspaces, memory_fallback):
    roots, relative_dirs = workspaces
    for workspace in roots[:2]:
        context = Context()
        config = Config(
            user=UserConfig(),
            project=None
            if memory_fallback
            else get_project_config(workspace, quiet=True),
        )
        context.run(set_config, config)
        context.run(set_workspace_cwd, str(workspace))
        if memory_fallback:
            discovered = context.run(_lesson_search_dirs, (), True)
            assert config.project is None
        else:
            discovered = context.run(LessonIndex._default_dirs)
        resolved_dirs = {path.resolve() for path in discovered}

        assert {workspace / name for name in relative_dirs} <= resolved_dirs
        for other in roots:
            if other != workspace:
                assert not any(path.is_relative_to(other) for path in resolved_dirs)
    assert Path.cwd() == roots[-1]
