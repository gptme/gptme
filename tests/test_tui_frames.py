"""Frame-level tests for TUI smoothness: no blinks or jumps while a turn runs.

Textual's headless test driver still composes every frame; recording them
(via ``App._display``, which a real terminal driver would write out) shows
what the user would see, frame by frame. Glitches that last a single frame
(~16-60 ms) are invisible to ordinary widget assertions but very visible on
a real terminal.
"""

import asyncio
import time
from dataclasses import dataclass

import pytest

pytest.importorskip("textual")

from gptme.init import init
from gptme.llm.models import set_default_model
from gptme.logmanager import LogManager
from gptme.message import Message, set_output_format
from gptme.tools import init_tools
from gptme.tui.app import ChatInput, GptmeApp

SIZE = (100, 40)
# consecutive blank rows between content: message margins are 1 row
MAX_GAP = 2


@dataclass
class Frame:
    t: float
    state: str
    lines: list[str]  # the whole screen
    chat: list[str]  # the chat's rows (the input box grows and shrinks)
    # screen row of each visible chat item (message, stream, status line)
    rows: dict[int, tuple[int, str]]


def record_frames(app: GptmeApp) -> list[Frame]:
    """Record the screen each time Textual would write a frame to the terminal."""
    frames: list[Frame] = []
    display = app._display

    def recording_display(screen, renderable) -> None:
        display(screen, renderable)
        if renderable is not None and not app._batch_count and screen is app.screen:
            lines = [s.text for s in screen._compositor.render_strips()]
            chat_widget = app.query_one("#chat")
            region = chat_widget.region
            chat = lines[region.y : region.bottom]
            rows = {
                id(w): (w.region.y, type(w).__name__)
                for w in chat_widget.children
                if w.display and w.region.height
            }
            frames.append(Frame(time.monotonic(), app.state, lines, chat, rows))

    app._display = recording_display  # type: ignore[method-assign]
    return frames


def interior_gaps(lines: list[str]) -> list[tuple[int, int]]:
    """(start row, length) of blank runs with content above and below."""
    content = [i for i, line in enumerate(lines) if line.strip()]
    gaps = []
    for above, below in zip(content, content[1:]):
        if below - above - 1 > MAX_GAP:
            gaps.append((above + 1, below - above - 1))
    return gaps


def blinks(frames: list[Frame]) -> list[tuple[int, str]]:
    """Frames showing a hole in the chat: space laid out but not yet drawn."""
    problems = []
    for i, frame in enumerate(frames):
        for row, length in interior_gaps(frame.chat):
            problems.append((i, f"{length} blank rows at row {row}"))
    return problems


def jumps(frames: list[Frame]) -> list[tuple[int, str]]:
    """Chat items moving down between frames: something above them shrank.

    While the user hasn't scrolled, the chat is anchored at the bottom and
    only grows there, so an item on screen should stay put or move up.
    """
    problems = []
    for i in range(1, len(frames)):
        before, after = frames[i - 1].rows, frames[i].rows
        for key in before.keys() & after.keys():
            (y0, name), (y1, _) = before[key], after[key]
            if y1 > y0:
                problems.append((i, f"{name} moved down {y1 - y0} rows"))
    return problems


def describe(frames: list[Frame], problems: list[tuple[int, str]]) -> str:
    """The problems, with the first one's frame and the one before it."""
    lines = [f"frame {i} ({frames[i].state}): {what}" for i, what in problems]
    i = problems[0][0]
    for j in (i - 1, i):
        if j >= 0:
            lines.append(f"--- frame {j} ({frames[j].state})")
            lines += [f"|{line.rstrip()}" for line in frames[j].chat]
    return "\n".join(lines)


@pytest.fixture
def app_factory(tmp_path, monkeypatch):
    def make(steps: int = 1) -> GptmeApp:
        monkeypatch.setenv("GPTME_MAX_STEPS", str(steps))
        init(
            "mock/echo",
            interactive=True,
            tool_allowlist=["shell"],
            tool_format="markdown",
            no_confirm=True,
        )
        set_default_model("mock/echo")
        init_tools(["shell"])
        set_output_format("quiet")
        history = []
        for i in range(15):  # enough to overflow: the chat is anchored at the bottom
            history += [
                Message("user", f"earlier question {i}"),
                Message("assistant", f"earlier answer {i}\n\nwith a second paragraph"),
            ]
        manager = LogManager(history, logdir=tmp_path / "conv", lock=False)
        app = GptmeApp(manager, tool_format="markdown", workspace=tmp_path)
        app.auto_confirm = True
        return app

    return make


async def run_turn(app: GptmeApp, prompt: str) -> list[Frame]:
    """Submit a prompt (the mock model echoes it as its reply) and record the turn."""
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        frames = record_frames(app)
        app.query_one("#input", ChatInput).text = prompt
        await pilot.press("enter")
        deadline = time.monotonic() + 20
        while app.generating or app.state != "idle":
            assert time.monotonic() < deadline, "turn did not finish"
            await asyncio.sleep(0.05)
        await pilot.pause()
        await asyncio.sleep(0.3)  # let late renders land
        await pilot.pause()
    return frames


PROSE = (
    "Running the tests now.\n\n## Plan\n\n"
    + "\n".join(f"- step {i}: something to do" for i in range(8))
    + "\n\n"
    + "Some **bold** prose that wraps over a couple of lines in the chat area. " * 3
)
TOOL = "\n\n```shell\necho all pass\n```"
THINKING = "<think>\n" + "\n".join(f"thought {i}" for i in range(12)) + "\n</think>\n"

SCENARIOS = {
    "reply": (PROSE, 1),
    "tool": (PROSE + TOOL, 1),
    "two_steps": (PROSE + TOOL, 2),
    "thinking_tool": (THINKING + PROSE + TOOL, 1),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", list(SCENARIOS))
async def test_turn_has_no_blinks_or_jumps(app_factory, scenario):
    prompt, steps = SCENARIOS[scenario]
    frames = await run_turn(app_factory(steps), prompt)
    assert len(frames) >= 2
    problems = sorted(blinks(frames) + jumps(frames))
    assert not problems, describe(frames, problems)
