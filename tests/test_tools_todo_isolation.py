from concurrent.futures import ThreadPoolExecutor
from contextvars import Context
from pathlib import Path
from threading import Barrier
from typing import Literal

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


@pytest.fixture(autouse=True)
def literal_history_format():
    from gptme.tools.base import get_tool_format, set_tool_format

    previous = get_tool_format()
    set_tool_format("tool")  # Native plus markdown histories used by these tests.
    try:
        yield
    finally:
        set_tool_format(previous)


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


@pytest.mark.parametrize("operation", ["update 1 completed", 'add "New plan"', "read"])
@pytest.mark.parametrize("tool_format", ["markdown", "xml", "tool"])
def test_direct_tool_resume_restores_before_pending_call(
    tmp_path: Path, operation: str, tool_format: Literal["markdown", "xml", "tool"]
) -> None:
    from gptme.tools.base import ToolUse, set_tool_format
    from gptme.tools.todo import clear_todo_state, ensure_todo_state

    set_tool_format(tool_format)
    saved = (
        ToolUse("todo", None, None, kwargs={"command": 'write add "Saved plan"'})
        if tool_format == "tool"
        else ToolUse("todo", ["write"], 'add "Saved plan"')
    )
    saved_content = saved.to_output(tool_format)
    if tool_format == "tool":
        saved_content = saved_content.replace("@todo:", "@todo(saved):", 1)
    messages = [Message("assistant", saved_content)]
    manager = start(tmp_path / "direct", messages)
    list(clear_todo_state(manager))
    pending = (
        ToolUse("todo", ["read"], "")
        if operation == "read"
        else ToolUse("todo", ["write"], operation)
    )
    if tool_format == "tool":
        command = "read" if operation == "read" else f"write {operation}"
        pending = ToolUse("todo", None, None, kwargs={"command": command})
    pending_content = pending.to_output(tool_format)
    if tool_format == "tool":
        pending = ToolUse("todo", None, None, kwargs=pending.kwargs, call_id="pending")
        pending_content = pending_content.replace("@todo:", "@todo(pending):", 1)
    manager.append(Message("assistant", pending_content))
    manager.write()
    resumed = LogManager.load(manager.logdir, lock=False)
    pending_message = next(
        msg for msg in reversed(resumed.log.messages) if msg.role == "assistant"
    )
    pending = list(ToolUse.iter_from_content(pending_message.content))[-1]
    result = list(pending.execute(log=resumed.log))
    assert all("not found" not in msg.content for msg in result)
    assert "Saved plan" in _todoread()
    if operation.startswith("add"):
        assert "Summary: 2 total" in _todoread()
        assert "2. 🔲 New plan" in _todoread()
    elif operation.startswith("update"):
        assert "1. ✅ Saved plan" in _todoread()
    else:
        assert any("Saved plan" in msg.content for msg in result)
    list(ensure_todo_state(resumed))
    assert "Saved plan" in _todoread()


@pytest.mark.parametrize("cleanup", ["remove", "inactive"])
def test_delayed_server_cleanup_preserves_reconnected_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup: str
) -> None:
    from datetime import datetime, timedelta, timezone

    pytest.importorskip("flask")
    from gptme.server.session_models import SessionManager
    from gptme.tools.todo import ensure_todo_state

    manager = start(tmp_path / "reconnect")
    _todowrite("add", "Existing plan")
    session = SessionManager.create_session(str(manager.logdir))
    session.last_activity = datetime.now(timezone.utc) - timedelta(hours=2)
    replacements = []

    def reconnect(*args, **kwargs):
        replacement = SessionManager.create_session(str(manager.logdir))
        replacements.append(replacement)
        list(ensure_todo_state(manager))
        _todowrite("add", "Replacement plan")
        return manager

    monkeypatch.setattr(LogManager, "load", reconnect)
    try:
        if cleanup == "remove":
            SessionManager.remove_session(session.id)
        else:
            SessionManager.clean_inactive_sessions()
        assert replacements
        assert "Existing plan" in _todoread()
        assert "Replacement plan" in _todoread()
    finally:
        # Do not trigger another SESSION_END through the reconnect stub.
        with SessionManager._lock:
            for replacement in replacements:
                SessionManager._sessions.pop(replacement.id, None)
            SessionManager._conversation_sessions.pop(str(manager.logdir), None)


def test_server_evicts_todos_only_after_last_session_and_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("flask")
    from gptme.server.session_models import SessionManager
    from gptme.tools.todo import _todo_stores

    manager = start(tmp_path / "idle")
    _todowrite("add", "Keep until idle")
    conversation = str(manager.logdir)
    session = SessionManager.create_session(conversation)
    monkeypatch.setattr(LogManager, "load", lambda *a, **kw: manager)
    SessionManager.start_command(conversation)
    try:
        SessionManager.remove_session(session.id)
        assert "Keep until idle" in _todoread()
    finally:
        SessionManager.finish_command(conversation)
    assert not any(key[0] == manager.logdir.resolve() for key in _todo_stores)


@pytest.mark.parametrize("call_id", [None, "reader-call"])
def test_tool_callbacks_and_yield_consumers_keep_caller(
    tmp_path: Path, call_id: str | None
) -> None:
    from collections.abc import Generator

    from gptme.hooks import HookType, clear_hooks, register_hook
    from gptme.tools.base import ToolSpec, ToolUse

    b_context = Context()
    b_context.run(start, tmp_path / "b")
    b_context.run(_todowrite, "add", "Keep B")
    a = start(tmp_path / "a")
    _todowrite("add", "A plan")
    clear_hooks()

    def execute(code, args, kwargs) -> Generator[Message, None, None]:
        LogManager(logdir=tmp_path / "b", lock=False)
        yield Message("system", "Read B")

    def post_hook(data):
        assert LogManager.get_current_log() is a
        yield from ()

    set_tools([ToolSpec(name="reader", desc="Read another log", execute=execute)])
    register_hook("check_owner", HookType.TOOL_EXECUTE_POST, post_hook)

    def on_result(msg: Message) -> None:
        assert LogManager.get_current_log() is a
        _todowrite("clear")

    execution = ToolUse("reader", None, None, call_id=call_id).execute(
        log=a.log, on_result_message=on_result
    )
    for _ in execution:
        assert LogManager.get_current_log() is a
    assert not has_incomplete_todos()
    assert "Keep B" in b_context.run(_todoread)


def test_start_replay_respects_explicit_branch(tmp_path: Path) -> None:
    manager = start(tmp_path / "branches")
    _todowrite("add", "Keep main")
    messages = [Message("assistant", '```todo write\nadd "Alternative"\n```')]
    list(
        replay_todo_on_session_start(
            manager.logdir, tmp_path, messages, branch="alternative"
        )
    )
    assert "Keep main" in _todoread()
    manager.branch("alternative")
    assert "Alternative" in _todoread()


@pytest.mark.parametrize("call_id", [None, "reader-call"])
@pytest.mark.parametrize("finish", ["exhaust", "close", "interrupt"])
def test_tool_execution_preserves_interleaved_consumer_owner(
    tmp_path: Path, call_id: str | None, finish: str
) -> None:
    from gptme.hooks import HookType, clear_hooks, register_hook
    from gptme.tools.base import ToolSpec, ToolUse

    a = start(tmp_path / "a")
    _todowrite("add", "A plan")
    clear_hooks()
    owners = []

    def execute(code, args, kwargs):
        try:
            yield Message("system", "First")
            assert LogManager.get_current_log() is a
            if finish == "interrupt":
                raise KeyboardInterrupt
            yield Message("system", "Second")
        finally:
            owners.append(LogManager.get_current_log())

    def post_hook(data):
        assert LogManager.get_current_log() is a
        yield from ()

    set_tools([ToolSpec(name="reader", desc="Stream results", execute=execute)])
    register_hook("check_owner", HookType.TOOL_EXECUTE_POST, post_hook)
    execution = ToolUse("reader", None, None, call_id=call_id).execute(log=a.log)
    # Native calls buffer the tool generator before yielding its first result,
    # so their interruption happens before any consumer can switch owners.
    if call_id and finish == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            list(execution)
        assert LogManager.get_current_log() is a
        return
    assert next(execution).content == "First"
    b = LogManager(logdir=tmp_path / "b", lock=False)
    _todowrite("add", "B plan")
    if finish == "close":
        execution.close()
    elif finish == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            list(execution)
    else:
        list(execution)
    assert owners == [a]
    assert LogManager.get_current_log() is b
    _todowrite("clear")
    assert not has_incomplete_todos()
    with LogManager.preserve_current():
        from gptme.logmanager import _current_log_var

        _current_log_var.set(a)
        assert "A plan" in _todoread()


@pytest.mark.parametrize("call_id", [None, "reader-call"])
@pytest.mark.parametrize("finish", ["exhaust", "close", "interrupt"])
def test_tool_generator_keeps_internal_owner_between_yields(
    tmp_path: Path, call_id: str | None, finish: str
) -> None:
    from collections.abc import Generator

    from gptme.hooks import clear_hooks
    from gptme.tools.base import ToolSpec, ToolUse

    a = start(tmp_path / "a")
    _todowrite("add", "A plan")
    clear_hooks()
    internal_owners = []

    def execute(code, args, kwargs) -> Generator[Message, None, None]:
        b = LogManager(logdir=tmp_path / "b", lock=False)
        try:
            yield Message("system", "Read B")
            internal_owners.append(LogManager.get_current_log())
            _todowrite("add", "B plan")
            if finish == "interrupt":
                raise KeyboardInterrupt
        finally:
            internal_owners.append(LogManager.get_current_log())
            assert LogManager.get_current_log() is b
            _todowrite("add", "B cleanup")

    set_tools([ToolSpec(name="reader", desc="Read another log", execute=execute)])
    execution = ToolUse("reader", None, None, call_id=call_id).execute(log=a.log)
    if finish == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            list(execution)
    else:
        assert next(execution).content == "Read B"
        assert LogManager.get_current_log() is a
        if finish == "close":
            execution.close()
        else:
            list(execution)
    assert internal_owners
    assert LogManager.get_current_log() is a
    assert "A plan" in _todoread()
    assert "B plan" not in _todoread()
    assert "B cleanup" not in _todoread()
    b_context = Context()
    b_context.run(LogManager, logdir=tmp_path / "b", lock=False)
    assert "B cleanup" in b_context.run(_todoread)


@pytest.mark.parametrize("tool_format", ["markdown", "xml", "tool"])
def test_direct_resume_preserves_earlier_calls_in_pending_message(
    tmp_path: Path, tool_format: Literal["markdown", "xml", "tool"]
) -> None:
    from gptme.tools.base import ToolUse, set_tool_format
    from gptme.tools.todo import clear_todo_state

    set_tool_format(tool_format)

    def call(command: str, call_id: str) -> ToolUse:
        if tool_format == "tool":
            return ToolUse(
                "todo", None, None, kwargs={"command": command}, call_id=call_id
            )
        return ToolUse("todo", ["write"], command.removeprefix("write "))

    add = call('write add "Saved task"', "add")
    update = call("write update 1 completed", "update")
    later = call('write add "Not yet executed"', "later")
    content = "\n\n".join(
        t.to_output(tool_format).replace("@todo:", f"@todo({t.call_id}):", 1)
        if tool_format == "tool"
        else t.to_output(tool_format)
        for t in [add, update, later]
    )
    manager = start(tmp_path / "partial")
    manager.append(Message("assistant", content))
    add = list(ToolUse.iter_from_content(content))[0]
    results = list(add.execute(log=manager.log))
    for msg in results:
        manager.append(msg)
    manager.write()
    list(clear_todo_state(manager))
    resumed = LogManager.load(manager.logdir, lock=False)
    pending_message = next(
        msg for msg in reversed(resumed.log.messages) if msg.role == "assistant"
    )
    update = list(ToolUse.iter_from_content(pending_message.content))[1]
    result = list(update.execute(log=resumed.log))
    assert all("not found" not in msg.content for msg in result)
    assert "1. ✅ Saved task" in _todoread()
    assert "Summary: 1 total" in _todoread()
    assert "Not yet executed" not in _todoread()


def test_restore_aborts_malformed_call_but_replays_next_call(tmp_path: Path) -> None:
    from gptme.tools.base import ToolUse
    from gptme.tools.todo import _restore_todos, execute_todo

    start(tmp_path / "malformed")
    body = 'add "First"\nadd "unterminated\nadd "Never executed"'
    with pytest.raises(ValueError, match="No closing quotation"):
        list(execute_todo(body, ["write"], None))
    assert "First" in _todoread()
    assert "Never executed" not in _todoread()
    messages = [
        Message("assistant", ToolUse("todo", ["write"], body).to_output("markdown")),
        Message("assistant", '```todo write\nadd "Next call"\n```'),
    ]
    restored, _ = _restore_todos(messages)
    assert {key: value["text"] for key, value in restored.items()} == {
        "1": "First",
        "2": "Next call",
    }


@pytest.mark.parametrize("lxml", [False, True])
def test_resume_repeated_xml_calls_uses_call_occurrence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lxml: bool
) -> None:
    import gptme.tools.base as base
    from gptme.tools.todo import clear_todo_state

    if lxml and not base._LXML_AVAILABLE:
        pytest.skip("lxml not installed")
    monkeypatch.setattr(base, "_LXML_AVAILABLE", lxml)
    base.set_tool_format("xml")
    content = "\n\n".join(
        base.ToolUse("todo", ["write"], 'add "Task"').to_output("xml") for _ in range(2)
    )
    manager = start(tmp_path / "repeated")
    manager.append(Message("assistant", content))
    calls = list(base.ToolUse.iter_from_content(content))
    for result in calls[0].execute(log=manager.log):
        manager.append(result)
    manager.write()
    list(clear_todo_state(manager))
    resumed = LogManager.load(manager.logdir, lock=False)
    pending_message = next(
        msg for msg in reversed(resumed.log.messages) if msg.role == "assistant"
    )
    calls = list(base.ToolUse.iter_from_content(pending_message.content))
    list(calls[1].execute(log=resumed.log))
    assert "Summary: 2 total" in _todoread()
    assert "2. 🔲 Task" in _todoread()


@pytest.mark.parametrize("tool_format", ["markdown", "xml", "tool"])
def test_independent_direct_call_keeps_identical_completed_history(
    tmp_path: Path, tool_format: Literal["markdown", "xml", "tool"]
) -> None:
    from gptme.tools.base import ToolUse, set_tool_format
    from gptme.tools.todo import clear_todo_state

    set_tool_format(tool_format)
    direct = ToolUse("todo", ["write"], 'add "Task"')
    saved = ToolUse("todo", ["write"], 'add "Task"')
    if tool_format == "tool":
        saved = ToolUse(
            "todo", None, None, kwargs={"command": 'write add "Task"'}, call_id="saved"
        )
        direct = ToolUse("todo", None, None, kwargs={"command": 'write add "Task"'})
    manager = start(tmp_path / "independent")
    saved_content = saved.to_output(tool_format)
    if tool_format == "tool":
        saved_content = saved_content.replace("@todo:", "@todo(saved):", 1)
    manager.append(Message("assistant", saved_content))
    saved = list(ToolUse.iter_from_content(saved_content))[0]
    for result in saved.execute(log=manager.log):
        manager.append(result)
    manager.write()
    list(clear_todo_state(manager))
    resumed = LogManager.load(manager.logdir, lock=False)
    list(direct.execute(log=resumed.log))
    assert "1. 🔲 Task" in _todoread()
    assert "2. 🔲 Task" in _todoread()
    assert "Summary: 2 total" in _todoread()


@pytest.mark.parametrize("active_format", ["markdown", "xml", "tool"])
def test_restore_history_across_tool_format_changes(
    tmp_path: Path, active_format: Literal["markdown", "xml", "tool"]
) -> None:
    from gptme.tools.base import ToolUse, get_tool_format, set_tool_format
    from gptme.tools.todo import clear_todo_state, ensure_todo_state

    messages = []
    formats: tuple[Literal["markdown", "xml", "tool"], ...] = (
        "markdown",
        "xml",
        "tool",
    )
    for tool_format in formats:
        call = ToolUse("todo", ["write"], f'add "Saved {tool_format}"')
        if tool_format == "tool":
            call = ToolUse(
                "todo",
                None,
                None,
                kwargs={"command": 'write add "Saved tool"'},
                call_id="saved-native",
            )
        content = call.to_output(tool_format)
        if tool_format == "tool":
            content = content.replace("@todo:", "@todo(saved-native):", 1)
        messages.append(Message("assistant", content))
    manager = start(tmp_path / "mixed")
    for msg in messages:
        manager.append(msg)
    list(clear_todo_state(manager))
    set_tool_format(active_format)
    list(ensure_todo_state(manager))
    assert get_tool_format() == active_format
    assert "1. 🔲 Saved markdown" in _todoread()
    assert "2. 🔲 Saved xml" in _todoread()
    assert "3. 🔲 Saved tool" in _todoread()
    assert "Summary: 3 total" in _todoread()


@pytest.mark.parametrize("tool_format", ["markdown", "tool"])
@pytest.mark.parametrize("wrapper", ["tool-use", "function_calls"])
@pytest.mark.parametrize("closed", [False, True])
def test_restore_ignores_ordinary_xml_mentions(
    tmp_path: Path,
    tool_format: Literal["markdown", "tool"],
    wrapper: str,
    closed: bool,
) -> None:
    from gptme.tools.base import ToolUse
    from gptme.tools.todo import clear_todo_state, ensure_todo_state

    mention = f"The XML format uses <{wrapper}>"
    if closed:
        mention += f"</{wrapper}>"
    call = (
        ToolUse("todo", ["write"], 'add "Saved task"')
        if tool_format == "markdown"
        else ToolUse(
            "todo",
            None,
            None,
            kwargs={"command": 'write add "Saved task"'},
            call_id="saved",
        )
    )
    content = (
        mention
        + "\n"
        + call.to_output(tool_format).replace("@todo:", "@todo(saved):", 1)
    )
    manager = start(tmp_path / "xml-mention", [Message("assistant", content)])
    list(clear_todo_state(manager))
    list(ensure_todo_state(manager))
    assert "1. 🔲 Saved task" in _todoread()
    assert "Summary: 1 total" in _todoread()


@pytest.mark.parametrize("wrapper", ["tool-use", "function_calls"])
@pytest.mark.parametrize("lxml", [False, True])
def test_restore_xml_after_fenced_wrapper(
    tmp_path: Path, wrapper: str, lxml: bool, monkeypatch
) -> None:
    from gptme.tools import base
    from gptme.tools.todo import clear_todo_state, ensure_todo_state

    if lxml and not base._LXML_AVAILABLE:
        pytest.skip("lxml is not installed")
    monkeypatch.setattr(base, "_LXML_AVAILABLE", lxml)
    call = (
        '<todo args="write">add "Saved task"</todo>'
        if wrapper == "tool-use"
        else '<invoke name="todo" args="write">add "Saved task"</invoke>'
    )
    content = f"```xml\n<{wrapper}></{wrapper}>\n```\n<{wrapper}>{call}</{wrapper}>"
    manager = start(tmp_path / "xml-after-example", [Message("assistant", content)])
    list(clear_todo_state(manager))
    list(ensure_todo_state(manager))
    assert "1. 🔲 Saved task" in _todoread()
    assert "Summary: 1 total" in _todoread()


@pytest.mark.parametrize("parsed", [False, True])
def test_fresh_direct_call_does_not_own_historical_message(
    tmp_path: Path, parsed: bool
) -> None:
    from gptme.tools.base import ToolUse
    from gptme.tools.todo import clear_todo_state

    content = '```todo write\nadd "Task"\n```'
    manager = start(tmp_path / "fresh", [Message("assistant", content)])
    list(clear_todo_state(manager))
    fresh_content = content.encode().decode()
    assert fresh_content == content and fresh_content is not content
    call = (
        list(ToolUse.iter_from_content(fresh_content))[0]
        if parsed
        else ToolUse("todo", ["write"], 'add "Task"')
    )
    list(call.execute(log=manager.log))
    assert "2. 🔲 Task" in _todoread()


@pytest.mark.parametrize("outer_format", ["markdown", "xml"])
def test_restore_does_not_execute_nested_format_examples(
    tmp_path: Path, outer_format: Literal["markdown", "xml"]
) -> None:
    from gptme.tools.base import ToolUse
    from gptme.tools.shell import tool as shell
    from gptme.tools.todo import _restore_todos

    start(tmp_path / "examples")
    set_tools([todo, shell])
    inner_format: Literal["markdown", "xml"] = (
        "xml" if outer_format == "markdown" else "markdown"
    )
    example = ToolUse("todo", ["write"], 'add "Example"').to_output(inner_format)
    content = ToolUse("shell", [], f"cat <<'EOF'\n{example}\nEOF").to_output(
        outer_format
    )
    restored, _ = _restore_todos([Message("assistant", content)])
    assert restored == {}


@pytest.mark.parametrize("lossless", [False, True])
@pytest.mark.parametrize("edited", [False, True])
@pytest.mark.parametrize("tool_format", ["markdown", "xml", "tool"])
def test_server_pending_call_restores_after_worker_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_format: Literal["markdown", "xml", "tool"],
    lossless: bool,
    edited: bool,
) -> None:
    from dataclasses import replace

    from gptme.config import ChatConfig
    from gptme.tools.base import ToolUse, set_tool_format
    from gptme.tools.todo import clear_todo_state

    pytest.importorskip("flask")
    from gptme.server.session_models import SessionManager, ToolExecution
    from gptme.server.session_step import start_tool_execution

    set_tool_format(tool_format)
    call = ToolUse("todo", ["write"], 'add "Pending"')
    if tool_format == "tool":
        call = ToolUse("todo", None, None, kwargs={"command": 'write add "Pending"'})
    content = call.to_output(tool_format).replace("@todo:", "@todo(pending):", 1)
    message = Message("assistant", content)
    manager = start(tmp_path / "worker", [message])
    manager.write()
    if lossless:
        manager.preserve_lossless_log()
    list(clear_todo_state(manager))
    session = SessionManager.create_session(str(manager.logdir))
    session.pending_tools["pending"] = ToolExecution(
        tool_id="pending",
        tooluse=list(ToolUse.iter_from_content(message.content))[0],
        auto_confirm=False,
        assistant_msg_timestamp=message.timestamp,
    )
    monkeypatch.setattr(
        "gptme.server.session_step.prepare_execution_environment", lambda **kw: None
    )
    monkeypatch.setattr(
        "gptme.server.session_step._start_step_thread", lambda *a, **kw: None
    )
    try:
        thread = start_tool_execution(
            str(manager.logdir),
            session,
            "pending",
            (
                replace(
                    session.pending_tools["pending"].tooluse,
                    kwargs={"command": 'write add "Edited"'},
                )
                if tool_format == "tool"
                else replace(
                    session.pending_tools["pending"].tooluse, content='add "Edited"'
                )
            )
            if edited
            else None,
            "mock/model",
            ChatConfig(model="mock/model"),
        )
        thread.join(timeout=10)
        assert not thread.is_alive()
        resumed = LogManager.load(manager.logdir, lock=False)
        assert resumed
        assert "Summary: 1 total" in _todoread()
        assert ("Edited" if edited else "Pending") in _todoread()
    finally:
        SessionManager.remove_session(session.id)


@pytest.mark.parametrize("view", [False, True])
@pytest.mark.parametrize("edited", [False, True])
@pytest.mark.parametrize("tool_format", ["markdown", "xml", "tool"])
def test_direct_pending_call_from_separately_loaded_history(
    tmp_path: Path,
    tool_format: Literal["markdown", "xml", "tool"],
    view: bool,
    edited: bool,
) -> None:
    from dataclasses import replace

    from gptme.tools.base import ToolUse, set_tool_format
    from gptme.tools.todo import clear_todo_state

    set_tool_format(tool_format)

    def call(text: str, call_id: str) -> str:
        tooluse = (
            ToolUse("todo", None, None, kwargs={"command": f'write add "{text}"'})
            if tool_format == "tool"
            else ToolUse("todo", ["write"], f'add "{text}"')
        )
        return tooluse.to_output(tool_format).replace("@todo:", f"@todo({call_id}):", 1)

    older = Message("assistant", call("Older task", "older"))
    pending = Message(
        "assistant",
        "\n\n".join(
            [
                call("Earlier call", "earlier"),
                call("Pending", "pending"),
                call("Later call", "later"),
            ]
        ),
    )
    manager = start(tmp_path / "separate-history", [older, pending])
    manager.write()
    if view:
        manager.create_view("compacted-001", [Message("system", "Summary"), pending])
        manager.switch_view("compacted-001")
    else:
        manager.preserve_lossless_log()
    list(clear_todo_state(manager))
    resumed = LogManager.load(manager.logdir, lock=False)
    pending_message = resumed.log.messages[-1]
    master_message = resumed.master_log.messages[-1]
    assert pending_message.content == master_message.content
    assert pending_message.content is not master_message.content
    tooluse = list(ToolUse.iter_from_content(pending_message.content))[1]
    if edited:
        tooluse = (
            replace(tooluse, kwargs={"command": 'write add "Edited"'})
            if tool_format == "tool"
            else replace(tooluse, content='add "Edited"')
        )
    list(tooluse.execute(log=resumed.log))
    assert "1. 🔲 Older task" in _todoread()
    assert "2. 🔲 Earlier call" in _todoread()
    assert f"3. 🔲 {'Edited' if edited else 'Pending'}" in _todoread()
    assert "Later call" not in _todoread()
    assert "Summary: 3 total" in _todoread()
