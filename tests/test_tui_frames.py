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
    layout: str = ""  # the chat's items and their regions, for failure output


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
            layout = " ".join(
                f"{type(w).__name__}@{w.region.y}+{w.region.height}"
                + ("" if w.display else "(hidden)")
                for w in chat_widget.children
            ) + (
                f" | scroll={chat_widget.scroll_offset.y}/{chat_widget.max_scroll_y}"
                f" region={region.y}+{region.height}"
                f" virtual={chat_widget.virtual_size.height}"
            )
            frames.append(Frame(time.monotonic(), app.state, lines, chat, rows, layout))

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
    Two items trading places (a queued prompt delivered above the tool
    status line) is a reorder, not a shrink: it doesn't count.
    """
    problems = []
    for i in range(1, len(frames)):
        before, after = frames[i - 1].rows, frames[i].rows
        both = before.keys() & after.keys()
        for key in both:
            (y0, name), (y1, _) = before[key], after[key]
            passed = any(
                before[k][0] > y0 and after[k][0] < y1 for k in both if k != key
            )
            if y1 > y0 and not passed:
                problems.append((i, f"{name} moved down {y1 - y0} rows"))
    return problems


def describe(frames: list[Frame], problems: list[tuple[int, str]]) -> str:
    """The problems, with the first one's frame and the one before it."""
    lines = [f"frame {i} ({frames[i].state}): {what}" for i, what in problems]
    i = problems[0][0]
    for j in (i - 2, i - 1, i):
        if j >= 0:
            lines.append(f"--- frame {j} ({frames[j].state}) {frames[j].layout[-400:]}")
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
        # submitted directly, not typed: a multi-line input box shrinking on
        # submit is its own effect (test_multiline_submit_...)
        await app._submit(prompt)
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


@pytest.mark.asyncio
async def test_prompt_queued_during_tool_waits_below_it_then_joins(app_factory):
    """A prompt queued while a tool runs sits at the bottom, below the
    running tool, and becomes a message in place at the next step: the tool's
    output and the queue meet in the middle, nothing jumps around."""
    from gptme.tui.app import SystemMessage, ToolPlaceholder, UserMessage

    app = app_factory(steps=2)  # step 2 answers the queued prompt
    prompt = PROSE + "\n\n```shell\nsleep 0.5; echo all pass\n```"
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        frames = record_frames(app)
        inp = app.query_one("#input", ChatInput)
        await app._submit(prompt)
        deadline = time.monotonic() + 20
        while not app.state.startswith("running"):
            assert time.monotonic() < deadline, "tool did not start"
            await asyncio.sleep(0.02)
        inp.text = "also check the docs"
        await pilot.press("enter")
        await pilot.pause()
        await asyncio.sleep(0.1)
        await pilot.pause()

        chat = app.query_one("#chat").children
        kinds = [type(w).__name__ for w in chat if w.display]
        queued = [
            w for w in chat if isinstance(w, UserMessage) and w.has_class("queued")
        ]
        assert queued, kinds
        tool_line = [w for w in chat if isinstance(w, ToolPlaceholder)]
        assert tool_line, kinds
        assert list(chat).index(queued[0]) > list(chat).index(tool_line[0]), kinds

        while app.generating or app.state != "idle":
            assert time.monotonic() < deadline, "turn did not finish"
            await asyncio.sleep(0.05)
        await pilot.pause()
        await asyncio.sleep(0.3)
        await pilot.pause()

        from gptme.tui.app import InfoMessage

        tail = [
            w
            for w in app.query_one("#chat").children
            if w.display and not isinstance(w, InfoMessage)  # e.g. max steps
        ][-5:]
        assert [type(w).__name__ for w in tail] == [
            "UserMessage",
            "AssistantMessage",
            "SystemMessage",
            "UserMessage",
            "AssistantMessage",
        ]
        assert not tail[3].has_class("queued")
        assert "also check the docs" in tail[3].content
        assert "also check the docs" in tail[4].content
        assert isinstance(tail[2], SystemMessage)
    problems = sorted(blinks(frames) + jumps(frames))
    assert not problems, describe(frames, problems)
