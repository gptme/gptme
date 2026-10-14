"""Regression coverage for the reply visible during tool execution."""

import pytest

pytest.importorskip("textual")

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
