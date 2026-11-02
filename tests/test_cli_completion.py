"""Offline CLI idle completion delivery through the chat loop and prompt_toolkit."""

import contextvars
import threading
import time
from unittest.mock import patch

import pytest

from gptme.hooks import clear_hooks
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tools import clear_tools, init_tools
from gptme.tools.subagent.hooks import (
    notify_completion,
    notify_progress,
    take_queued_completions,
)
from gptme.tools.subagent.types import Status, _completion_queue, _progress_queue

chat = pytest.importorskip("gptme.chat")
prompt = pytest.importorskip("gptme.util.prompt")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_USER_CONFIG", str(tmp_path / "no-config.toml"))
    for queue in (_completion_queue, _progress_queue):
        with queue.mutex:
            queue.queue.clear()
    clear_hooks()
    clear_tools()
    init_tools(["subagent", "watch"], include_mcp=False)
    idle_token = chat._idle_autowake_blocked.set(False)
    prompt.reset_idle_prompt_state()
    monkeypatch.setattr(prompt, "_prompt_session", None)
    monkeypatch.setattr(chat, "print_bell", lambda: None)
    monkeypatch.setattr(chat, "flush_stdin", lambda: None)
    with patch("gptme.tools.subagent.hooks._notify_server", return_value=False):
        yield
    chat._idle_autowake_blocked.reset(idle_token)
    prompt.reset_idle_prompt_state()
    clear_hooks()
    clear_tools()
    for queue in (_completion_queue, _progress_queue):
        with queue.mutex:
            queue.queue.clear()
    from gptme.tools import watch

    with watch._pending_lock:
        watch._pending_deliveries.clear()
    from gptme.util import notification_ready

    notification_ready._listeners.clear()


def manager_at(tmp_path, name="parent"):
    return LogManager(
        [Message("user", "delegate"), Message("assistant", "Final parent text.")],
        logdir=tmp_path / name,
        lock=False,
    )


class InputController:
    """Stand in for prompt_toolkit: block until EOF, typed text, or idle wake."""

    def __init__(self):
        self.release = threading.Event()
        self.pause_wake = threading.Event()
        self.input_seq = 0
        self._reply: list[str | None] = [None]
        self._interrupt = False
        self._lock = threading.Lock()

    def submit(self, text: str) -> None:
        self._reply[0] = text

    def interrupt(self) -> None:
        self._interrupt = True

    def wait_for_input(self, after_seq: int = 0, timeout: float = 5) -> int:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                seq = self.input_seq
            if seq > after_seq:
                return seq
            time.sleep(0.01)
        raise AssertionError(
            f"timed out waiting for CLI input (seq={self.input_seq} after={after_seq})"
        )

    def get_input(self, _prompt: str) -> str | object:
        with self._lock:
            self.input_seq += 1
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.release.is_set():
                raise EOFError
            if self._interrupt:
                self._interrupt = False
                raise KeyboardInterrupt
            if self._reply[0] is not None:
                text = self._reply[0]
                self._reply[0] = None
                return text
            if not self.pause_wake.is_set():
                with prompt._idle_lock:
                    pending = prompt._pending_wake
                    armed = prompt._idle_armed
                if pending and armed:
                    with prompt._idle_lock:
                        prompt._pending_wake = False
                    return prompt.NOTIFICATION_WAKE
            time.sleep(0.01)
        raise TimeoutError("CLI input wait expired")


def run_loop(manager, errors):
    try:
        chat._run_chat_loop(manager, [], False, interactive=True, logdir=manager.logdir)
    except BaseException as exc:
        errors.append(exc)


def wait_until(predicate, timeout: float = 5, *, message: str = "condition") -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {message}")


@pytest.mark.parametrize("when", ["queued", "idle"])
def test_completion_wakes_once(tmp_path, monkeypatch, when):
    manager = manager_at(tmp_path)
    processed: list[int] = []
    errors: list[BaseException] = []
    controller = InputController()

    def fake_process(*_a, **_kw):
        processed.append(1)
        manager.append(Message("assistant", "follow-up"))
        return False

    monkeypatch.setattr(prompt, "get_input", controller.get_input)
    monkeypatch.setattr(chat, "_process_message_conversation", fake_process)
    if when == "queued":
        notify_completion("child", "success", "done", parent_logdir=manager.logdir)
    thread = threading.Thread(
        target=contextvars.copy_context().run, args=(run_loop, manager, errors)
    )
    thread.start()
    try:
        seq = 0
        if when == "idle":
            seq = controller.wait_for_input()
            notify_completion("child", "success", "done", parent_logdir=manager.logdir)
        controller.wait_for_input(after_seq=seq)
        wait_until(lambda: processed == [1], message="follow-up generation")
        assert sum("Subagent 'child'" in msg.content for msg in manager.log) == 1
        assert not take_queued_completions(manager.logdir)
    finally:
        controller.release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not errors
    from gptme.util import notification_ready

    assert manager.logdir.resolve() not in notification_ready._listeners


def test_progress_is_notification_only(tmp_path, monkeypatch):
    manager = manager_at(tmp_path)
    processed: list[int] = []
    errors: list[BaseException] = []
    controller = InputController()
    monkeypatch.setattr(prompt, "get_input", controller.get_input)
    monkeypatch.setattr(
        chat, "_process_message_conversation", lambda *_a, **_kw: processed.append(1)
    )
    thread = threading.Thread(
        target=contextvars.copy_context().run, args=(run_loop, manager, errors)
    )
    thread.start()
    try:
        seq = controller.wait_for_input()
        notify_progress("child", "working", parent_logdir=manager.logdir)
        controller.wait_for_input(after_seq=seq)
        assert not processed
        assert sum("working" in msg.content for msg in manager.log) == 1
    finally:
        controller.release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not errors


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("kind", ["child", "watch"])
def test_policy_status_burst_and_foreign_parent(tmp_path, monkeypatch, enabled, kind):
    from gptme.config import ChatConfig, get_config
    from gptme.tools import watch

    manager = manager_at(tmp_path)
    foreign = tmp_path / "foreign"
    get_config().chat = ChatConfig(watch_autowake=enabled)
    processed: list[int] = []
    errors: list[BaseException] = []
    controller = InputController()

    def fake_process(*_a, **_kw):
        processed.append(1)
        manager.append(Message("assistant", "done"))
        return False

    monkeypatch.setattr(prompt, "get_input", controller.get_input)
    monkeypatch.setattr(chat, "_process_message_conversation", fake_process)
    thread = threading.Thread(
        target=contextvars.copy_context().run, args=(run_loop, manager, errors)
    )
    thread.start()
    try:
        seq = controller.wait_for_input()
        controller.pause_wake.set()
        notify_completion("foreign", "success", "foreign text", parent_logdir=foreign)
        expected_system = 6 if kind == "child" else 3
        if kind == "child":
            statuses: tuple[Status, ...] = (
                "success",
                "failure",
                "timeout",
                "budget_exceeded",
                "cancelled",
                "clarification_needed",
            )
            for status in statuses:
                notify_completion(
                    status, status, "result", parent_logdir=manager.logdir
                )
        else:
            for i in range(3):
                watch._deliver(
                    watch.Watch(f"w{i}", "timer", "test", 0, logdir=manager.logdir),
                    "ready",
                )
        controller.pause_wake.clear()
        wait_until(
            lambda: (
                len([m for m in manager.log if m.role == "system"]) == expected_system
            ),
            message="owned notifications",
        )
        if enabled:
            wait_until(lambda: processed == [1], message="autowake generation")
            controller.wait_for_input(after_seq=seq)
        else:
            controller.wait_for_input(after_seq=seq)
            assert not processed
        assert len([m for m in manager.log if m.role == "system"]) == expected_system
        assert not any("foreign text" in m.content for m in manager.log)
        assert [item[0] for item in take_queued_completions(foreign)] == ["foreign"]
    finally:
        controller.release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not errors


def test_originating_branch_deferred_until_return(tmp_path, monkeypatch):
    manager = manager_at(tmp_path)
    manager.branch("other")
    processed: list[int] = []
    errors: list[BaseException] = []
    controller = InputController()

    def fake_process(*_a, **_kw):
        processed.append(1)
        manager.append(Message("assistant", "done"))
        return False

    monkeypatch.setattr(prompt, "get_input", controller.get_input)
    monkeypatch.setattr(chat, "_process_message_conversation", fake_process)
    thread = threading.Thread(
        target=contextvars.copy_context().run, args=(run_loop, manager, errors)
    )
    thread.start()
    try:
        seq = controller.wait_for_input()
        notify_completion(
            "child",
            "success",
            "origin result",
            parent_logdir=manager.logdir,
            parent_branch="main",
        )
        seq = controller.wait_for_input(after_seq=seq)
        assert not processed
        assert not any("origin result" in m.content for m in manager.log)
        manager.branch("main")
        prompt.request_idle_wake()
        controller.wait_for_input(after_seq=seq)
        wait_until(lambda: processed == [1], message="branch autowake")
        assert sum("origin result" in m.content for m in manager.log) == 1
    finally:
        controller.release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not errors


def test_interrupt_defers_autowake_until_user_turn(tmp_path, monkeypatch):
    manager = manager_at(tmp_path)
    calls: list[list[str]] = []
    errors: list[BaseException] = []
    controller = InputController()

    def fake_step(log, *_a, **_kw):
        calls.append([m.content for m in log])
        if len(calls) == 1:
            raise KeyboardInterrupt
        yield Message("assistant", "resumed")

    def fake_cmd(msg, manager):
        if "help-cmd" not in msg.content:
            return False
        manager.undo(1, quiet=True)
        return True

    monkeypatch.setattr(prompt, "get_input", controller.get_input)
    monkeypatch.setattr(chat, "step", fake_step)
    monkeypatch.setattr(chat, "execute_cmd", fake_cmd)
    thread = threading.Thread(
        target=contextvars.copy_context().run, args=(run_loop, manager, errors)
    )
    thread.start()
    try:
        seq = controller.wait_for_input()
        notify_completion("child", "success", "first", parent_logdir=manager.logdir)
        seq = controller.wait_for_input(after_seq=seq)
        wait_until(lambda: len(calls) == 1, message="interrupted first generation")
        notify_completion(
            "interrupted", "success", "no forced restart", parent_logdir=manager.logdir
        )
        seq = controller.wait_for_input(after_seq=seq)
        assert len(calls) == 1
        assert sum("interrupted" in m.content for m in manager.log) == 1
        controller.submit("help-cmd")
        seq = controller.wait_for_input(after_seq=seq)
        notify_completion(
            "after-help", "success", "still paused", parent_logdir=manager.logdir
        )
        seq = controller.wait_for_input(after_seq=seq)
        assert len(calls) == 1
        controller.submit("resume")
        seq = controller.wait_for_input(after_seq=seq)
        wait_until(lambda: len(calls) == 2, message="user resume generation")
        notify_completion(
            "resumed", "success", "continue again", parent_logdir=manager.logdir
        )
        controller.wait_for_input(after_seq=seq)
        wait_until(lambda: len(calls) == 3, message="post-resume autowake")
    finally:
        controller.release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not errors


def test_idle_ctrl_c_blocks_autowake(tmp_path, monkeypatch):
    manager = manager_at(tmp_path)
    processed: list[int] = []
    errors: list[BaseException] = []
    controller = InputController()

    def fake_process(*_a, **_kw):
        processed.append(1)
        manager.append(Message("assistant", "done"))
        return False

    # Exercise the real get_input exception handling, not a replacement that
    # bypasses its conversion of prompt_toolkit's KeyboardInterrupt.
    session = prompt.get_prompt_session()
    monkeypatch.setattr(session, "prompt", lambda *_a, **_kw: controller.get_input(""))
    monkeypatch.setattr(chat, "_process_message_conversation", fake_process)
    thread = threading.Thread(
        target=contextvars.copy_context().run, args=(run_loop, manager, errors)
    )
    thread.start()
    try:
        seq = controller.wait_for_input()
        controller.interrupt()
        seq = controller.wait_for_input(after_seq=seq)
        notify_completion("child", "success", "paused", parent_logdir=manager.logdir)
        controller.wait_for_input(after_seq=seq)
        assert not processed
        assert sum("paused" in msg.content for msg in manager.log) == 1
    finally:
        controller.release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not errors


def test_force_prompt_delivers_without_autowake(tmp_path, monkeypatch):
    manager = manager_at(tmp_path)
    errors: list[BaseException] = []
    controller = InputController()
    result: list[object] = []
    monkeypatch.setattr(prompt, "get_input", controller.get_input)

    def run() -> None:
        try:
            result.append(
                chat._get_user_input(
                    manager.log,
                    None,
                    force_prompt=True,
                    manager=manager,
                )
            )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=contextvars.copy_context().run, args=(run,))
    thread.start()
    try:
        seq = controller.wait_for_input()
        notify_completion("child", "success", "held", parent_logdir=manager.logdir)
        controller.wait_for_input(after_seq=seq)
        assert result == []
        assert sum("held" in msg.content for msg in manager.log) == 1
    finally:
        controller.release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not errors
    assert result == [None]


def test_include_paths_runs_unarmed(tmp_path, monkeypatch):
    manager = manager_at(tmp_path)
    controller = InputController()
    seen: dict[str, bool] = {}
    errors: list[BaseException] = []

    def fake_include(msg, workspace=None, **_kw):
        with prompt._idle_lock:
            seen["armed"] = prompt._idle_armed
        return msg

    monkeypatch.setattr(prompt, "get_input", controller.get_input)
    monkeypatch.setattr(chat, "include_paths", fake_include)

    def run() -> None:
        try:
            chat._get_user_input(manager.log, None, manager=manager)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=contextvars.copy_context().run, args=(run,))
    thread.start()
    try:
        controller.wait_for_input()
        controller.submit("hello /tmp")
        wait_until(lambda: "armed" in seen, message="include_paths")
        assert seen["armed"] is False
    finally:
        controller.release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not errors


def test_unarmed_wake_does_not_steal_confirmation(tmp_path):
    notify_completion("child", "success", "done", parent_logdir=tmp_path / "parent")
    prompt.request_idle_wake()
    with prompt._idle_lock:
        assert not prompt._pending_wake
        assert not prompt._idle_armed
    assert [item[0] for item in take_queued_completions(tmp_path / "parent")] == [
        "child"
    ]


def _wait_app_running(session, timeout: float = 5) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        app = session.app
        if app is not None and app.is_running and getattr(app, "loop", None):
            return
        time.sleep(0.01)
    raise AssertionError("prompt_toolkit application did not start")


def _exit_app(session, value: object = "done") -> None:
    app = session.app
    loop = getattr(app, "loop", None)
    if app is None or loop is None or not app.is_running:
        return
    loop.call_soon_threadsafe(lambda: app.exit(result=value))


def test_prompt_toolkit_wake_restores_draft_and_cursor(tmp_path, monkeypatch):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    monkeypatch.setattr(prompt, "get_pt_history_file", lambda: tmp_path / "history")
    monkeypatch.setattr(prompt, "_prompt_session", None)

    with create_pipe_input() as pipe:
        session: PromptSession[object] = PromptSession(
            input=pipe,
            output=DummyOutput(),
            history=InMemoryHistory(),
            multiline=True,
        )
        monkeypatch.setattr(prompt, "_prompt_session", session)
        result: list[object] = []
        restored: dict[str, object] = {}
        threads: list[threading.Thread] = []

        def run_prompt() -> None:
            prompt.arm_idle_prompt()
            try:
                result.append(prompt.get_input("User"))
            finally:
                prompt.disarm_idle_prompt()

        try:
            thread = threading.Thread(target=run_prompt)
            threads.append(thread)
            thread.start()
            _wait_app_running(session)

            applied = threading.Event()

            def set_draft() -> None:
                session.default_buffer.text = "unfinished draft"
                session.default_buffer.cursor_position = 4
                applied.set()

            assert session.app.loop is not None
            session.app.loop.call_soon_threadsafe(set_draft)
            assert applied.wait(3)
            prompt.request_idle_wake()
            thread.join(5)
            assert not thread.is_alive()
            assert result == [prompt.NOTIFICATION_WAKE]
            draft = prompt.get_saved_draft()
            assert draft is not None
            assert draft.text == "unfinished draft"
            assert draft.cursor == 4

            second = threading.Thread(target=run_prompt)
            threads.append(second)
            second.start()
            _wait_app_running(session)
            captured = threading.Event()

            def capture() -> None:
                restored["text"] = session.default_buffer.text
                restored["cursor"] = session.default_buffer.cursor_position
                captured.set()

            assert session.app.loop is not None
            session.app.loop.call_soon_threadsafe(capture)
            assert captured.wait(3)
            _exit_app(session, "submitted")
            second.join(5)
            assert not second.is_alive()
            assert restored["text"] == "unfinished draft"
            assert restored["cursor"] == 4
        finally:
            _exit_app(session, "cleanup")
            for worker in threads:
                worker.join(2)


@pytest.mark.parametrize("cancel", [False, True])
def test_real_prompt_wake_preserves_completion_until_cancel(
    tmp_path, monkeypatch, cancel
):
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    monkeypatch.chdir(tmp_path)
    (tmp_path / "my notes").mkdir()
    monkeypatch.setattr(prompt, "get_pt_history_file", lambda: tmp_path / "history")
    monkeypatch.setattr(prompt, "_prompt_session", None)
    results: list[object] = []
    errors: list[BaseException] = []
    threads: list[threading.Thread] = []

    with (
        create_pipe_input() as pipe,
        create_app_session(input=pipe, output=DummyOutput()),
    ):
        session = prompt.get_prompt_session()
        completer = session.completer
        assert isinstance(completer, prompt.GptmeCompleter)

        def run_prompt() -> None:
            prompt.arm_idle_prompt()
            try:
                results.append(prompt.get_input("User"))
            except BaseException as exc:
                errors.append(exc)
            finally:
                prompt.disarm_idle_prompt()

        def start() -> threading.Thread:
            worker = threading.Thread(target=run_prompt)
            threads.append(worker)
            worker.start()
            _wait_app_running(session)
            return worker

        def on_loop(fn) -> None:
            done = threading.Event()

            def apply() -> None:
                try:
                    fn()
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    done.set()

            assert session.app.loop is not None
            session.app.loop.call_soon_threadsafe(apply)
            assert done.wait(3)
            assert not errors

        try:
            first = start()

            def complete_directory() -> None:
                document = Document("@my", cursor_position=3)
                options = list(completer.get_completions(document, CompleteEvent()))
                completion = next(c for c in options if "my notes" in c.text)
                session.default_buffer.text = completion.text

            on_loop(complete_directory)
            prompt.request_idle_wake()
            first.join(5)
            assert not first.is_alive()
            assert results == [prompt.NOTIFICATION_WAKE]
            second = start()
            on_loop(lambda: None)  # Wait until pre_run restored the draft.
            if cancel:
                on_loop(lambda: setattr(session.default_buffer, "text", "replacement"))
                pipe.send_text("\x03")
                second.join(5)
                assert not second.is_alive()
                assert len(errors) == 1 and isinstance(errors[0], KeyboardInterrupt)
                errors.clear()
                assert prompt.get_saved_draft() is None
                third = start()
                on_loop(lambda: None)
                assert session.default_buffer.text == ""
                pipe.send_text("fresh\r")
                third.join(5)
                assert not third.is_alive()
                assert results[-1] == "fresh"
            else:
                pipe.send_text("\r")
                second.join(5)
                assert not second.is_alive()
                assert results[-1] == "`@my notes/`"
            assert not errors
        finally:
            _exit_app(session, "cleanup")
            for worker in threads:
                worker.join(2)


@pytest.mark.parametrize("queued", [False, True])
def test_declined_tool_blocks_later_idle_generation(tmp_path, monkeypatch, queued):
    from gptme.constants import DECLINED_CONTENT

    manager = manager_at(tmp_path)
    calls: list[int] = []
    errors: list[BaseException] = []
    controller = InputController()

    def decline(*_args, **_kwargs):
        calls.append(1)
        yield Message("system", DECLINED_CONTENT)

    monkeypatch.setattr(prompt, "get_input", controller.get_input)
    monkeypatch.setattr(chat, "step", decline)
    monkeypatch.setattr(chat, "execute_cmd", lambda *_args: False)

    def run() -> None:
        try:
            chat._run_chat_loop(
                manager,
                [Message("user", "run tool")] if queued else [],
                False,
                interactive=True,
                logdir=manager.logdir,
            )
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=contextvars.copy_context().run, args=(run,))
    worker.start()
    try:
        seq = controller.wait_for_input()
        if not queued:
            controller.submit("run tool")
            seq = controller.wait_for_input(after_seq=seq)
        assert calls == [1]
        notify_completion(
            "child", "success", "after decline", parent_logdir=manager.logdir
        )
        controller.wait_for_input(after_seq=seq)
        assert sum("after decline" in msg.content for msg in manager.log) == 1
        assert calls == [1], "declining execution must block notification generation"
    finally:
        controller.release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert not errors


def test_chat_invocation_isolates_and_restores_idle_cancellation(tmp_path, monkeypatch):
    from gptme.constants import DECLINED_CONTENT

    outer = manager_at(tmp_path, "outer")
    inner = manager_at(tmp_path, "inner")
    monkeypatch.setattr(
        chat, "step", lambda *_a, **_kw: iter([Message("system", DECLINED_CONTENT)])
    )
    assert chat._process_message_conversation(outer, False, "markdown", None)
    notify_completion("outer-child", "success", "done", parent_logdir=outer.logdir)
    assert not chat._deliver_idle_notifications(outer)

    class StopSetup(Exception):
        pass

    def check_fresh_invocation(*_args):
        notify_completion("inner-child", "success", "done", parent_logdir=inner.logdir)
        assert chat._deliver_idle_notifications(inner), "new chat inherits cancellation"
        raise StopSetup

    monkeypatch.setattr(chat, "init", check_fresh_invocation)
    with pytest.raises(StopSetup):
        chat.chat(
            [Message("user", "new prompt")],
            [],
            inner.logdir,
            tmp_path,
            "local/test",
            tool_format="markdown",
        )
    notify_completion("outer-next", "success", "done", parent_logdir=outer.logdir)
    assert not chat._deliver_idle_notifications(outer), (
        "nested chat cleared outer pause"
    )


def test_history_search_wake_preserves_main_draft(tmp_path, monkeypatch):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.search import SearchDirection, start_search

    monkeypatch.setattr(prompt, "get_pt_history_file", lambda: tmp_path / "history")
    results: list[object] = []
    errors: list[BaseException] = []
    workers: list[threading.Thread] = []
    with create_pipe_input() as pipe:
        session: PromptSession[object] = PromptSession(
            input=pipe, output=DummyOutput(), history=InMemoryHistory(), multiline=True
        )
        monkeypatch.setattr(prompt, "_prompt_session", session)

        def run() -> None:
            prompt.arm_idle_prompt()
            try:
                results.append(prompt.get_input("User"))
            except BaseException as exc:
                errors.append(exc)
            finally:
                prompt.disarm_idle_prompt()

        def start() -> threading.Thread:
            worker = threading.Thread(target=run)
            workers.append(worker)
            worker.start()
            _wait_app_running(session)
            return worker

        try:
            first = start()
            searching = threading.Event()

            def enter_search() -> None:
                session.default_buffer.text = "unfinished main draft"
                session.default_buffer.cursor_position = 7
                start_search(direction=SearchDirection.BACKWARD)
                session.app.current_buffer.text = "history query"
                searching.set()

            assert session.app.loop is not None
            session.app.loop.call_soon_threadsafe(enter_search)
            assert searching.wait(3)
            assert session.app.current_buffer is not session.default_buffer
            prompt.request_idle_wake()
            first.join(5)
            assert not first.is_alive()
            assert not errors
            assert results == [prompt.NOTIFICATION_WAKE]
            draft = prompt.get_saved_draft()
            assert draft is not None
            assert draft.text == "unfinished main draft"
            assert draft.cursor == 7
            second = start()
            restored = threading.Event()
            assert session.app.loop is not None
            session.app.loop.call_soon_threadsafe(restored.set)
            assert restored.wait(3)
            assert session.default_buffer.text == "unfinished main draft"
            assert session.default_buffer.cursor_position == 7
            _exit_app(session, "submitted")
            second.join(5)
            assert not second.is_alive()
            assert not errors
        finally:
            _exit_app(session, "cleanup")
            for worker in workers:
                worker.join(2)


@pytest.mark.parametrize("completed", [False, True])
def test_idle_notice_keeps_resume_profile_transparent(tmp_path, completed):
    from gptme.logmanager import Log

    messages = [Message("user", "hello")]
    if completed:
        messages.append(Message("assistant", "done"))
    messages.extend(
        [
            Message(
                "system",
                "profile",
                pinned=True,
                metadata={"resume_key": "agent_profile"},
            ),
            Message("system", "child done", metadata={"idle_notification": True}),
        ]
    )
    assert chat._should_prompt_for_input(Log(messages)) is completed


def test_queued_user_resume_reenables_idle_wake(tmp_path, monkeypatch):
    manager = manager_at(tmp_path)
    chat._idle_autowake_blocked.set(True)
    decisions: list[bool] = []

    def process(*_args):
        notify_completion("child", "success", "done", parent_logdir=manager.logdir)
        decisions.append(chat._deliver_idle_notifications(manager))
        manager.append(Message("assistant", "done"))
        return False

    monkeypatch.setattr(chat, "_process_message_conversation", process)
    monkeypatch.setattr(chat, "execute_cmd", lambda *_args: False)
    chat._run_chat_loop(manager, [Message("user", "resume")], False, interactive=False)
    assert decisions == [True]


def test_chat_invocation_scopes_saved_idle_draft(tmp_path, monkeypatch):
    outer = prompt.IdleDraft("outer text", 3, completed_directory=True)
    prompt._saved_draft = outer
    manager = manager_at(tmp_path)

    class StopSetup(Exception):
        pass

    def check_draft(*_args):
        assert prompt.get_saved_draft() is None, "new chat inherited unsent text"
        prompt._saved_draft = prompt.IdleDraft("inner text", 2)
        raise StopSetup

    monkeypatch.setattr(chat, "init", check_draft)
    with pytest.raises(StopSetup):
        chat.chat(
            [], [], manager.logdir, tmp_path, "local/test", tool_format="markdown"
        )
    assert prompt.get_saved_draft() is outer
