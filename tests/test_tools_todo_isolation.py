from concurrent.futures import ThreadPoolExecutor
from contextvars import Context
from pathlib import Path
from threading import Barrier

import pytest

from gptme.logmanager import Log, LogManager
from gptme.message import Message
from gptme.tools import set_tools
from gptme.tools.todo import (
    _todoread,
    _todowrite,
    get_incomplete_todos_summary,
    has_incomplete_todos,
    replay_todo_on_session_start,
    todo,
)


def start(path: Path, messages: list[Message] | None = None) -> LogManager:
    set_tools([todo])
    manager = LogManager(log=messages or [], logdir=path, lock=False)
    list(replay_todo_on_session_start(path, path.parent, manager.log.messages))
    return manager


@pytest.mark.parametrize("copied", [False, True])
def test_fresh_conversations_are_isolated(tmp_path: Path, copied: bool) -> None:
    a = Context()
    a.run(start, tmp_path / "a")
    a.run(_todowrite, "add", "A-only plan")
    b = a.copy() if copied else Context()
    b.run(start, tmp_path / "b")
    assert "A-only plan" not in b.run(_todoread)
    assert not b.run(has_incomplete_todos)
    assert b.run(get_incomplete_todos_summary) == ""
    b.run(_todowrite, "clear")
    assert "A-only plan" in a.run(_todoread)
    assert a.run(has_incomplete_todos)


def test_interleaved_mutations_and_same_conversation_context(tmp_path: Path) -> None:
    a, b = Context(), Context()
    a.run(start, tmp_path / "a")
    b.run(start, tmp_path / "b")
    a.run(_todowrite, "add", "A")
    b.run(_todowrite, "add", "B")
    b.run(_todowrite, "add", "B second")
    b.run(_todowrite, "update", "1", "completed")
    b.run(_todowrite, "clear", "completed")
    assert "A" in a.run(get_incomplete_todos_summary)
    assert "1. 🔲 A" in a.run(_todoread)
    b.run(_todowrite, "update", "2", "Renamed B")
    b.run(_todowrite, "remove", "2")
    assert not b.run(has_incomplete_todos)
    same = a.copy()
    same.run(_todowrite, "update", "1", "paused")
    assert "⏸️ A" in a.run(_todoread)
    # Server tool workers load a new manager for the same conversation.
    same.run(LogManager, logdir=tmp_path / "a", lock=False)
    assert "⏸️ A" in same.run(_todoread)


def test_concurrent_conversations(tmp_path: Path) -> None:
    barrier = Barrier(2)

    def run(name: str) -> str:
        start(tmp_path / name)
        _todowrite("add", name)
        barrier.wait(timeout=5)
        result = _todoread()
        barrier.wait(timeout=5)
        _todowrite("clear")
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(run, "alpha")
        b = pool.submit(run, "bravo")
        assert "alpha" in a.result() and "bravo" not in a.result()
        assert "bravo" in b.result() and "alpha" not in b.result()


def test_replay_is_idempotent_and_targets_explicit_conversation(tmp_path: Path) -> None:
    a = Context()
    a.run(start, tmp_path / "a")
    a.run(_todowrite, "add", "Keep A")
    messages = [Message("assistant", '```todo write\nadd "Resumed B"\n```')]
    # CLI fires SESSION_START before creating B's manager: A is still current.
    for _ in range(2):
        a.run(
            lambda: list(
                replay_todo_on_session_start(tmp_path / "b", tmp_path, messages)
            )
        )
    assert "Keep A" in a.run(_todoread)
    assert "Resumed B" not in a.run(_todoread)
    b = Context()
    b.run(LogManager, log=messages, logdir=tmp_path / "b", lock=False)
    result = b.run(_todoread)
    assert "1. 🔲 Resumed B" in result
    assert "Summary: 1 total" in result


def test_resume_before_generation_and_cleanup(tmp_path: Path) -> None:
    from gptme.tools.todo import _todo_stores, clear_todo_state, ensure_todo_state

    a, b = Context(), Context()
    messages = [Message("assistant", '```todo write\nadd "Persisted A"\n```')]
    manager = a.run(start, tmp_path / "a", messages)
    manager.write()
    b.run(start, tmp_path / "b")
    b.run(_todowrite, "add", "Keep B")
    # Server SESSION_END supplies manager, not logdir; another context may be current.
    b.run(lambda: list(clear_todo_state(manager)))
    assert not any(key[0] == manager.logdir for key in _todo_stores)
    assert "Keep B" in b.run(_todoread)
    resumed = a.run(LogManager.load, manager.logdir, lock=False)
    # Resumed server conversations skip SESSION_START, so STEP_PRE restores them.
    a.run(lambda: list(ensure_todo_state(resumed)))
    assert "Persisted A" in a.run(_todoread)
    a.run(_todowrite, "add", "Live A")
    a.run(lambda: list(ensure_todo_state(resumed)))
    assert "Live A" in a.run(_todoread)
    assert "Summary: 2 total" in a.run(_todoread)
    # The pending assistant call is persisted before a fresh tool worker loads.
    resumed.append(Message("assistant", '```todo write\nadd "New A"\n```'))
    resumed.write()
    a.run(LogManager.load, manager.logdir, lock=False)
    a.run(_todowrite, "add", "New A")
    assert "Summary: 3 total" in a.run(_todoread)


def test_branch_and_compacted_view_restore(tmp_path: Path) -> None:
    from gptme.tools.todo import clear_todo_state, ensure_todo_state

    messages = [Message("assistant", '```todo write\nadd "Main plan"\n```')]
    manager = start(tmp_path / "conversation", messages)
    manager.branch("alternative")
    manager.log = Log([Message("assistant", '```todo write\nadd "Branch plan"\n```')])
    list(ensure_todo_state(manager))
    assert "Branch plan" in _todoread() and "Main plan" not in _todoread()
    manager.branch("main")
    assert "Main plan" in _todoread() and "Branch plan" not in _todoread()
    manager.preserve_lossless_log()
    manager.log = Log([Message("system", "Compacted summary without tool calls")])
    list(clear_todo_state(manager))
    list(ensure_todo_state(manager))
    assert "Main plan" in _todoread()


def test_unbound_context_has_no_shared_fallback() -> None:
    context = Context()
    assert not context.run(has_incomplete_todos)
    assert context.run(get_incomplete_todos_summary) == ""
    with pytest.raises(RuntimeError, match="active conversation"):
        context.run(_todowrite, "add", "Unowned")


def test_cli_resume_uses_loaded_history_after_empty_initial_messages(
    tmp_path: Path,
) -> None:
    from gptme.tools.todo import clear_todo_state, ensure_todo_state

    messages = [Message("assistant", '```todo write\nadd "Saved plan"\n```')]
    manager = start(tmp_path / "saved", messages)
    manager.write()
    list(clear_todo_state(manager))
    # chat() receives initial prompt messages, then loads existing history.
    list(
        replay_todo_on_session_start(
            manager.logdir, tmp_path, [Message("system", "Prompt")]
        )
    )
    resumed = LogManager.load(manager.logdir, lock=False)
    assert not has_incomplete_todos()  # A read must not suppress later restoration.
    list(ensure_todo_state(resumed))
    assert "Saved plan" in _todoread()


def test_reading_another_chat_does_not_redirect_following_todos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from types import SimpleNamespace

    from gptme.tools.base import ToolUse
    from gptme.tools.chats import tool as chats

    b_context = Context()
    b = b_context.run(start, tmp_path / "b", [Message("user", "B history")])
    b.write()
    b_context.run(_todowrite, "add", "Keep B")
    a = start(tmp_path / "a")
    _todowrite("add", "Keep A")
    set_tools([todo, *chats.as_function_subtoolspecs()])
    monkeypatch.setattr(
        "gptme.logmanager.get_conversation_by_id",
        lambda _: SimpleNamespace(path=b.logdir, name="B", id="b"),
    )
    list(ToolUse("chats.read_chat", None, None, kwargs={"id": "b"}).execute(log=a.log))
    assert "B history" in capsys.readouterr().out
    assert LogManager.get_current_log() is a
    assert "Keep A" in _todoread() and "Keep B" not in _todoread()
    _todowrite("clear")
    assert "Keep B" in b_context.run(_todoread)


@pytest.mark.parametrize("finish", ["exhaust", "close", "interrupt"])
def test_tool_restores_conversation_on_all_exit_paths(
    tmp_path: Path, finish: str
) -> None:
    from collections.abc import Generator

    from gptme.hooks import clear_hooks
    from gptme.tools.base import ToolSpec, ToolUse

    a = start(tmp_path / "a")
    _todowrite("add", "A plan")
    clear_hooks()

    def execute(code, args, kwargs) -> Generator[Message, None, None]:
        LogManager(logdir=tmp_path / "b", lock=False)
        yield Message("system", "Read B")
        if finish == "interrupt":
            raise KeyboardInterrupt

    set_tools([ToolSpec(name="reader", desc="Read another log", execute=execute)])
    execution = ToolUse("reader", None, None).execute(log=a.log)
    assert next(execution).content == "Read B"
    if finish == "close":
        execution.close()
    elif finish == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            list(execution)
    else:
        list(execution)
    assert LogManager.get_current_log() is a
    assert "A plan" in _todoread()
