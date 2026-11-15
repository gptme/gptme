"""Child code is literal text, not Textual markup (including truncated tags)."""

import json
from pathlib import Path

import pytest

pytest.importorskip("textual")

from textual.widgets import Static

from gptme.logmanager import LogManager
from gptme.tui.app import GptmeApp, WorkOverviewScreen, WorkScreen


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code", ['[a="', "[a=\\nfoo", "[bold]literal[/bold]", "x" * 497 + '[a="']
)
async def test_open_child_with_markup_like_code(tmp_path: Path, code: str) -> None:
    """Exercise selection/opening, not only the transcript string builder."""
    child = tmp_path / "child"
    child.mkdir()
    (child / "conversation.jsonl").write_text(
        json.dumps({"role": "assistant", "content": code}) + "\n"
    )
    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        overview = WorkOverviewScreen(manager.logdir, manager.current_branch)
        app.push_screen(overview)
        await pilot.pause()
        # The same screen opened by overview selection; literal code must mount.
        app.push_screen(WorkScreen(child, "child"))
        await pilot.pause()
        widget = app.screen.query_one("#work-screen-content", Static)
        assert code[:500] in str(widget.render())
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is overview
        await pilot.press("escape")
        await pilot.pause()
        assert app.manager is manager
