"""Regressions for child composer delivery and live inspection navigation."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest

pytest.importorskip("textual")
from textual import events
from textual.widgets import Collapsible, Static

from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tools.subagent import SubagentSnapshot
from gptme.tui.app import MAX_INITIAL_MESSAGES, ChatInput, ChatScroll, GptmeApp
from gptme.tui.child_view import ChildView

ChildApp = tuple[LogManager, list[SubagentSnapshot]]


@pytest.fixture
def child_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ChildApp:
    monkeypatch.setattr("gptme.tui.app.maybe_start_auto_naming", lambda *a, **kw: None)
    monkeypatch.setattr("gptme.tui.app.get_default_model", lambda: None)
    child_dir = tmp_path / "child"
    child_dir.mkdir()
    (child_dir / "conversation.jsonl").write_text("")
    child = SubagentSnapshot(
        run_id=str(child_dir),
        agent_id="first",
        status="running",
        started_at=1,
        elapsed_s=0,
        execution_mode="thread",
        prompt_preview="task",
        logdir=child_dir,
    )
    children = [child]
    monkeypatch.setattr(
        "gptme.tools.subagent.subagent_snapshots", lambda *a, **kw: children
    )
    manager = LogManager(
        [Message("user", "parent")], logdir=tmp_path / "parent", lock=False
    )
    return manager, children


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "success"])
async def test_rejected_paste_can_be_retried(
    child_app: ChildApp, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    manager, children = child_app
    children[0] = replace(children[0], status=status)
    route = Mock(side_effect=ValueError("delivery unavailable"))
    monkeypatch.setattr("gptme.tui.child_view.send_child_message", route)
    app = GptmeApp(manager)
    async with app.run_test() as pilot:
        app.open_child(children[0])
        await pilot.pause()
        editor = app.query_one("#child-input", ChatInput)
        pasted = "first line\nsecond line\nthird line"
        await editor._on_paste(events.Paste(pasted))
        await pilot.press("enter")
        await pilot.pause()
        assert editor._expand_pastes(editor.text) == pasted
        # A stopped-child rejection and a routing error must both retain mappings.
        children[0] = replace(children[0], status="running")
        app.query_one(ChildView).child = children[0]
        route.reset_mock(side_effect=True)
        route.side_effect = None
        route.return_value = "Sent"
        await pilot.press("enter")
        await pilot.pause()
        assert route.call_args.args[3] == pasted
        assert editor.text == ""
        assert editor._pastes == {}


@pytest.mark.asyncio
async def test_delivery_preserves_newer_paste(
    child_app: ChildApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, children = child_app
    started, release = Event(), Event()

    def send(*args, **kwargs) -> str:
        started.set()
        assert release.wait(10)
        return "Sent"

    monkeypatch.setattr("gptme.tui.child_view.send_child_message", send)
    app = GptmeApp(manager)
    async with app.run_test() as pilot:
        app.open_child(children[0])
        await pilot.pause()
        editor = app.query_one("#child-input", ChatInput)
        editor.text = "first instruction"
        delivery = asyncio.create_task(
            app.query_one(ChildView).on_chat_input_submitted(
                ChatInput.Submitted(editor.text)
            )
        )
        assert await asyncio.to_thread(started.wait, 5)
        try:
            editor.text = ""
            newer = "new first\nnew second\nnew third"
            await editor._on_paste(events.Paste(newer))
        finally:
            release.set()
        await delivery
        assert editor._expand_pastes(editor.text) == newer


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True])
async def test_switch_child_preserves_both_drafts_and_parent(
    child_app: ChildApp, inline: bool
) -> None:
    manager, children = child_app
    second = replace(children[0], run_id="second-run", agent_id="second")
    children.append(second)
    app = GptmeApp(manager, inline=inline)
    async with app.run_test() as pilot:
        parent = app.query_one("#input", ChatInput)
        parent.text = "parent draft"
        app.open_child(children[0])
        await pilot.pause()
        app.query_one("#child-input", ChatInput).text = "first draft"
        app.open_child(second)
        await pilot.pause()
        assert app.query_one(ChildView).child == second
        app.query_one("#child-input", ChatInput).text = "second draft"
        app.open_child(children[0])
        await pilot.pause()
        assert app.query_one("#child-input", ChatInput).text == "first draft"
        app.close_child()
        await pilot.pause()
        assert parent.display and parent.text == "parent draft"
        app.open_child(second)
        await pilot.pause()
        assert app.query_one("#child-input", ChatInput).text == "second draft"


@pytest.mark.asyncio
async def test_inline_status_updates_do_not_reveal_parent_rows(
    child_app: ChildApp,
) -> None:
    manager, children = child_app
    app = GptmeApp(manager, inline=True)
    async with app.run_test() as pilot:
        app.open_child(children[0])
        await pilot.pause()
        app.prompt_queue.append("queued parent instruction")
        app._update_queued_view()
        await app._update_work_indicator()
        assert not app.query_one("#queued", Static).display
        assert not app.query_one("#work-indicator", Static).display
        app.close_child()
        await pilot.pause()
        assert app.query_one("#queued", Static).display
        assert app.query_one("#work-indicator", Static).display


@pytest.mark.asyncio
async def test_rolling_tail_keeps_overlapping_expanded_widgets(
    child_app: ChildApp,
) -> None:
    manager, children = child_app
    path = children[0].logdir / "conversation.jsonl"
    messages = [
        {"role": "assistant", "content": f"message {i}\n```shell\necho {i}\n```"}
        for i in range(MAX_INITIAL_MESSAGES)
    ]
    path.write_text("".join(json.dumps(m) + "\n" for m in messages))
    app = GptmeApp(manager)
    async with app.run_test(size=(100, 35)) as pilot:
        app.open_child(children[0])
        await pilot.pause()
        view = app.query_one(ChildView)
        # Stop the periodic refresh and let an in-flight tick finish, so the
        # explicit refresh below is the one that rolls the tail.
        assert view._refresh_timer is not None
        view._refresh_timer.pause()
        while view._refreshing:
            await pilot.pause()
        chat = view.query_one("#child-chat", ChatScroll)
        retained = list(chat.children)[-1]
        block = retained.query_one(Collapsible)
        block.collapsed = False
        with path.open("a") as stream:
            stream.write(json.dumps({"role": "user", "content": "next"}) + "\n")
        await view.refresh_snapshot()
        assert retained.is_attached and not block.collapsed
        assert len(chat.children) == MAX_INITIAL_MESSAGES


@pytest.mark.asyncio
async def test_select_continued_run_with_same_logdir(child_app: ChildApp) -> None:
    manager, children = child_app
    app = GptmeApp(manager)
    async with app.run_test() as pilot:
        app.open_child(children[0])
        await pilot.pause()
        children[0] = replace(children[0], started_at=2)
        app.open_child(children[0])
        await pilot.pause()
        assert app.query_one(ChildView).child.started_at == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("continuation", [False, True])
async def test_pending_delivery_survives_close_and_reopen(
    child_app: ChildApp, monkeypatch: pytest.MonkeyPatch, continuation: bool
) -> None:
    manager, children = child_app
    started, release = Event(), Event()
    sends: list[str] = []

    def send(child, owner, branch, message, **kwargs) -> str:
        sends.append(message)
        started.set()
        assert release.wait(10)
        if continuation:
            children[0] = replace(children[0], started_at=2)
        return "Sent"

    monkeypatch.setattr("gptme.tui.child_view.send_child_message", send)
    app = GptmeApp(manager)
    async with app.run_test() as pilot:
        app.open_child(children[0])
        await pilot.pause()
        view = app.query_one(ChildView)
        editor = view.query_one("#child-input", ChatInput)
        editor.text = (
            "/continue deliver exactly once" if continuation else "deliver exactly once"
        )
        delivery = asyncio.create_task(
            view.on_chat_input_submitted(ChatInput.Submitted(editor.text))
        )
        assert await asyncio.to_thread(started.wait, 5)
        try:
            app.close_child()
            await pilot.pause()
            app.open_child(children[0])
            await pilot.pause()
            reopened = app.query_one(ChildView)
            await reopened.on_chat_input_submitted(
                ChatInput.Submitted("deliver exactly once")
            )
        finally:
            release.set()
        await delivery
        assert sends == ["deliver exactly once"]
        assert reopened.query_one("#child-input", ChatInput).text == ""
        assert reopened.child.started_at == children[0].started_at


@pytest.mark.asyncio
async def test_repeated_enter_during_delivery_is_not_queued_twice(
    child_app: ChildApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, children = child_app
    started, release = Event(), Event()
    sends: list[str] = []

    def send(child, owner, branch, message, **kwargs) -> str:
        sends.append(message)
        started.set()
        assert release.wait(10)
        return "Sent"

    monkeypatch.setattr("gptme.tui.child_view.send_child_message", send)
    app = GptmeApp(manager)
    async with app.run_test() as pilot:
        app.open_child(children[0])
        await pilot.pause()
        editor = app.query_one("#child-input", ChatInput)
        editor.text = "deliver once"
        editor.post_message(events.Key("enter", None))
        assert await asyncio.to_thread(started.wait, 5)
        try:
            # Dispatch through Textual, not a concurrent direct handler call.
            await editor._on_key(events.Key("enter", None))
        finally:
            release.set()
        await pilot.pause()
        assert sends == ["deliver once"]


@pytest.mark.asyncio
async def test_ctrl_c_dismisses_overview_before_returning_from_child(
    child_app: ChildApp,
) -> None:
    from gptme.tui.app import WorkOverviewScreen

    manager, children = child_app
    app = GptmeApp(manager)
    async with app.run_test() as pilot:
        app.open_child(children[0])
        await pilot.pause()
        view = app.query_one(ChildView)
        app.action_show_work()
        await pilot.pause()
        assert isinstance(app.screen, WorkOverviewScreen)
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app.screen is app.default_screen
        assert app._child_view is view
        assert not app._interrupt_event.is_set()
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app._child_view is None
        assert app.is_running and not app._interrupt_event.is_set()
