"""Offline completion delivery through real Textual workers and event loop."""

import asyncio
import threading
from unittest.mock import patch

import pytest

pytest.importorskip("textual")

from gptme.hooks import clear_hooks
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tools import clear_tools, init_tools
from gptme.tools.subagent.hooks import notify_completion, notify_progress
from gptme.tools.subagent.types import Status, _completion_queue, _progress_queue
from gptme.tui.app import ChatInput, GptmeApp


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_USER_CONFIG", str(tmp_path / "no-config.toml"))
    for queue in (_completion_queue, _progress_queue):
        with queue.mutex:
            queue.queue.clear()
    clear_hooks()
    clear_tools()
    init_tools(["subagent", "watch"], include_mcp=False)
    with patch("gptme.tools.subagent.hooks._notify_server", return_value=False):
        yield
    clear_hooks()
    clear_tools()
    for queue in (_completion_queue, _progress_queue):
        with queue.mutex:
            queue.queue.clear()
    from gptme.tools import watch

    with watch._pending_lock:
        watch._pending_deliveries.clear()


def record_step(seen, value=1):
    seen.append(value)
    return iter([Message("assistant", "done")])


async def settle(app, pilot):
    for _ in range(8):
        await pilot.pause()
        await app.workers.wait_for_complete()
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before-mount", "active", "idle"])
async def test_completion_wakes_once_preserving_draft(tmp_path, monkeypatch, when):
    manager = LogManager(
        [Message("user", "delegate")], logdir=tmp_path / "parent", lock=False
    )
    seen = []

    def model(log, **kwargs):
        seen.append([msg.content for msg in log])
        if when == "active" and len(seen) == 1:
            notify_completion("child", "success", "done", parent_logdir=manager.logdir)
        yield Message("assistant", "Final text.")

    monkeypatch.setattr("gptme.tui.app.step", model)
    if when == "before-mount":
        notify_completion("child", "success", "done", parent_logdir=manager.logdir)
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        field = app.query_one("#input", ChatInput)
        field._set_text("unfinished draft")
        field.move_cursor((0, 4))
        selection = field.selection
        if when != "before-mount":
            app._start_generation()
            await settle(app, pilot)
        if when == "idle":
            # Complete from a real foreign thread; callback must only signal.
            thread = threading.Thread(
                target=lambda: notify_completion(
                    "child", "success", "done", parent_logdir=manager.logdir
                )
            )
            thread.start()
            await asyncio.to_thread(thread.join)
        await settle(app, pilot)
        assert sum("Subagent 'child'" in msg.content for msg in manager.log) == 1
        assert len(seen) == (1 if when == "before-mount" else 2)
        assert any("Subagent 'child'" in text for text in seen[-1])
        assert field.text == "unfinished draft"
        assert field.selection == selection
        assert not app.generating


@pytest.mark.asyncio
async def test_progress_is_notification_only(tmp_path, monkeypatch):
    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    seen: list[int] = []
    monkeypatch.setattr("gptme.tui.app.step", lambda *a, **kw: record_step(seen))
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        notify_progress("child", "working", parent_logdir=manager.logdir)
        await settle(app, pilot)
        assert not seen
        assert sum("working" in msg.content for msg in manager.log) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("kind", ["child", "watch"])
async def test_policy_status_burst_and_foreign_parent(
    tmp_path, monkeypatch, enabled, kind
):
    from gptme.config import ChatConfig, get_config
    from gptme.tools import watch
    from gptme.tools.subagent.hooks import take_queued_completions

    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    foreign = tmp_path / "foreign"
    get_config().chat = ChatConfig(watch_autowake=enabled)
    seen = []
    active = 0
    maximum = 0

    def model(log, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        seen.append([m.content for m in log])
        yield Message("assistant", "done")
        active -= 1

    monkeypatch.setattr("gptme.tui.app.step", model)
    monkeypatch.setattr(
        "gptme.server.session_models.SessionManager.request_watch_wake",
        lambda *a, **kw: False,
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        notify_completion("foreign", "success", "foreign text", parent_logdir=foreign)
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
        await settle(app, pilot)
        assert len(seen) == int(enabled)
        assert maximum <= 1
        assert len([m for m in manager.log if m.role == "system"]) == (
            6 if kind == "child" else 3
        )
        assert not any("foreign text" in m.content for m in manager.log)
        assert [item[0] for item in take_queued_completions(foreign)] == ["foreign"]
        await settle(app, pilot)
        assert len(seen) == int(enabled)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["child", "watch"])
async def test_originating_branch_deferred_until_return(tmp_path, monkeypatch, kind):
    from gptme.tools import watch

    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    seen: list[str] = []
    monkeypatch.setattr(
        "gptme.tui.app.step",
        lambda log, **kw: record_step(seen, manager.current_branch),
    )
    monkeypatch.setattr(
        "gptme.server.session_models.SessionManager.request_watch_wake",
        lambda *a, **kw: False,
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        manager.branch("other")
        if kind == "child":
            notify_completion(
                "child",
                "success",
                "origin result",
                parent_logdir=manager.logdir,
                parent_branch="main",
            )
        else:
            watch._deliver(
                watch.Watch(
                    "w1", "timer", "test", 0, logdir=manager.logdir, branch="main"
                ),
                "origin result",
            )
        await settle(app, pilot)
        assert not seen
        assert not any("origin result" in m.content for m in manager.log)
        manager.branch("main")
        app._finish_command(
            "branch",
            [],
            type(
                "Result",
                (),
                {"exit": False, "error": None, "output": "", "handled": True},
            )(),
        )
        await settle(app, pilot)
        assert seen == ["main"]
        assert sum("origin result" in m.content for m in manager.log) == 1


@pytest.mark.asyncio
async def test_modal_defers_wake_and_unmount_unsubscribes(tmp_path, monkeypatch):
    from textual.screen import ModalScreen

    from gptme.util import notification_ready

    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    seen: list[int] = []
    monkeypatch.setattr(
        "gptme.tui.app.step",
        lambda *a, **kw: record_step(seen),
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await app.push_screen(ModalScreen())
        notify_completion("child", "success", "done", parent_logdir=manager.logdir)
        await settle(app, pilot)
        assert not seen
        await app.pop_screen()
        await settle(app, pilot)
        assert seen == [1]
    assert manager.logdir.resolve() not in notification_ready._listeners
    notify_completion("after-exit", "success", "late", parent_logdir=manager.logdir)
    assert not any("after-exit" in m.content for m in manager.log)


@pytest.mark.asyncio
async def test_final_worker_exit_window_and_interrupt(tmp_path, monkeypatch):
    """Queue in the worker's last instruction, then retry only after context exit."""
    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    calls: list[int] = []
    monkeypatch.setattr(
        "gptme.tui.app.step",
        lambda *a, **kw: record_step(calls),
    )
    app = GptmeApp(manager, workspace=tmp_path)
    original = app._generation_body
    first = True

    def body():
        nonlocal first
        original()
        if first:
            first = False
            notify_completion(
                "exit-window", "success", "ready", parent_logdir=manager.logdir
            )

    monkeypatch.setattr(app, "_generation_body", body)
    async with app.run_test() as pilot:
        app._start_generation()
        await settle(app, pilot)
        assert calls == [1, 1]
        assert sum("exit-window" in m.content for m in manager.log) == 1
        assert not any("Error:" in m.content for m in manager.log)
        app._interrupt_event.set()
        notify_completion(
            "interrupted", "success", "no forced restart", parent_logdir=manager.logdir
        )
        await settle(app, pilot)
        assert calls == [1, 1]
        assert sum("interrupted" in m.content for m in manager.log) == 1


@pytest.mark.asyncio
async def test_command_busy_defers_and_preserves_draft(tmp_path, monkeypatch):
    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    calls: list[int] = []
    monkeypatch.setattr(
        "gptme.tui.app.step",
        lambda *a, **kw: record_step(calls),
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        app.generating = True  # command owns the context until its release callback
        field = app.query_one("#input", ChatInput)
        field._set_text("keep me")
        notify_completion("busy", "success", "ready", parent_logdir=manager.logdir)
        await pilot.pause()
        assert not calls
        assert not any("Subagent 'busy'" in m.content for m in manager.log)
        from gptme.tui.app import _CommandResult

        app._background_command_done("help", [], _CommandResult(handled=True))
        await settle(app, pilot)
        assert calls == [1]
        assert field.text == "keep me"


@pytest.mark.asyncio
async def test_step_pre_control_and_steer_hooks_run(tmp_path, monkeypatch):
    from gptme.hooks import HookType, register_hook
    from gptme.tools.complete import SessionCompleteException

    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "gptme.tui.app.step",
        lambda log, **kw: record_step(calls, [m.content for m in log]),
    )

    def steer(manager):
        yield Message("user", "steering at boundary")

    register_hook("test_steer", HookType.STEP_PRE, steer)
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        app._start_generation()
        await settle(app, pilot)
        assert "steering at boundary" in calls[0]

        def cancel(manager):
            raise SessionCompleteException("cancel at boundary")

        register_hook("test_cancel", HookType.STEP_PRE, cancel, priority=100)
        app._start_generation()
        await settle(app, pilot)
        assert len(calls) == 1
        assert not app.generating


@pytest.mark.asyncio
async def test_progress_waits_for_originating_branch(tmp_path, monkeypatch):
    from gptme.tools.subagent.types import Subagent, _subagents, _subagents_lock

    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    child = Subagent(
        "progress-child",
        "test",
        None,
        tmp_path / "child",
        None,
        parent_logdir=manager.logdir,
        parent_branch="main",
    )
    with _subagents_lock:
        _subagents.append(child)
    seen: list[int] = []
    monkeypatch.setattr("gptme.tui.app.step", lambda *a, **kw: record_step(seen))
    try:
        app = GptmeApp(manager, workspace=tmp_path)
        async with app.run_test() as pilot:
            manager.branch("other")
            notify_progress(
                "progress-child", "owned progress", parent_logdir=manager.logdir
            )
            await settle(app, pilot)
            assert not any("owned progress" in m.content for m in manager.log)
            manager.branch("main")
            app.post_message(app.NotificationReady())
            await settle(app, pilot)
            assert sum("owned progress" in m.content for m in manager.log) == 1
            assert not seen
    finally:
        with _subagents_lock:
            _subagents.remove(child)


@pytest.mark.asyncio
async def test_real_confirmation_defers_completion(tmp_path, monkeypatch):
    from gptme.tools.base import ToolUse
    from gptme.tui.app import ConfirmScreen

    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    calls: list[int] = []
    app = GptmeApp(manager, workspace=tmp_path)

    def model(log, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            app._tui_confirm_hook(ToolUse("shell", [], "echo confirmed"))
        yield Message("assistant", "done")

    monkeypatch.setattr("gptme.tui.app.step", model)
    async with app.run_test() as pilot:
        app._start_generation()
        for _ in range(100):
            await pilot.pause()
            if isinstance(app.screen, ConfirmScreen):
                break
            await asyncio.sleep(0.01)
        assert isinstance(app.screen, ConfirmScreen)
        notify_completion(
            "confirmation-child", "success", "done", parent_logdir=manager.logdir
        )
        await pilot.pause()
        assert calls == [1]
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("y")
        await settle(app, pilot)
        assert calls == [1, 1]
        assert sum("confirmation-child" in m.content for m in manager.log) == 1


def test_readiness_is_scoped_reentrant_and_unsubscribable(tmp_path):
    from gptme.util.notification_ready import signal, subscribe

    calls: list[int] = []
    stop = subscribe(tmp_path / "parent", lambda: calls.append(1))
    signal(tmp_path / "foreign")
    assert not calls
    signal(tmp_path / "parent")
    assert calls == [1]
    stop()
    stop()
    signal(tmp_path / "parent")
    assert calls == [1]


@pytest.mark.asyncio
async def test_initial_prompt_precedes_mount_time_completion(tmp_path, monkeypatch):
    """A queued completion must not steal the startup prompt's worker."""
    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    seen: list[list[str]] = []
    requested = threading.Event()
    release = threading.Event()

    def model(log, **kwargs):
        seen.append([m.content for m in log])
        requested.set()
        assert release.wait(5)
        yield Message("assistant", "done")

    monkeypatch.setattr("gptme.tui.app.step", model)
    notify_completion("child", "success", "ready", parent_logdir=manager.logdir)
    app = GptmeApp(manager, workspace=tmp_path, initial_prompts=["startup task"])
    try:
        async with app.run_test() as pilot:
            for _ in range(100):
                await pilot.pause()
                if requested.is_set():
                    break
                await asyncio.sleep(0.01)
            assert requested.is_set()
            assert "startup task" in seen[0]
            release.set()
            await settle(app, pilot)
            assert sum("Subagent 'child'" in m.content for m in manager.log) == 1
            assert not app.generating
    finally:
        release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_local_startup_command_retries_mount_time_completion(
    tmp_path, monkeypatch, enabled
):
    """A synchronous startup command must release the initial inbox deferral."""
    from gptme.config import ChatConfig, get_config

    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    get_config().chat = ChatConfig(watch_autowake=enabled)
    seen: list[int] = []
    monkeypatch.setattr("gptme.tui.app.step", lambda *a, **kw: record_step(seen))
    notify_completion("child", "success", "ready", parent_logdir=manager.logdir)
    app = GptmeApp(manager, workspace=tmp_path, initial_prompts=["/display outputs"])
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert not app._initial_prompts
        assert sum("Subagent 'child'" in m.content for m in manager.log) == 1
        assert seen == ([1] if enabled else [])
        assert not app.generating


@pytest.mark.asyncio
async def test_command_release_prioritizes_queued_input_over_notification(
    tmp_path, monkeypatch
):
    """Readiness must not steal the queued prompt's first model request."""
    from gptme.tui.app import _CommandResult

    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    seen: list[list[str]] = []
    requested = threading.Event()
    release = threading.Event()

    def model(log, **kwargs):
        seen.append([m.content for m in log])
        requested.set()
        assert release.wait(10)
        yield Message("assistant", "done")

    monkeypatch.setattr("gptme.tui.app.step", model)
    app = GptmeApp(manager, workspace=tmp_path)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.generating = True  # /help owns the context
            app._enqueue_prompt("queued user task")
            notify_completion(
                "busy-child", "success", "ready", parent_logdir=manager.logdir
            )
            await pilot.pause()
            # Hold the call_later dispatch so readiness deterministically runs
            # first, as permitted by the two independent release callbacks.
            dispatches = []
            call_later = app.call_later

            def defer_dispatch(callback, *args, **kwargs):
                if callback == app._dispatch_queue:
                    dispatches.append(callback)
                    return True
                return call_later(callback, *args, **kwargs)

            monkeypatch.setattr(app, "call_later", defer_dispatch)
            app._background_command_done("help", [], _CommandResult(handled=True))
            await pilot.pause()
            assert not requested.is_set()
            assert app.prompt_queue == ["queued user task"]
            assert len(dispatches) == 1
            await dispatches[0]()
            for _ in range(100):
                await pilot.pause()
                if requested.is_set():
                    break
                await asyncio.sleep(0.01)
            assert requested.is_set()
            assert "queued user task" in seen[0]
            assert sum("Subagent 'busy-child'" in m.content for m in manager.log) == 1
            assert len(seen) == 1
            release.set()
            await settle(app, pilot)
            assert not app.prompt_queue
            assert not app.generating
    finally:
        release.set()


@pytest.mark.asyncio
async def test_stale_dispatch_does_not_mutate_active_worker_log(tmp_path, monkeypatch):
    """A delayed command-release callback leaves input at the step boundary."""
    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.generating = True
        app._enqueue_prompt("pending at boundary")
        before = list(manager.log)
        await app._dispatch_queue()
        assert list(manager.log) == before
        assert app.prompt_queue == ["pending at boundary"]
        app.generating = False
