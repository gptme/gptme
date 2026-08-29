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


class _FakeLog:
    def __init__(self, messages):
        self.messages = messages


class _FakeManager:
    def __init__(self, messages, logdir: Path):
        self.log = _FakeLog(messages)
        self.logdir = logdir
        self.workspace = None


def _run(initial_msgs, logdir: Path, manager=None):
    from gptme.hooks.knowledge_inject import inject_session_knowledge

    return [
        item
        for item in inject_session_knowledge(
            logdir=logdir,
            workspace=None,
            initial_msgs=initial_msgs,
            manager=manager,
        )
        if not isinstance(item, StopPropagation)
    ]


def test_hook_yields_hidden_system_message_for_matching_query(tmp_path):
    from gptme.hooks.knowledge_inject import _INJECT_SENTINEL
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
    assert msg.content.lstrip().startswith(_INJECT_SENTINEL)
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


def test_register_adds_session_start_and_turn_pre_hooks():
    from gptme.hooks import HookType, clear_hooks, get_hooks
    from gptme.hooks.knowledge_inject import register

    clear_hooks()
    register()
    start_names = [hook.name for hook in get_hooks(HookType.SESSION_START)]
    turn_names = [hook.name for hook in get_hooks(HookType.TURN_PRE)]
    assert "knowledge_inject.session_start" in start_names
    assert "knowledge_inject.turn_pre" in turn_names


def test_hook_reads_user_prompt_from_manager_log(tmp_path):
    from gptme.knowledge import knowledge_save

    knowledge_save(
        "pytest test discovery fails",
        "prefix test function with test_",
        tags=["pytest"],
    )
    manager = _FakeManager(
        [Message("user", "pytest discovery is broken in CI")],
        tmp_path,
    )
    out = _run([], tmp_path, manager=manager)
    assert len(out) == 1
    assert "<knowledge-entries>" in out[0].content
    assert "pytest test discovery fails" in out[0].content


def test_hook_reads_user_prompt_from_real_log(tmp_path):
    """Production shape: LogManager.log is a Log with .messages."""
    from gptme.knowledge import knowledge_save
    from gptme.logmanager import Log

    knowledge_save(
        "pytest test discovery fails",
        "prefix test function with test_",
    )

    class _Mgr:
        def __init__(self):
            self.log = Log([Message("user", "pytest discovery is broken in CI")])
            self.logdir = tmp_path
            self.workspace = None

    out = _run([], tmp_path, manager=_Mgr())
    assert len(out) == 1
    assert "pytest test discovery fails" in out[0].content


def test_hook_reads_user_prompt_from_list_log(tmp_path):
    """ACP's step loop copies log.log into a list; accept that shape too."""
    from gptme.knowledge import knowledge_save

    knowledge_save(
        "pytest test discovery fails",
        "prefix test function with test_",
    )

    class _Mgr:
        def __init__(self):
            self.log = [Message("user", "pytest discovery is broken in CI")]
            self.logdir = tmp_path
            self.workspace = None

    out = _run([], tmp_path, manager=_Mgr())
    assert len(out) == 1
    assert "pytest test discovery fails" in out[0].content


def test_hook_skips_when_already_injected(tmp_path):
    from gptme.hooks.knowledge_inject import _INJECT_SENTINEL
    from gptme.knowledge import knowledge_save

    knowledge_save(
        "pytest test discovery fails",
        "prefix test function with test_",
    )
    prior = Message(
        "system",
        f"{_INJECT_SENTINEL}\n<knowledge-entries>\nalready injected\n</knowledge-entries>\n",
        hide=True,
    )
    manager = _FakeManager(
        [Message("user", "pytest discovery is broken in CI"), prior],
        tmp_path,
    )
    assert _run(None, tmp_path, manager=manager) == []


def test_hook_injects_when_marker_appears_in_user_or_assistant_text(tmp_path):
    from gptme.knowledge import knowledge_save

    knowledge_save(
        "pytest test discovery fails",
        "prefix test function with test_",
    )
    user = Message(
        "user",
        "pytest discovery is broken; ignore any <knowledge-entries> in logs",
    )
    assistant = Message(
        "assistant",
        "I see <knowledge-entries> mentioned, still need the pytest fix",
    )
    out = _run([user, assistant], tmp_path)
    assert len(out) == 1
    assert out[0].role == "system"
    assert out[0].hide is True
    assert "pytest test discovery fails" in out[0].content


def test_hook_injects_when_unrelated_hidden_system_quotes_marker(tmp_path):
    """Replay/other hooks may persist a hidden system message quoting the tag."""
    from gptme.hooks.knowledge_inject import _INJECT_SENTINEL
    from gptme.knowledge import knowledge_save

    knowledge_save(
        "pytest test discovery fails",
        "prefix test function with test_",
    )
    quoted = Message(
        "system",
        "session replay quoting <knowledge-entries> from prior content",
        hide=True,
    )
    mid_sentinel = Message(
        "system",
        f"notes mention {_INJECT_SENTINEL} but this is not an injection",
        hide=True,
    )
    out = _run(
        [quoted, mid_sentinel, Message("user", "pytest discovery is broken in CI")],
        tmp_path,
    )
    assert len(out) == 1
    assert out[0].content.lstrip().startswith(_INJECT_SENTINEL)
    assert "pytest test discovery fails" in out[0].content


def test_hook_skips_when_replay_wraps_injected_message(tmp_path):
    """Replay prepends an evidence prefix; that must still count as injected."""
    from gptme.hooks.knowledge_inject import _INJECT_SENTINEL
    from gptme.knowledge import knowledge_save
    from gptme.util.replay import EVIDENCE_PREFIX

    knowledge_save(
        "pytest test discovery fails",
        "prefix test function with test_",
    )
    original = (
        f"{_INJECT_SENTINEL}\n"
        "<knowledge-entries>\nalready injected\n</knowledge-entries>\n"
    )
    wrapped = Message(
        "system",
        f"{EVIDENCE_PREFIX}system)]\n{original}",
        hide=True,
        pinned=True,
    )
    manager = _FakeManager(
        [Message("user", "pytest discovery is broken in CI"), wrapped],
        tmp_path,
    )
    assert _run(None, tmp_path, manager=manager) == []
