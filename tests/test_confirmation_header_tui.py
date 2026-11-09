"""Non-editable destination metadata in the native Textual approval surface."""

import pytest

pytest.importorskip("textual")

from textual.app import App
from textual.widgets import Static

from gptme.tools.base import ToolUse
from gptme.tui.app import ConfirmScreen


@pytest.mark.asyncio
async def test_tui_confirmation_header_is_literal_and_separate() -> None:
    header = "Scope: user\nDestination: /memory/[red]literal[/bold].md"
    content = "Preference body"
    screen = ConfirmScreen(
        ToolUse("memory", [], content, confirmation_header=header), content
    )
    app: App[None] = App()
    async with app.run_test() as pilot:
        await app.push_screen(screen)
        await pilot.pause()
        displayed = screen.query_one("#confirm-header", Static)
        assert str(displayed.render()) == header
        assert screen.preview_content == content
        ids = [child.id for child in screen.query_one("#confirm-dialog").children]
        assert ids.index("confirm-header") < ids.index("confirm-preview")
