"""Performance benchmarks for TUI (Textual) latency, tracked over time.

Covers the TUI areas from issue #4373:
- Submit-to-user-message latency
- First-token-to-first-visible-text latency
- Per-chunk render fps for 1k / 10k / 50k char streamed replies
- Tool-start to status-line-visible latency
- History render latency for a long conversation (500 messages)

All tests use the mock/echo model so results are deterministic and fast.
Slow tests (with artificial streaming delay) are marked @pytest.mark.slow
and excluded from regular CI. Save named measurements in a JUnit report:

Run manually:
    pytest tests/test_perf_tui.py -v -m perf --junitxml=perf-tui.xml -o junit_family=legacy
    pytest tests/test_perf_tui.py -v -m "perf and slow" -s   # see fps numbers
"""

from __future__ import annotations

import asyncio
import time

import pytest

pytest.importorskip(
    "textual", reason="textual not installed; install with: uv sync --extra tui"
)

from gptme.init import init
from gptme.llm.models import set_default_model
from gptme.logmanager import LogManager
from gptme.message import Message, set_output_format
from gptme.tools import init_tools
from gptme.tui.app import ChatInput, GptmeApp

SIZE = (100, 40)


def _make_long_content(chars: int) -> str:
    """Deterministic prose without any tool-call syntax."""
    # Distinct words keep visible reply updates distinguishable after scrolling.
    # Newlines exercise on_token's line-buffered delivery during the stream,
    # rather than flushing one giant line only when generation completes.
    return " ".join(f"word{i:05d}\n" for i in range(chars // 10 + 1))[:chars]


def _record_stream_frames(app: GptmeApp) -> list[tuple[float, str]]:
    """Record changed reply text from frames actually composed for the terminal."""
    from gptme.tui.app import StreamingMessage

    frames: list[tuple[float, str]] = []
    display = app._display

    def recording_display(screen, renderable):
        display(screen, renderable)
        if renderable is None or app._batch_count or screen is not app.screen:
            return
        for widget in app.query(StreamingMessage):
            if not widget.display or not widget._buffer.strip():
                continue
            region = widget.region.intersection(app.query_one("#chat").region)
            strips = screen._compositor.render_strips()

            text = "\n".join(
                strip.crop(region.x, region.right).text
                for strip in strips[max(0, region.y) : region.bottom]
            ).strip()
            # The role label/placeholder alone is not a rendered reply token.
            if text and ("Echo:" in text or "word" in text):
                if not frames or frames[-1][1] != text:
                    frames.append((time.perf_counter(), text))

    app._display = recording_display  # type: ignore[method-assign]
    return frames


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tui_app_factory(tmp_path, monkeypatch):
    """Factory for GptmeApp instances backed by the mock/echo model."""

    def make(n_history: int = 15, steps: int = 1) -> GptmeApp:
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
        history: list[Message] = []
        for i in range(n_history):
            history += [
                Message("user", f"earlier question {i}"),
                Message(
                    "assistant",
                    f"earlier answer {i}\n\nwith a second paragraph",
                ),
            ]
        manager = LogManager(history, logdir=tmp_path / "conv", lock=False)
        app = GptmeApp(manager, tool_format="markdown", workspace=tmp_path)
        app.auto_confirm = True
        return app

    return make


# ---------------------------------------------------------------------------
# Latency benchmarks
# ---------------------------------------------------------------------------


@pytest.mark.perf
@pytest.mark.asyncio
async def test_tui_submit_to_user_message_latency(tui_app_factory, record_property):
    """Time from pilot.press('enter') to the submitted UserMessage widget appearing."""
    from gptme.tui.app import UserMessage

    app = tui_app_factory()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.text = "ping-perf"
        t0 = time.perf_counter()
        # Pilot.press waits for idle after dispatch; observe concurrently so
        # its own synchronization delay is not counted as rendering latency.
        press = asyncio.create_task(pilot.press("enter"))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            msgs = [
                w
                for w in app.query(UserMessage)
                if "ping-perf" in w.content and w.display
            ]
            if msgs:
                break
            await asyncio.sleep(0.005)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        await press
        record_property("elapsed_ms", elapsed_ms)
        assert msgs, "user message never appeared on screen"
        # UserMessage should appear within 200ms (render pipeline, not LLM)
        assert elapsed_ms < 200, f"submit-to-user-visible: {elapsed_ms:.1f}ms"


@pytest.mark.perf
@pytest.mark.asyncio
async def test_tui_first_token_visible_latency(
    tui_app_factory, monkeypatch, record_property
):
    """Submit to the first frame containing text from the new streaming reply."""
    from gptme.llm import llm_mock

    original_stream = llm_mock.stream

    def slow_stream(*args, **kwargs):
        words = original_stream(*args, **kwargs)
        try:
            while True:
                word = next(words)
                time.sleep(0.05)
                yield word
        except StopIteration as done:
            return done.value

    monkeypatch.setattr(llm_mock, "stream", slow_stream)
    app = tui_app_factory()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        frames = _record_stream_frames(app)
        t0 = time.perf_counter()
        await app._submit("hello world\n" * 10)
        deadline = time.monotonic() + 15
        while not frames and time.monotonic() < deadline:
            await asyncio.sleep(0.005)

        assert frames, "new streaming reply never rendered any text"
        elapsed_ms = (frames[0][0] - t0) * 1000
        record_property("elapsed_ms", elapsed_ms)
        # Wait for turn to finish before tearing down.
        finish = time.monotonic() + 15
        while app.generating or app.state != "idle":
            assert time.monotonic() < finish, "turn did not finish"
            await asyncio.sleep(0.05)

        # With mock/echo the first token should be visible within 2s.
        assert elapsed_ms < 2000, f"first-token-visible: {elapsed_ms:.1f}ms"


@pytest.mark.perf
@pytest.mark.asyncio
async def test_tui_tool_status_visible_latency(tui_app_factory, record_property):
    """Time from submit (with a tool call) to the ToolPlaceholder status line appearing."""
    from gptme.tui.app import ToolPlaceholder

    tool_content = "checking something\n\n```shell\necho status\n```"
    app = tui_app_factory()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        t0 = time.perf_counter()
        await app._submit(tool_content)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            placeholders = [w for w in app.query(ToolPlaceholder) if w.display]
            if placeholders:
                break
            await asyncio.sleep(0.01)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        finish = time.monotonic() + 15
        while (app.generating or app.state != "idle") and time.monotonic() < finish:
            await asyncio.sleep(0.05)

        record_property("elapsed_ms", elapsed_ms)
        assert placeholders, "tool status placeholder never appeared"
        assert elapsed_ms < 3000, f"tool-status-visible: {elapsed_ms:.1f}ms"


# ---------------------------------------------------------------------------
# Render throughput (slow — artificial streaming delay)
# ---------------------------------------------------------------------------


@pytest.mark.perf
@pytest.mark.slow
@pytest.mark.asyncio
@pytest.mark.parametrize("reply_chars", [1_000, 10_000, 50_000])
async def test_tui_render_fps_during_stream(
    tui_app_factory, monkeypatch, record_property, reply_chars: int
):
    """Measure frames-per-second while a long reply streams in.

    The mock/echo model echoes the prompt; we submit a message of `reply_chars`
    characters and count frames recorded during the live turn.  A 2ms per-word
    delay makes the stream slow enough that multiple render frames are drawn.
    """
    from gptme.llm import llm_mock

    original_stream = llm_mock.stream

    def slow_stream(*args, **kwargs):
        words = original_stream(*args, **kwargs)
        try:
            while True:
                word = next(words)
                time.sleep(0.002)  # 2ms/word ≈ 500 words/s
                yield word
        except StopIteration as done:
            return done.value

    monkeypatch.setattr(llm_mock, "stream", slow_stream)

    content = _make_long_content(reply_chars)
    app = tui_app_factory()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        frames = _record_stream_frames(app)
        t0 = time.monotonic()
        await app._submit(content)
        deadline = t0 + 120
        while app.generating or app.state != "idle":
            assert time.monotonic() < deadline, "streaming turn did not finish"
            await asyncio.sleep(0.05)
        elapsed = time.monotonic() - t0
        assert app.manager.log.messages[-1].content == f"Echo: {content}"

    assert len(frames) >= 2, "fewer than two distinct streaming reply frames rendered"
    frame_times = [t for t, _ in frames]
    fps = (len(frame_times) - 1) / (frame_times[-1] - frame_times[0])
    record_property("reply_update_fps", fps)
    record_property("reply_frames", len(frames))
    record_property("elapsed_ms", elapsed * 1000)
    print(
        f"\nrender fps for {reply_chars:,}ch reply: {fps:.1f} fps "
        f"over {len(frame_times)} frames in {elapsed:.2f}s"
    )


# ---------------------------------------------------------------------------
# History render latency
# ---------------------------------------------------------------------------


@pytest.mark.perf
@pytest.mark.slow
@pytest.mark.asyncio
async def test_tui_history_render_latency(tui_app_factory, record_property):
    """Time for the TUI to become interactive with a 500-message history.

    n_history=250 pairs → 500 messages total.  This exercises the initial
    layout pass that mounts and positions every message widget.
    """
    app = tui_app_factory(n_history=250)
    t0 = time.perf_counter()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        elapsed_ms = (time.perf_counter() - t0) * 1000

    record_property("elapsed_ms", elapsed_ms)
    # Should reach idle/ready within 10s even with 500 messages
    assert elapsed_ms < 10_000, f"500-message history render took {elapsed_ms:.1f}ms"
    print(f"\n500-message history startup: {elapsed_ms:.1f}ms")
