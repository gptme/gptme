"""End-to-end tests for the TUI.

Two layers:

1. ``run_test``-based tests driving the real generation loop with the offline
   ``mock/echo`` model (streaming, tool confirmation, execution, queueing).
   ``mock/echo`` echoes the last user message, so a prompt containing a tool
   codeblock makes the "model" emit a real tool call.

2. tmux-based tests that spin up the actual ``gptme-tui`` binary in a real
   terminal, send keys, and assert on the captured pane. This is the layer
   that catches real-tty bugs: cooked-mode echo garbage, dead dialog keys,
   layout issues — which headless tests cannot see.
"""

import asyncio
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

pytest.importorskip("textual")

from gptme.init import init
from gptme.llm.models import set_default_model
from gptme.logmanager import LogManager
from gptme.message import Message, set_output_format
from gptme.tools import init_tools
from gptme.tui.app import (
    AssistantMessage,
    ChatInput,
    ConfirmScreen,
    GptmeApp,
    SystemMessage,
)

GENERATION_TIMEOUT = 20.0


@pytest.fixture
def mock_app(tmp_path, monkeypatch):
    """A GptmeApp wired to the offline mock/echo model with the shell tool."""
    monkeypatch.setenv("GPTME_MAX_STEPS", "1")
    init(
        "mock/echo",
        interactive=True,
        tool_allowlist=["shell"],
        tool_format="markdown",
        no_confirm=False,
    )
    # init() is process-global and idempotent; explicitly (re)apply the state
    # this test needs in case another test initialized differently
    set_default_model("mock/echo")
    init_tools(["shell"])
    set_output_format("quiet")
    manager = LogManager([], logdir=tmp_path / "conv", lock=False)
    # GptmeApp captures the chat context at construction: must happen after
    # the model/output-format contextvars are set
    return GptmeApp(manager, tool_format="markdown", workspace=tmp_path)


async def wait_for(pilot, condition, timeout=GENERATION_TIMEOUT, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        await pilot.pause()
        if condition():
            return
    raise AssertionError(f"Timed out waiting for {what}")


@pytest.mark.asyncio
async def test_e2e_generation_streams_and_renders(mock_app):
    """Submitting a prompt drives the real step loop to a rendered response."""
    app = mock_app
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.text = "hello world"
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: not app.generating and app.query(AssistantMessage),
            what="assistant response",
        )
        assert app.state == "idle"
        (assistant,) = app.query(AssistantMessage)
        assert "Echo: hello world" in assistant.content
        # response persisted to the log
        assert any(
            m.role == "assistant" and "Echo: hello world" in m.content
            for m in app.manager.log
        )


@pytest.mark.asyncio
async def test_e2e_tool_confirm_execute(mock_app, tmp_path):
    """A tool call raises the confirm dialog; confirming executes it."""
    app = mock_app
    marker = f"marker_{uuid.uuid4().hex[:8]}"
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        # mock/echo will echo this back, making the codeblock a real tool call;
        # `touch` is not on the shell tool's no-confirm allowlist (echo is)
        # leading text line keeps the fence at line-start after the "Echo: " prefix
        inp.text = f"run this:\n```shell\ntouch {tmp_path / marker}\n```"
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: isinstance(app.screen_stack[-1], ConfirmScreen),
            what="confirmation dialog",
        )
        await pilot.press("y")
        await wait_for(
            pilot,
            lambda: not app.generating and app.state == "idle",
            what="generation to finish",
        )
        assert (tmp_path / marker).exists(), "confirmed tool should have executed"
        assert app.query(SystemMessage), "tool output should be rendered"


@pytest.mark.asyncio
async def test_e2e_tool_skip(mock_app, tmp_path):
    """Declining the confirm dialog skips execution and returns to idle."""
    app = mock_app
    marker = f"marker_{uuid.uuid4().hex[:8]}"
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        # leading text line keeps the fence at line-start after the "Echo: " prefix
        inp.text = f"run this:\n```shell\ntouch {tmp_path / marker}\n```"
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: isinstance(app.screen_stack[-1], ConfirmScreen),
            what="confirmation dialog",
        )
        await pilot.press("n")
        await wait_for(
            pilot,
            lambda: not app.generating and app.state == "idle",
            what="generation to finish",
        )
        assert not (tmp_path / marker).exists(), "skipped tool must not execute"


@pytest.mark.asyncio
async def test_e2e_queue_dispatches_after_turn(mock_app, monkeypatch):
    """Prompts queued while generating are submitted when the turn ends."""
    import threading

    import gptme.llm.llm_mock as llm_mock

    app = mock_app
    orig_stream = llm_mock.stream

    # Gate that holds the first stream open until after we verify the queue.
    # time.sleep()-based delays are unreliable: the Textual event loop can
    # process the worker-done message during await pilot.pause(), clearing the
    # queue before our assertion runs. A threading.Event is deterministic.
    first_turn_gate = threading.Event()
    call_count = 0

    def gated_stream(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        is_first = call_count == 1

        def gen():
            if is_first:
                first_turn_gate.wait()  # block until test releases
            yield from orig_stream(*args, **kwargs)

        return gen()

    monkeypatch.setattr(llm_mock, "stream", gated_stream)

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.text = "first message please"
        await pilot.press("enter")
        # generating is set synchronously on submit; the gate holds the first
        # stream open so we can safely queue the next prompt and assert
        assert app.generating
        inp.text = "second message please"
        await pilot.press("enter")
        await pilot.pause()
        assert app.prompt_queue == ["second message please"]
        # Release the gate: first stream completes, generation ends, queued
        # prompt is dispatched
        first_turn_gate.set()
        await wait_for(
            pilot,
            lambda: (
                not app.generating
                and not app.prompt_queue
                and sum(
                    1
                    for m in app.manager.log
                    if m.role == "assistant" and m.content.startswith("Echo:")
                )
                >= 2
            ),
            what="queued prompt to be processed",
        )
        assert any(
            "Echo: second message please" in m.content
            for m in app.manager.log
            if m.role == "assistant"
        )


# ---------------------------------------------------------------------------
# Real-TTY tests via tmux
# ---------------------------------------------------------------------------

TMUX = shutil.which("tmux")

# fragments of escape sequences that show up as literal text when the tty is
# in cooked/echo mode (SGR mouse reports, VREPRINT, CSI fragments)
ESCAPE_GARBAGE = ["[<3", "^R", "[?100", ";4M", ";3M"]


class TmuxTUI:
    """Drive a real gptme-tui process in a tmux pane."""

    def __init__(self, tmp_path: Path, extra_args: str = "", size=(100, 30)):
        self.session = f"tuie2e_{uuid.uuid4().hex[:8]}"
        self.tmp_path = tmp_path
        # hermetic env: fresh HOME so the user's real config (plugins, hooks,
        # TTS, …) can't leak into the test run
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        env = (
            f"HOME={home} "
            f"XDG_CONFIG_HOME={home / '.config'} "
            f"XDG_DATA_HOME={home / '.local' / 'share'} "
            f"GPTME_LOGS_HOME={tmp_path / 'logs'} "
            f"GPTME_MAX_STEPS=1 "
            f"COLUMNS={size[0]} LINES={size[1]}"
        )
        # NOTE: do not redirect stderr — the Textual driver renders the UI
        # via sys.__stderr__
        cmd = (
            f"env {env} {sys.executable} -m gptme.tui.main "
            f"-m mock/echo -w {tmp_path} {extra_args}"
        )
        subprocess.run(
            [
                "tmux",
                "new-session",
                "-d",
                "-s",
                self.session,
                "-x",
                str(size[0]),
                "-y",
                str(size[1]),
                cmd,
            ],
            check=True,
        )

    def send(self, keys: str, literal: bool = True) -> None:
        args = ["tmux", "send-keys", "-t", self.session]
        if literal:
            args.append("-l")
        args.append(keys)
        subprocess.run(args, check=True)

    def send_key(self, key: str) -> None:
        """Send a named key (Enter, Escape, Tab, …)."""
        subprocess.run(["tmux", "send-keys", "-t", self.session, key], check=True)

    def capture(self) -> str:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", self.session, "-p"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def capture_scrollback(self, lines: int = 1000) -> str:
        """Capture pane content including scrollback history."""
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", self.session, "-p", "-S", f"-{lines}"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def wait_for(self, needle: str, timeout: float = 30.0) -> str:
        deadline = time.monotonic() + timeout
        content = ""
        while time.monotonic() < deadline:
            content = self.capture()
            if needle in content:
                return content
            time.sleep(0.3)
        raise AssertionError(
            f"Timed out waiting for {needle!r} in tmux pane.\n--- pane ---\n{content}"
        )

    def wait_ready(self) -> None:
        """Wait until the app renders and settles enough to receive input.

        Right after first paint, Textual is still negotiating terminal
        protocols and can drop the first keypress.
        """
        self.wait_for("Type a message")
        time.sleep(0.7)

    def resume(self, expect: str, timeout: float = 10.0) -> str:
        """Press Enter on the empty input to resume generation, with retries
        (a keypress sent during app startup can be dropped)."""
        for _attempt in range(3):
            self.send_key("Enter")
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                content = self.capture()
                if expect in content:
                    return content
                time.sleep(0.3)
        raise AssertionError(
            f"Resume did not produce {expect!r}.\n--- pane ---\n{self.capture()}"
        )

    def alive(self) -> bool:
        return (
            subprocess.run(
                ["tmux", "has-session", "-t", self.session],
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )

    def kill(self) -> None:
        subprocess.run(["tmux", "kill-session", "-t", self.session], check=False)


@pytest.fixture
def tmux_tui(tmp_path):
    sessions: list[TmuxTUI] = []

    def start(extra_args: str = "-t shell") -> TmuxTUI:
        tui = TmuxTUI(tmp_path, extra_args=extra_args)
        sessions.append(tui)
        return tui

    yield start
    for tui in sessions:
        tui.kill()


def seed_conversation(tmp_path: Path, name: str, content: str) -> None:
    """Create a conversation whose last message is a user prompt.

    The single-line Input can't be used to type multiline codeblocks, so tool
    call prompts are seeded on disk; pressing Enter on the empty input then
    resumes generation from the pending user message.
    """
    from gptme.logmanager import Log

    logdir = tmp_path / "logs" / name
    logdir.mkdir(parents=True, exist_ok=True)
    Log([Message("user", content)]).write_jsonl(logdir / "conversation.jsonl")


def assert_no_escape_garbage(pane: str) -> None:
    for fragment in ESCAPE_GARBAGE:
        assert fragment not in pane, (
            f"escape-sequence garbage {fragment!r} visible in pane:\n{pane}"
        )


@pytest.mark.skipif(not TMUX, reason="tmux not available")
@pytest.mark.timeout(120)
class TestTmuxRealTerminal:
    def test_renders_and_responds(self, tmux_tui):
        """The TUI starts in a real terminal, accepts input, renders response."""
        tui = tmux_tui()
        tui.wait_ready()
        tui.send("hello from tmux")
        tui.send_key("Enter")
        pane = tui.wait_for("Echo: hello from tmux")
        assert_no_escape_garbage(pane)
        # status bar present with model + state
        assert "mock/echo" in pane
        assert "idle" in pane

    def test_confirm_dialog_keys_work(self, tmux_tui, tmp_path):
        """Type a multiline tool-call prompt live (Ctrl+J for newlines); the
        dialog answers with a single keypress (regression: cooked-mode tty
        line-buffers input, so y/n appeared dead)."""
        marker = tmp_path / "confirmed.txt"
        tui = tmux_tui(extra_args="-t shell")
        tui.wait_ready()
        # multiline input: Ctrl+J inserts newlines, Enter submits
        for i, line in enumerate(["run this:", "```shell", f"touch {marker}", "```"]):
            if i:
                tui.send_key("C-j")
            tui.send(line)
        tui.send_key("Enter")
        tui.wait_for("Execute shell?")
        tui.send("y")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.3)
        assert marker.exists(), "y keypress should confirm and execute the tool"
        assert_no_escape_garbage(tui.capture())

    def test_ipython_does_not_corrupt_tty(self, tmux_tui, tmp_path):
        """Regression: ipython init resets the tty to cooked/echo mode, which
        echoed typed chars and mouse reports as garbage and broke input."""
        seed_conversation(
            tmp_path, "ipython-test", "run this:\n```ipython\nprint('tty_marker')\n```"
        )
        tui = tmux_tui(extra_args="-t shell,ipython -n ipython-test")
        tui.wait_ready()
        # empty-submit resumes generation from the pending user message
        tui.resume("Execute ipython?")
        tui.send("y")
        # max-steps info message marks the end of the turn deterministically
        tui.wait_for("Reached max steps", timeout=30)
        # after ipython ran, typing must still work without cooked-mode echo
        time.sleep(1.5)  # give the tty watchdog a cycle
        tui.send("still typing fine")
        pane = tui.wait_for("still typing fine")
        assert_no_escape_garbage(pane)
        # simulate what a terminal sends on mouse-move with SGR tracking on;
        # in cooked/echo mode these bytes would be echoed into the pane
        tui.send("\x1b[<35;10;4M")
        time.sleep(0.5)
        assert_no_escape_garbage(tui.capture())
        assert tui.alive()


@pytest.mark.skipif(not TMUX, reason="tmux not available")
@pytest.mark.timeout(120)
class TestTmuxInlineMode:
    """--inline: native-scrollback rendering (no alternate screen)."""

    def test_inline_prints_to_scrollback(self, tmux_tui, tmp_path):
        tui = tmux_tui(extra_args="-t shell --inline")
        tui.wait_ready()
        tui.send("hello inline")
        tui.send_key("Enter")
        tui.wait_for("Echo: hello inline")
        # Wait for the input prompt to confirm rendering is complete.
        tui.wait_for("Type a message")
        # In --inline mode the response is emitted to native scrollback (above
        # the live input region). tmux capture-pane without -S only shows the
        # current 30-line visible screen, so "Echo: hello inline" can scroll
        # above that window by the time the prompt re-appears, causing
        # ValueError from str.index(). Use a scrollback-aware capture so both
        # strings are always in the same snapshot.
        pane = tui.capture_scrollback()
        assert_no_escape_garbage(pane)
        # transcript lines are in scrollback (lower index) above the live
        # input region which contains the prompt (higher index). Use rindex so
        # we compare against the re-rendered prompt AFTER the echo, not the
        # initial startup prompt that appears earlier in the scrollback.
        assert pane.index("Echo: hello inline") < pane.rindex("Type a message")

    def test_inline_tool_flow(self, tmux_tui, tmp_path):
        marker = tmp_path / "inline_marker"
        tui = tmux_tui(extra_args="-t shell --inline")
        tui.wait_ready()
        for i, line in enumerate(["run this:", "```shell", f"touch {marker}", "```"]):
            if i:
                tui.send_key("C-j")
            tui.send(line)
        tui.send_key("Enter")
        tui.wait_for("Execute shell?")
        tui.send("y")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.3)
        assert marker.exists()
        # tool output printed as a collapsed summary line
        pane = tui.wait_for("Ran command")
        assert "▶" in pane
        assert_no_escape_garbage(pane)
