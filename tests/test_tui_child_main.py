"""Independent Pilot acceptance tests for inspecting a child in the main view."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

pytest.importorskip("textual")

from rich.syntax import Syntax
from textual.widgets import Collapsible, Static
from textual.widgets.text_area import Selection

from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tools.subagent import SubagentSnapshot
from gptme.tui.app import AssistantMessage, ChatInput, ChatScroll, GptmeApp
from gptme.tui.child_view import ChildView


@pytest.fixture(autouse=True)
def isolated_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    # User config/providers are isolated by conftest; don't start an LLM namer.
    monkeypatch.setattr("gptme.tui.app.maybe_start_auto_naming", lambda *a, **kw: None)
    monkeypatch.setattr("gptme.tui.app.get_default_model", lambda: None)


def owned_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: str = "running",
) -> tuple[LogManager, SubagentSnapshot]:
    manager = LogManager(
        [Message("user", "Parent transcript must survive inspection")],
        logdir=tmp_path / "parent",
        lock=False,
    )
    child_dir = tmp_path / "child"
    child_dir.mkdir()
    child = SubagentSnapshot(
        run_id=str(child_dir),
        agent_id="code-reviewer",
        status=status,
        started_at=123.0,
        elapsed_s=4,
        execution_mode="thread",
        prompt_preview="Review the code",
        logdir=child_dir,
    )

    def snapshots(
        parent_logdir: Path, *, parent_branch: str | None = None
    ) -> list[SubagentSnapshot]:
        # A synthetic registry still enforces the owning parent/branch contract.
        if (
            parent_logdir.resolve() == manager.logdir.resolve()
            and parent_branch == manager.current_branch
        ):
            return [child]
        return []

    monkeypatch.setattr("gptme.tools.subagent.subagent_snapshots", snapshots)
    (child_dir / "conversation.jsonl").write_text(
        json.dumps({"role": "user", "content": "Child-only task"}) + "\n"
    )
    return manager, child


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True], ids=["fullscreen", "inline"])
@pytest.mark.parametrize("back_key", ["ctrl+c", "escape"])
async def test_child_back_preserves_parent_editor_and_widget_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline: bool, back_key: str
) -> None:
    manager, child = owned_child(tmp_path, monkeypatch)
    app = GptmeApp(manager, workspace=tmp_path, inline=inline)
    context = app._chat_ctx
    original_messages = list(manager.log)
    async with app.run_test(size=(100, 35)) as pilot:
        await pilot.pause()
        editor = app.query_one("#input", ChatInput)
        editor.text = "unfinished parent draft\nsecond line"
        selection = Selection((0, 4), (1, 6))
        editor.selection = selection
        parent_widgets = tuple(app.default_screen.walk_children())
        chat_widgets = (
            tuple(app.query_one("#chat", ChatScroll).children) if not inline else ()
        )
        app.generating = True
        try:
            app.open_child(child)
            await pilot.pause()
            view = app.query_one(ChildView)
            assert app.screen is app.default_screen
            assert app.focused is view.query_one("#child-input", ChatInput)
            assert all(widget.is_attached for widget in parent_widgets)
            view.query_one("#child-input", ChatInput).text = "unsubmitted child draft"
            assert editor.text == "unfinished parent draft\nsecond line"
            assert editor.selection == selection
            await pilot.press(back_key)
            await pilot.pause()
            assert app._child_view is None
            assert not view.is_attached
            assert app.manager is manager
            assert app._chat_ctx is context
            assert list(manager.log) == original_messages
            assert tuple(app.default_screen.walk_children()) == parent_widgets
            if not inline:
                assert (
                    tuple(app.query_one("#chat", ChatScroll).children) == chat_widgets
                )
            assert app.query_one("#input", ChatInput) is editor
            assert editor.text == "unfinished parent draft\nsecond line"
            assert editor.selection == selection
            assert app.focused is editor
            assert app.generating
            assert not app._interrupt_event.is_set()
        finally:
            app.generating = False


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True], ids=["fullscreen", "inline"])
async def test_locked_child_renders_complete_code_without_loading_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline: bool
) -> None:
    fcntl = pytest.importorskip("fcntl")
    manager, child = owned_child(tmp_path, monkeypatch)
    code = "\n".join(f"print('child line {i:03d}')" for i in range(70))
    code += "\nprint('UNTRUNCATED_CHILD_END')"
    content = f"Child prose before code\n```shell\n{code}\n```\nChild final prose"
    assert content.index("UNTRUNCATED_CHILD_END") > 500
    transcript = child.logdir / "conversation.jsonl"
    transcript.write_text(
        json.dumps({"role": "assistant", "content": content})
        + '\n{"role":"assistant","content":"PARTIAL_TAIL_MUST_NOT_RENDER'
    )
    original_bytes = transcript.read_bytes()
    lock_path = child.logdir / ".lock"
    lock_path.write_text("live child writer")
    original_inode = lock_path.stat().st_ino
    load = Mock(side_effect=AssertionError("Inspection must not call LogManager.load"))
    monkeypatch.setattr(LogManager, "load", load)
    app = GptmeApp(manager, workspace=tmp_path, inline=inline)
    # Hold a real writer lock throughout mounting, refreshing and closing.
    with lock_path.open("r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        async with app.run_test(size=(100, 35)) as pilot:
            app.open_child(child)
            await pilot.pause()
            view = app.query_one(ChildView)
            message = view.query_one(AssistantMessage)
            assert message.content == content
            assert len(view.query(AssistantMessage)) == 1
            block = message.query_one(Collapsible)
            block.collapsed = False
            await pilot.pause()
            syntax = [
                widget.content
                for widget in block.query(Static)
                if isinstance(widget.content, Syntax)
            ]
            assert len(syntax) == 1
            assert syntax[0].code.strip() == code
            assert "UNTRUNCATED_CHILD_END" in syntax[0].code
            await view.refresh_snapshot()
            assert view.query_one(AssistantMessage) is message
            assert message.query_one(Collapsible) is block
            assert not block.collapsed
            app.close_child()
            await pilot.pause()
            load.assert_not_called()
            assert transcript.read_bytes() == original_bytes
            assert lock_path.stat().st_ino == original_inode
            assert lock_path.read_text() == "live child writer"
            # A second descriptor must still fail to acquire the writer lease.
            with (
                lock_path.open("r+") as contender,
                pytest.raises(BlockingIOError),
            ):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True], ids=["fullscreen", "inline"])
@pytest.mark.parametrize("status", ["running", "success"])
async def test_child_submit_is_literal_and_continue_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline: bool, status: str
) -> None:
    manager, child = owned_child(tmp_path, monkeypatch, status=status)
    routes: list[tuple[SubagentSnapshot, Path, str, str, bool]] = []

    def record_send(
        target: SubagentSnapshot,
        owner: Path,
        branch: str,
        text: str,
        *,
        continue_finished: bool = False,
    ) -> str:
        routes.append((target, owner, branch, text, continue_finished))
        return "Recorded child delivery"

    monkeypatch.setattr("gptme.tui.child_view.send_child_message", record_send)
    app = GptmeApp(manager, workspace=tmp_path, inline=inline)
    original_messages = list(manager.log)
    async with app.run_test(size=(100, 35)) as pilot:
        parent_editor = app.query_one("#input", ChatInput)
        parent_editor.text = "parent draft stays here"
        app.open_child(child)
        await pilot.pause()
        view = app.query_one(ChildView)
        child_editor = view.query_one("#child-input", ChatInput)
        # If this bubbled into the parent's slash dispatcher it would exit.
        child_editor.text = "/quit"
        await pilot.press("enter")
        await pilot.pause()
        assert app.is_running
        assert app._child_view is view
        if status == "running":
            assert routes == [
                (child, manager.logdir, manager.current_branch, "/quit", False)
            ]
            assert child_editor.text == ""
        else:
            assert routes == []
            assert child_editor.text == "/quit"
            assert "/continue" in str(view.query_one("#child-feedback", Static).content)
            child_editor.text = "/continue finish the child task"
            await pilot.press("enter")
            await pilot.pause()
            assert routes == [
                (
                    child,
                    manager.logdir,
                    manager.current_branch,
                    "finish the child task",
                    True,
                )
            ]
            assert child_editor.text == ""
        assert list(manager.log) == original_messages
        assert not app.prompt_queue
        assert parent_editor.text == "parent draft stays here"
        assert not app.generating
        assert not app._interrupt_event.is_set()
        app.close_child()
        await pilot.pause()
        assert app.focused is parent_editor
