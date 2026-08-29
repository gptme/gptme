"""Tests for session-start personal KB injection (gptme/gptme#3596 follow-on)."""

from pathlib import Path

import pytest

from gptme.hooks import StopPropagation
from gptme.message import Message


@pytest.fixture(autouse=True)
def isolated_kb(tmp_path, monkeypatch):
    """Redirect the knowledge store away from the real XDG data dir."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from gptme import dirs

    if hasattr(dirs.get_data_dir, "cache_clear"):
        dirs.get_data_dir.cache_clear()
    yield
    if hasattr(dirs.get_data_dir, "cache_clear"):
        dirs.get_data_dir.cache_clear()


def _run(initial_msgs, logdir: Path):
    from gptme.hooks.knowledge_inject import inject_session_knowledge

    return [
        item
        for item in inject_session_knowledge(
            logdir=logdir, workspace=None, initial_msgs=initial_msgs
        )
        if not isinstance(item, StopPropagation)
    ]


def test_hook_yields_hidden_system_message_for_matching_query(tmp_path):
    from gptme.knowledge import knowledge_save

    knowledge_save(
        "pytest test discovery fails",
        "prefix test function with test_",
        tags=["pytest"],
    )
    knowledge_save("git merge conflict resolution", "use git mergetool")

    out = _run(
        [Message("user", "pytest discovery is broken in CI")],
        tmp_path,
    )

    assert len(out) == 1
    msg = out[0]
    assert msg.role == "system"
    assert msg.hide is True
    assert "<knowledge-entries>" in msg.content
    assert "pytest test discovery fails" in msg.content
    assert "prefix test function with test_" in msg.content
    assert "git merge conflict" not in msg.content


def test_hook_yields_nothing_without_user_prompt(tmp_path):
    from gptme.knowledge import knowledge_save

    knowledge_save("pytest test discovery fails", "prefix test function with test_")

    assert _run([], tmp_path) == []
    assert _run([Message("system", "bootstrap")], tmp_path) == []


def test_hook_yields_nothing_for_short_query(tmp_path):
    from gptme.knowledge import knowledge_save

    knowledge_save("pytest test discovery fails", "prefix test function with test_")

    assert _run([Message("user", "hi")], tmp_path) == []


def test_hook_yields_nothing_when_store_empty(tmp_path):
    assert _run([Message("user", "pytest discovery is broken")], tmp_path) == []


def test_hook_yields_nothing_when_no_match(tmp_path):
    from gptme.knowledge import knowledge_save

    knowledge_save("pytest test discovery fails", "prefix test function with test_")

    assert _run([Message("user", "unrelated kubernetes helm chart")], tmp_path) == []


def test_hook_swallows_unexpected_errors(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "gptme.hooks.knowledge_inject.select_knowledge_for_session",
        boom,
    )

    assert _run([Message("user", "pytest discovery is broken")], tmp_path) == []


def test_register_adds_session_start_hook():
    from gptme.hooks import HookType, clear_hooks, get_hooks
    from gptme.hooks.knowledge_inject import register

    clear_hooks()
    register()
    names = [hook.name for hook in get_hooks(HookType.SESSION_START)]
    assert "knowledge_inject.session_start" in names
