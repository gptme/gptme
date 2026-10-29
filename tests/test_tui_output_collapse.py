"""Output folding must bound wrapped rows without truncating retained content."""

import json
from io import StringIO
from pathlib import Path

import pytest

pytest.importorskip("textual")

from rich.console import Console
from textual.widgets import Collapsible, Static

from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tui.app import (
    ChatInput,
    ChatMarkdown,
    GptmeApp,
    SystemMessage,
    renderables_for_message,
)

LARGE_JSON = json.dumps(
    {"records": [{"label": f"record{i}", "value": "abcdefghij"} for i in range(100)]}
)
MULTILINE_OUTPUT = "Command completed\nfirst output row\nlast output row"


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [40, 120])
@pytest.mark.parametrize("toggle", ["keyboard", "command"])
@pytest.mark.parametrize(
    "content", [LARGE_JSON, MULTILINE_OUTPUT], ids=["json", "multiline"]
)
async def test_fullscreen_output_folds_and_preserves_content(
    tmp_path: Path, width: int, toggle: str, content: str
) -> None:
    message = Message("system", content)
    manager = LogManager([message], logdir=tmp_path / "conversation", lock=False)
    app = GptmeApp(manager, workspace=tmp_path)

    async with app.run_test(size=(width, 30)) as pilot:
        await app.wait_rendered()
        await pilot.pause()
        output = app.query_one(SystemMessage)
        assert output.size.height <= 5, "collapsed output must occupy only a few rows"
        block = output.query_one(Collapsible)
        assert block.collapsed
        collapsed_height = output.size.height

        for expanded in (True, False):
            if toggle == "keyboard":
                await pilot.press("ctrl+o")
            else:
                inp = app.query_one("#input", ChatInput)
                inp.text = f"/display outputs {'on' if expanded else 'off'}"
                await pilot.press("enter")
            await pilot.pause()
            assert block.collapsed is not expanded
            if expanded:
                markdown = output.query_one(ChatMarkdown)
                await markdown.rendered.wait()
                if content == LARGE_JSON:
                    assert markdown.source == content
                    assert output.size.height > collapsed_height
                else:
                    # The short first line is already present in the title.
                    assert markdown.source == "first output row\nlast output row"
            else:
                assert output.size.height == collapsed_height
            assert message.content == content
            assert manager.log[0].content == content


def _render_inline(message: Message, width: int, expanded: bool) -> str:
    stream = StringIO()
    console = Console(file=stream, width=width, color_system=None)
    for renderable in renderables_for_message(message, expanded=expanded):
        console.print(renderable)
    return stream.getvalue()


@pytest.mark.parametrize("width", [40, 120])
@pytest.mark.parametrize(
    "content", [LARGE_JSON, MULTILINE_OUTPUT], ids=["json", "multiline"]
)
def test_inline_output_folds_and_preserves_content(width: int, content: str) -> None:
    message = Message("system", content)
    collapsed = _render_inline(message, width, expanded=False)
    assert len(collapsed.splitlines()) <= 5
    assert "record99" not in collapsed
    assert "last output row" not in collapsed

    expanded = _render_inline(message, width, expanded=True)
    # Rich wraps the paragraph and adds indentation; every original character
    # other than whitespace must survive the expanded display.
    assert "".join(content.split()) in "".join(expanded.split())
    assert _render_inline(message, width, expanded=False) == collapsed
    assert message.content == content


@pytest.mark.asyncio
async def test_short_one_line_output_stays_visible(tmp_path: Path) -> None:
    content = "cwd is /x"
    message = Message("system", content)
    manager = LogManager([message], logdir=tmp_path / "conversation", lock=False)
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test(size=(40, 30)) as pilot:
        await app.wait_rendered()
        await pilot.pause()
        output = app.query_one(SystemMessage)
        assert not output.query(Collapsible)
        assert content in str(output.query_one(Static).render())
        assert output.size.height <= 3

    assert content in _render_inline(message, 40, expanded=False)
    assert _render_inline(message, 40, expanded=False) == _render_inline(
        message, 40, expanded=True
    )
    assert message.content == content
