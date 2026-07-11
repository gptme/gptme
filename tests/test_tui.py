"""Tests for the Textual TUI (requires the `tui` extra)."""

import pytest

pytest.importorskip("textual")

from textual.widgets import Collapsible

from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tui.app import (
    AssistantMessage,
    ChatInput,
    GptmeApp,
    InfoMessage,
    SystemMessage,
    UserMessage,
    _summarize,
)


def make_manager(tmp_path, msgs: list[Message] | None = None) -> LogManager:
    return LogManager(msgs or [], logdir=tmp_path / "test-conversation", lock=False)


def test_summarize():
    assert _summarize("hello\nworld") == "hello (2 lines)"
    assert _summarize("```stdout\nfoo\n```").startswith("stdout")
    long = "x" * 200
    assert len(_summarize(long)) < 100


@pytest.mark.asyncio
async def test_app_renders_history(tmp_path):
    manager = make_manager(
        tmp_path,
        [
            Message("system", "system prompt", hide=True),
            Message("user", "hello"),
            Message("assistant", "hi there!"),
            Message("system", "```stdout\ntool output\n```"),
        ],
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.query(UserMessage)) == 1
        assert len(app.query(AssistantMessage)) == 1
        # hidden system prompt not rendered; tool output is, collapsed
        assert len(app.query(SystemMessage)) == 1
        collapsible = app.query_one(Collapsible)
        assert collapsible.collapsed


@pytest.mark.asyncio
async def test_queue_while_generating(tmp_path):
    manager = make_manager(tmp_path, [Message("user", "hello")])
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # simulate a running generation
        app.generating = True
        inp = app.query_one("#input", ChatInput)
        inp.text = "queued prompt"
        await pilot.press("enter")
        await pilot.pause()
        assert app.prompt_queue == ["queued prompt"]
        queued = app.query(UserMessage)
        assert any("queued" in w.classes for w in queued)
        # not appended to the log while generating
        assert all(m.content != "queued prompt" for m in manager.log)


@pytest.mark.asyncio
async def test_toggle_outputs(tmp_path):
    manager = make_manager(
        tmp_path,
        [Message("system", "some output"), Message("system", "more output")],
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        collapsibles = list(app.query(Collapsible))
        assert all(c.collapsed for c in collapsibles)
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert all(not c.collapsed for c in collapsibles)
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert all(c.collapsed for c in collapsibles)


@pytest.mark.asyncio
async def test_slash_command_help(tmp_path):
    """Slash-commands route through the CLI command registry."""
    manager = make_manager(tmp_path)
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.text = "/help"
        await pilot.press("enter")
        await pilot.pause()
        infos = list(app.query(InfoMessage))
        assert infos, "expected /help output to be shown"


def test_complete_input_commands():
    """Completion reuses the CLI command registry and completers."""
    from gptme.tui.app import complete_input

    candidates = complete_input("/mod")
    assert "/model" in candidates
    assert all(c.startswith("/mod") for c in candidates)
    # no completions for regular text
    assert complete_input("hello") == []


@pytest.mark.asyncio
async def test_tab_completes_command(tmp_path):
    manager = make_manager(tmp_path)
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.focus()
        inp.text = "/mode"
        await pilot.press("tab")
        await pilot.pause()
        # completes towards /model (single candidate or common prefix)
        assert inp.text.startswith("/model")
        # tab must not switch focus away from the input
        assert app.focused is inp
