"""Regression coverage for the reply visible during tool execution."""

from pathlib import Path

import pytest

pytest.importorskip("textual")

from rich.console import Console
from textual.widgets import Static

from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tools import init_tools
from gptme.tui.app import (
    AssistantMessage,
    GptmeApp,
    StreamingMessage,
    ToolPlaceholder,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [True, False])
async def test_reply_visible_before_tools_and_not_duplicated(tmp_path, streamed):
    """The completed reply remains mounted until and after the step yields it."""
    init_tools()
    manager = LogManager([], logdir=tmp_path / "conversation", lock=False)
    app = GptmeApp(manager, workspace=tmp_path)
    msg = Message("assistant", "Let me check.\n```shell\necho hello\n```")

    async with app.run_test() as pilot:
        await pilot.pause()
        if streamed:
            app._begin_stream()
            app._on_token(msg.content)
            app._flush_stream()
            await pilot.pause()
            assert len(app.query(StreamingMessage)) == 1

        # This callback runs before execute_msg; _on_step_message has not fired.
        app._on_before_tool_execution(msg)
        await pilot.pause()
        assert len(app.query(StreamingMessage)) == 0
        assert len(app.query(AssistantMessage)) == 1
        reply = app.query_one(AssistantMessage)
        assert reply.content == msg.content
        assert len(app.query(ToolPlaceholder)) == 1
        assert app.state == "executing tools"

        # A delayed stream flush must not resurrect the preview over the reply.
        app._flush_stream()
        await pilot.pause()
        assert len(app.query(StreamingMessage)) == 0
        assert app.query_one(AssistantMessage) is reply

        # The same assistant message is yielded after tool execution finishes.
        app._on_step_message(msg)
        await pilot.pause()
        assert len(app.query(AssistantMessage)) == 1
        assert app.query_one(AssistantMessage) is reply
        assert len(app.query(ToolPlaceholder)) == 1

        app._on_step_message(Message("system", "```stdout\nhello\n```"))
        app._begin_stream()
        await pilot.pause()
        assert len(app.query(ToolPlaceholder)) == 0
        app._on_step_message(Message("assistant", "Done."))
        await pilot.pause()
        assert len(app.query(AssistantMessage)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [True, False])
async def test_inline_reply_visible_before_tools_and_not_duplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, streamed: bool
) -> None:
    """Print the reply before tools and keep pending tokens off the indicator."""
    init_tools()
    manager = LogManager([], logdir=tmp_path / "conversation", lock=False)
    app = GptmeApp(manager, workspace=tmp_path, inline=True)
    console = Console(record=True, width=100)
    monkeypatch.setattr(app, "_print_above", lambda *items: console.print(*items))
    msg = Message("assistant", "Let me check.\n```shell\necho hello\n```")

    async with app.run_test() as pilot:
        await pilot.pause()
        app._begin_stream()
        if streamed:
            app._on_stream_token("Let me ")
            # Queue the last chunk, but don't deliver the scheduled flush yet.
            app._on_token("check.\n```shell\necho hello\n```")

        app._on_before_tool_execution(msg)
        before_tools = console.export_text(clear=False)
        live = app.query_one("#live", Static)
        assert "Let me check." in before_tools
        assert "echo hello" in before_tools
        assert str(live.render()) == "Running tool…"
        assert app.state == "executing tools"

        app._flush_stream()
        await pilot.pause()
        assert str(live.render()) == "Running tool…"
        assert console.export_text(clear=False) == before_tools

        app._on_step_message(msg)
        await pilot.pause()
        assert console.export_text(clear=False) == before_tools
        assert str(live.render()) == "Running tool…"

        app._on_step_message(Message("system", "```stdout\nhello\n```"))
        app._begin_stream()
        app._on_step_message(Message("assistant", "Done."))
        await pilot.pause()
        output = console.export_text(clear=False)
        assert output.count("Let me check.") == 1
        assert "Done." in output
