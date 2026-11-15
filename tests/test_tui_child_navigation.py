"""Child inspection uses normal widgets without rebinding the parent's session."""

import json
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

pytest.importorskip("textual")
from textual.widgets import Collapsible, Static
from textual.widgets.text_area import Selection

from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tools.subagent import SubagentSnapshot
from gptme.tui.app import AssistantMessage, ChatInput, ChatScroll, GptmeApp
from gptme.tui.child_view import ChildView, read_child_messages, send_child_message


def snapshot(path: Path) -> SubagentSnapshot:
    return SubagentSnapshot(
        run_id=str(path),
        agent_id="child[broken]",
        status="running",
        started_at=1,
        elapsed_s=0,
        execution_mode="thread",
        prompt_preview="task",
        logdir=path,
    )


def test_snapshot_partial_tail_and_bounds(tmp_path: Path) -> None:
    path = tmp_path / "conversation.jsonl"
    content = "```python\n" + "print('not truncated')\n" * 100 + "```"
    path.write_text(
        json.dumps({"role": "assistant", "content": content})
        + '\n[1,2]\n{"role":"assistant","content":"unfinished'
    )
    messages, error = read_child_messages(tmp_path)
    assert error is None
    assert len(messages) == 1
    assert messages[0].content == content
    assert not (tmp_path / ".lock").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True])
async def test_child_main_view_preserves_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inline: bool,
) -> None:
    monkeypatch.setattr("gptme.tui.app.maybe_start_auto_naming", lambda *a, **kw: None)
    child_dir = tmp_path / "child"
    child_dir.mkdir()
    child = snapshot(child_dir)
    monkeypatch.setattr(
        "gptme.tools.subagent.subagent_snapshots", lambda *a, **kw: [child]
    )
    content = "Child [arbitrary] prose\n```shell\necho child\n```"
    path = child_dir / "conversation.jsonl"
    path.write_text(
        json.dumps(
            {
                "role": "assistant",
                "content": content,
                "metadata": {"model": "child-model"},
            }
        )
        + "\n"
    )
    manager = LogManager(
        [Message("user", "parent")], logdir=tmp_path / "parent", lock=False
    )
    app = GptmeApp(manager, workspace=tmp_path, inline=inline)
    context = app._chat_ctx
    async with app.run_test(size=(100, 35)) as pilot:
        await pilot.pause()
        editor = app.query_one("#input", ChatInput)
        editor.text = "parent draft"
        selection = Selection((0, 2), (0, 5))
        editor.selection = selection
        parent_widgets = (
            list(app.query_one("#chat", ChatScroll).children) if not inline else []
        )
        app.generating = True  # prove child Ctrl+C never interrupts parent
        app.open_child(child)
        await pilot.pause()
        view = cast(ChildView, app._child_view)
        assert view is not None
        assert app.screen is app.default_screen
        assert view.query_one(AssistantMessage).content == content
        assert "child-model" in str(view.query_one("#child-banner", Static).content)
        assert view.query(Collapsible)
        view.query_one("#child-input", ChatInput).text = "child draft"
        # Refresh must retain an existing message's expansion state.
        block = view.query_one(Collapsible)
        block.collapsed = False
        with path.open("a") as stream:
            stream.write(json.dumps({"role": "user", "content": "live update"}) + "\n")
        await view.refresh_snapshot()
        assert block.is_attached and not block.collapsed
        assert app.manager is manager and app._chat_ctx is context
        assert editor.text == "parent draft" and editor.selection == selection
        assert not app._interrupt_event.is_set()
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app._child_view is None
        assert app.generating
        assert not app._interrupt_event.is_set()
        assert editor.text == "parent draft" and editor.selection == selection
        if not inline:
            assert list(app.query_one("#chat", ChatScroll).children) == parent_widgets
        app.generating = False


def test_routing_rejects_reused_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gptme.tools.subagent.types import _subagents

    child = snapshot(tmp_path / "old")
    monkeypatch.setattr(
        "gptme.tools.subagent.subagent_snapshots", lambda *a, **kw: [child]
    )
    steer = Mock()
    monkeypatch.setattr("gptme.tools.subagent.subagent_steer", steer)
    monkeypatch.setattr("gptme.tools.subagent.types._subagents", [])
    with pytest.raises(ValueError, match="no longer"):
        send_child_message(child, tmp_path, "main", "test")
    steer.assert_not_called()
    assert _subagents is not None


@pytest.mark.asyncio
async def test_stopped_composer_requires_explicit_continue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("gptme.tui.app.maybe_start_auto_naming", lambda *a, **kw: None)
    child = replace(snapshot(tmp_path), status="success")
    monkeypatch.setattr(
        "gptme.tools.subagent.subagent_snapshots", lambda *a, **kw: [child]
    )
    route = Mock(side_effect=ValueError("writer lease held"))
    monkeypatch.setattr("gptme.tui.child_view.send_child_message", route)
    app = GptmeApp(
        LogManager([], logdir=tmp_path / "parent", lock=False), workspace=tmp_path
    )
    async with app.run_test() as pilot:
        app.open_child(child)
        await pilot.pause()
        view = cast(ChildView, app._child_view)
        editor = view.query_one("#child-input", ChatInput)
        editor.text = "do more"
        await pilot.press("enter")
        route.assert_not_called()
        assert editor.text == "do more"
        editor.text = "/continue do more"
        await pilot.press("enter")
        await pilot.pause()
        route.assert_called_once()
        assert route.call_args.kwargs == {"continue_finished": True}
        assert "writer lease held" in str(
            view.query_one("#child-feedback", Static).content
        )
        assert editor.text == "/continue do more"
        assert not manager_messages(app)


def manager_messages(app: GptmeApp) -> list[Message]:
    return list(app.manager.log)


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True])
async def test_parent_finishes_behind_child_and_child_draft_returns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inline: bool,
) -> None:
    monkeypatch.setattr("gptme.tui.app.maybe_start_auto_naming", lambda *a, **kw: None)
    child = snapshot(tmp_path)
    monkeypatch.setattr(
        "gptme.tools.subagent.subagent_snapshots", lambda *a, **kw: [child]
    )
    (tmp_path / "conversation.jsonl").write_text(
        json.dumps({"role": "system", "content": "[red]bad[/green]"}) + "\n"
    )
    app = GptmeApp(
        LogManager([], logdir=tmp_path / "parent", lock=False),
        workspace=tmp_path,
        inline=inline,
    )
    async with app.run_test() as pilot:
        app.open_child(child)
        await pilot.pause()
        view = cast(ChildView, app._child_view)
        editor = view.query_one("#child-input", ChatInput)
        editor.text = "remember child draft"
        editor.selection = Selection((0, 1), (0, 4))
        app.generating = True
        app._begin_stream()
        app._on_stream_token("Parent response behind child")
        msg = Message("assistant", "Parent response behind child")
        app.manager.append(msg)
        app._on_step_message(msg)
        await app._generation_done()
        await pilot.pause()
        assert app._child_view is view and editor.has_focus
        assert not app.generating
        view.update_parent_status()
        assert "1 new messages" in str(
            view.query_one("#child-parent-status", Static).content
        )
        app.close_child()
        await pilot.pause()
        app.open_child(child)
        await pilot.pause()
        restored = app.query_one("#child-input", ChatInput)
        assert restored.text == "remember child draft"
        assert restored.selection == Selection((0, 1), (0, 4))


def test_expected_run_guard_rejects_replacement_at_queue_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gptme.tools.subagent import Subagent, api
    from gptme.tools.subagent.types import ReturnType

    old = Subagent(
        agent_id="reused",
        prompt="old",
        logdir=tmp_path / "old",
        thread=None,
        model="test",
    )
    newer = Subagent(
        agent_id="reused",
        prompt="new",
        logdir=tmp_path / "new",
        thread=None,
        model="test",
    )
    registry = [old]
    monkeypatch.setattr(api, "_subagents", registry)
    monkeypatch.setattr(api, "_find_subagent", lambda _: old)
    queued = Mock()
    monkeypatch.setattr("gptme.prompt_queue.queue_prompt", queued)

    def status(self: Subagent) -> ReturnType:
        registry[:] = [newer]
        return ReturnType("running")

    monkeypatch.setattr(Subagent, "status", status)
    with pytest.raises(ValueError, match="changed before steering"):
        api.subagent_steer(
            "reused", "do not misroute", expected_run=(old.logdir, old.started_at)
        )
    queued.assert_not_called()
    with pytest.raises(ValueError, match="Selected subagent run changed"):
        api.subagent_continue(
            "reused", "do not resume", expected_run=(newer.logdir, newer.started_at)
        )
