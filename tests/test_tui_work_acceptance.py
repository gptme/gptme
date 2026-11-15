"""Integrated work-view ownership and timeout acceptance (gptme/gptme#4557)."""

import asyncio
import json
import threading
import time
from collections.abc import Generator, Iterable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest

pytest.importorskip("textual")

from textual.pilot import Pilot
from textual.widgets import Static

from gptme.hooks import clear_hooks
from gptme.llm.models import ModelMeta
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tools import clear_tools, get_tools, init_tools, watch
from gptme.tools.subagent import Subagent
from gptme.tools.subagent.hooks import notify_completion
from gptme.tools.subagent.types import (
    ReturnType,
    _completion_queue,
    _progress_queue,
    _subagent_results,
    _subagents,
)
from gptme.tui.app import (
    ChatInput,
    ChatScroll,
    GptmeApp,
    WorkOverviewScreen,
)
from gptme.tui.child_view import ChildView

if TYPE_CHECKING:
    from textual.widget import Widget

ContextFingerprint = tuple[LogManager | None, ModelMeta | None, tuple[int, ...]]


@pytest.fixture(autouse=True)
def isolated_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[None, None, None]:
    """No persisted agents, server delivery, configured providers or live workers."""
    monkeypatch.setenv("GPTME_USER_CONFIG", str(tmp_path / "no-config.toml"))
    monkeypatch.setattr("gptme.tui.app.maybe_start_auto_naming", lambda *a, **kw: None)
    monkeypatch.setattr(
        "gptme.tools.subagent.api._maybe_rehydrate_registry", lambda: None
    )
    monkeypatch.setattr(
        "gptme.tools.subagent.types._persist_cached_outcome", lambda _: None
    )
    monkeypatch.setattr(
        "gptme.server.session_models.SessionManager.request_watch_wake",
        lambda *a, **kw: False,
    )
    clear_hooks()
    clear_tools()
    init_tools(["subagent", "watch"], include_mcp=False)

    def reset() -> None:
        _subagents.clear()
        _subagent_results.clear()
        watch._watches.clear()
        watch._pending_deliveries.clear()
        for queue in (_completion_queue, _progress_queue):
            with queue.mutex:
                queue.queue.clear()

    reset()
    with patch("gptme.tools.subagent.hooks._notify_server", return_value=False):
        yield
    reset()
    clear_hooks()
    clear_tools()


async def settle(app: GptmeApp, pilot: Pilot[None]) -> None:
    for _ in range(4):
        await pilot.pause()
        await app.workers.wait_for_complete()
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True], ids=["fullscreen", "inline"])
@pytest.mark.parametrize("busy", [False, True], ids=["idle", "generating"])
async def test_two_children_two_watches_preserve_parent_and_defer_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline: bool, busy: bool
) -> None:
    """Inspect a locked child; one completion + timeout yield one owner wake."""
    fcntl = pytest.importorskip("fcntl")
    from gptme.config import ChatConfig, get_config
    from gptme.llm.models import get_default_model

    get_config().chat = ChatConfig(watch_autowake=True)
    # Exercise production parent writer ownership too: LogManager ordinarily
    # disables locks under pytest. Its own context manager releases it below.
    with monkeypatch.context() as production_lock:
        production_lock.delenv("PYTEST_CURRENT_TEST", raising=False)
        manager = LogManager(
            [Message("user", f"parent line {i}") for i in range(30)],
            logdir=tmp_path / "parent",
        )
    parent_lock = manager.logdir / ".lock"
    parent_lock_inode = parent_lock.stat().st_ino
    branch = manager.current_branch
    child_dir = tmp_path / "child-a"
    child_dir.mkdir()
    transcript = child_dir / "conversation.jsonl"
    transcript.write_text(
        json.dumps({"role": "assistant", "content": "child transcript only"})
        + '\n{"role": "assistant", "content": "unfinished'
    )
    original_bytes = transcript.read_bytes()
    lock_path = child_dir / ".lock"
    lock_path.write_text("child writer owns this conversation")
    lock_inode = lock_path.stat().st_ino
    # Only thread liveness is exercised; no real child worker is started.
    running = cast(threading.Thread, SimpleNamespace(is_alive=lambda: True))
    for agent_id, logdir, parent, origin in (
        ("child-a", child_dir, manager.logdir, branch),
        ("child-b", tmp_path / "child-b", manager.logdir, branch),
        ("foreign-owner", tmp_path / "foreign", tmp_path / "stranger", branch),
        ("foreign-branch", tmp_path / "side", manager.logdir, "side"),
    ):
        _subagents.append(
            Subagent(
                agent_id=agent_id,
                prompt=f"task for {agent_id}",
                thread=running,
                logdir=logdir,
                model=None,
                parent_logdir=parent,
                parent_branch=origin,
                started_at=time.time(),
            )
        )
    monitors = []
    for watch_id, parent, origin in (
        ("timeout", manager.logdir, branch),
        ("still-armed", manager.logdir, branch),
        ("foreign-watch", tmp_path / "stranger", branch),
        ("side-watch", manager.logdir, "side"),
    ):
        monitor = watch.Watch(
            watch_id,
            "timer",
            f"await {watch_id}",
            100.0,
            logdir=parent,
            branch=origin,
            deadline=101.0,
        )
        watch._register(monitor)
        monitors.append(monitor)

    started, release = threading.Event(), threading.Event()
    turns: list[list[str]] = []
    worker_contexts: list[tuple[ContextFingerprint, ContextFingerprint]] = []
    turn_contexts: list[ContextFingerprint] = []

    def context_fingerprint() -> ContextFingerprint:
        return (
            LogManager.get_current_log(),
            get_default_model(),
            tuple(id(tool) for tool in get_tools()),
        )

    def model(
        log: Iterable[Message], **kwargs: object
    ) -> Generator[Message, None, None]:
        turns.append([msg.content for msg in log])
        turn_contexts.append(context_fingerprint())
        if busy and len(turns) == 1:
            before = context_fingerprint()
            started.set()
            assert release.wait(10), "test did not release parent generation"
            worker_contexts.append((before, context_fingerprint()))
        yield Message("assistant", "parent response")

    monkeypatch.setattr("gptme.tui.app.step", model)
    app = GptmeApp(manager, workspace=tmp_path, inline=inline)
    try:
        with manager, lock_path.open("r+") as writer:
            fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            async with app.run_test(size=(90, 28)) as pilot:
                await pilot.pause()
                if busy:
                    app._start_generation()
                    assert await asyncio.to_thread(started.wait, 5)
                    await pilot.pause()
                parent_screen = app.screen
                field = app.query_one("#input", ChatInput)
                field.text = "unsent parent draft"
                field.move_cursor((0, 7))
                # Inline mode writes transcript output to the terminal rather
                # than mounting a ChatScroll; preserve its screen instead.
                chat: Widget
                if inline:
                    chat = parent_screen
                else:
                    chat = app.query_one(ChatScroll)
                chat.scroll_to(y=4, animate=False)
                await pilot.pause()
                selection, scroll = field.selection, chat.scroll_y
                if not inline:
                    assert 0 < scroll < chat.max_scroll_y
                context = context_fingerprint()
                expected_worker_context = (
                    manager,
                    context[1],
                    context[2],
                )
                chat_context = app._chat_ctx
                workers = tuple(app.workers)
                log_before = list(manager.log)

                await pilot.press("ctrl+t")
                assert isinstance(app.screen, WorkOverviewScreen)
                overview = app.screen
                content = overview.query_one("#work-overview-content", Static)
                monitor_view = overview.query_one("#work-overview-monitors", Static)
                summary = f"{content.render()}\n{monitor_view.render()}"
                assert len(overview._children) == 2
                for label in ("child-a", "child-b", "timeout", "still-armed"):
                    assert label in summary
                assert summary.count("[running]") == 2
                assert summary.count("[armed]") == 2
                assert "foreign" not in summary and "side-watch" not in summary
                # Catch context adoption even though pytest normally skips locks.
                with patch.object(
                    LogManager, "load", side_effect=AssertionError("child load")
                ) as load_child:
                    await pilot.press("enter")
                    assert app.screen is parent_screen
                    assert overview not in app.screen_stack
                    child_view = app._child_view
                    assert isinstance(child_view, ChildView)
                    child_text = "\n".join(msg.content for msg in child_view._messages)
                    assert "child transcript only" in child_text
                    assert "unfinished" not in child_text
                    assert app.manager is manager
                    assert context_fingerprint() == context
                    assert app._chat_ctx is chat_context
                    assert tuple(app.workers) == workers
                    assert app.generating is busy
                    assert (
                        field.text == "unsent parent draft"
                        and field.selection == selection
                    )
                    assert chat.scroll_y == scroll

                    # Completing and expiring while inspection is open must not
                    # acquire a writer or redirect an automatic turn to the child.
                    _subagents[0] = replace(_subagents[0], thread=None)
                    _subagent_results["child-a"] = ReturnType("success", "child done")
                    notify_completion(
                        "child-a",
                        "success",
                        "child done",
                        parent_logdir=manager.logdir,
                        parent_branch=branch,
                    )
                    now = 100.0

                    def advance(timeout: float | None = None) -> bool:
                        nonlocal now
                        assert timeout is not None and 0 < timeout <= 101.0 - now
                        now += timeout
                        return False

                    with monkeypatch.context() as clock:
                        clock.setattr(watch, "time", SimpleNamespace(time=lambda: now))
                        clock.setattr(monitors[0].cancel_event, "wait", advance)
                        watch._poll_timer(monitors[0], 5.0)
                    assert now == 101.0
                    await pilot.pause()
                    assert monitors[0].status == "expired"
                    assert list(manager.log) == log_before
                    assert len(turns) == int(busy)
                    await pilot.press("escape")
                    load_child.assert_not_called()
                assert app.screen is parent_screen
                assert app._child_view is None
                # Esc returns directly to the parent; reopen the overview to
                # inspect the completed child and expired watch explicitly.
                await pilot.press("ctrl+t")
                overview = app.screen
                assert isinstance(overview, WorkOverviewScreen)
                overview._refresh_summary()
                content = overview.query_one("#work-overview-content", Static)
                monitor_view = overview.query_one("#work-overview-monitors", Static)
                summary = f"{content.render()}\n{monitor_view.render()}"
                assert "[success]" in summary and "[running]" in summary
                assert "[expired]" in summary and "[armed]" in summary
                # Returning from ChildView already releases deferred delivery.
                # The untouched-parent-log assertion above belongs before Esc,
                # not after reopening this new overview from the parent.
                await pilot.press("escape")
                assert app.screen is parent_screen
                assert app.focused is field
                assert chat.scroll_y == scroll
                assert (
                    field.text == "unsent parent draft" and field.selection == selection
                )
                assert app.manager is manager and manager.current_branch == branch
                assert app._chat_ctx is chat_context
                assert context_fingerprint() == context
                if busy:
                    assert tuple(app.workers) == workers
                    assert app.generating
                release.set()
                await settle(app, pilot)
                assert len(turns) == 1 + int(busy)
                assert all(ctx == expected_worker_context for ctx in turn_contexts)
                terminal = [msg.content for msg in manager.log if msg.role == "system"]
                assert sum("Subagent 'child-a'" in text for text in terminal) == 1
                assert sum("Watch timeout expired:" in text for text in terminal) == 1
                assert not any("Watch timeout fired" in text for text in terminal)
                assert any("expired before timer elapsed" in text for text in turns[-1])
                assert not app.generating
                assert (
                    field.text == "unsent parent draft" and field.selection == selection
                )
                assert app.manager is manager and manager.current_branch == branch
                if busy:
                    assert worker_contexts[0][0] == worker_contexts[0][1]
                    assert worker_contexts[0][0][0] is manager
                await settle(app, pilot)
                assert len(turns) == 1 + int(busy)  # no duplicate wake/re-arm loop
                assert (
                    watch.take_queued_watch_events(manager.logdir, branch=branch) == []
                )
                assert monitors[1].status == "armed"
                assert transcript.read_bytes() == original_bytes
                assert lock_path.stat().st_ino == lock_inode
                assert parent_lock.stat().st_ino == parent_lock_inode
                for owned_lock in (lock_path, parent_lock):
                    with (
                        owned_lock.open("r+") as contender,
                        pytest.raises(BlockingIOError),
                    ):
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True], ids=["fullscreen", "inline"])
async def test_timeout_alone_wakes_idle_parent_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline: bool
) -> None:
    """Expiry must signal its own wake, without a completion or screen change."""
    from gptme.config import ChatConfig, get_config

    get_config().chat = ChatConfig(watch_autowake=True)
    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    turns: list[list[str]] = []

    def model(log: Iterable[Message], **kwargs: object) -> Iterable[Message]:
        assert LogManager.get_current_log() is manager
        turns.append([msg.content for msg in log])
        return [Message("assistant", "reevaluated expired wait")]

    monkeypatch.setattr("gptme.tui.app.step", model)
    monitor = watch.Watch(
        "only-timeout",
        "timer",
        "external condition",
        100.0,
        logdir=manager.logdir,
        branch=manager.current_branch,
        deadline=101.0,
    )
    watch._register(monitor)
    app = GptmeApp(manager, workspace=tmp_path, inline=inline)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert not turns
        now = 100.0

        def advance(timeout: float | None = None) -> bool:
            nonlocal now
            assert timeout is not None and 0 < timeout <= 101.0 - now
            now += timeout
            return False

        with monkeypatch.context() as clock:
            clock.setattr(watch, "time", SimpleNamespace(time=lambda: now))
            clock.setattr(monitor.cancel_event, "wait", advance)
            watch._poll_timer(monitor, 5.0)
        assert now == 101.0
        await settle(app, pilot)
        assert len(turns) == 1
        assert any("Watch only-timeout expired:" in text for text in turns[0])
        assert not any("only-timeout fired" in text for text in turns[0])
        await settle(app, pilot)
        assert len(turns) == 1
        assert monitor.delivered == 1 and monitor.status == "expired"
