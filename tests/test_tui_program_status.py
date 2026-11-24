"""OSC lifecycle reports use the Textual driver, not captured stdout."""

import asyncio
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("textual")

from gptme.logmanager import Log, LogManager
from gptme.message import Message
from gptme.tui.app import ChatInput, GptmeApp, UrlConfirmScreen
from gptme.util.program_status import encode_report


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True])
async def test_tui_lifecycle(tmp_path, monkeypatch, inline):
    manager = LogManager([], logdir=tmp_path / "chat", lock=False)
    app = GptmeApp(manager, workspace=tmp_path, inline=inline)
    app._statusline_cmd = None
    reports = []
    monkeypatch.delenv("GPTME_PROGRAM_STATUS", raising=False)
    # run_test's driver is headless; spy only on terminal output, keeping the
    # real event loop, widgets, queue dispatch and modal lifecycle.
    from textual.drivers.headless_driver import HeadlessDriver

    monkeypatch.setattr(
        HeadlessDriver,
        "write",
        lambda self, data: reports.append(data) if "7501;" in data else None,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        assert encode_report("idle") in reports
        app.generating = True
        app._terminal_outcome = "done"
        app._set_state("generating")
        app._set_state("running shell")
        assert reports[-1] == encode_report("working")
        assert reports.count(encode_report("working")) == 1
        app._set_state("awaiting confirmation")
        assert reports[-1] == encode_report("blocked", "permission")
        app._after_confirm()
        assert reports[-1] == encode_report("working")
        await app._generation_done()
        assert reports[-1] == encode_report("done")

        app.push_screen(UrlConfirmScreen(["https://example.com"]))
        await pilot.pause()
        assert reports[-1] == encode_report("blocked", "permission")
        await pilot.press("n")
        await pilot.pause()
        assert reports[-1] == encode_report("done")

        app.generating = True
        app._set_state("generating")
        app._generation_failed()
        await app._generation_done()
        assert reports[-1] == encode_report("error")
        app.generating = True
        app._interrupt_event.set()
        await app._generation_done()
        assert reports[-1] == encode_report("idle")
    assert reports[-1] == encode_report("clear")
    # A delayed worker callback after unmount must not resurrect the badge.
    app._report_program_status("working")
    assert reports[-1] == encode_report("clear")


@pytest.mark.asyncio
async def test_tui_opt_out(tmp_path, monkeypatch):
    manager = LogManager([], logdir=tmp_path / "chat", lock=False)
    app = GptmeApp(manager, workspace=tmp_path)
    app._statusline_cmd = None
    reports = []
    from textual.drivers.headless_driver import HeadlessDriver

    monkeypatch.setattr(
        HeadlessDriver,
        "write",
        lambda self, data: reports.append(data) if "7501;" in data else None,
    )
    monkeypatch.setenv("GPTME_PROGRAM_STATUS", "0")
    async with app.run_test() as pilot:
        await pilot.pause()
        app.generating = True
        app._set_state("generating")
    assert not any("7501;" in report for report in reports)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("declined", "idle"),
        ("interrupt", "idle"),
        ("error", "error"),
        ("reply", "done"),
    ],
)
def test_tui_turn_outcome(tmp_path, monkeypatch, mode, expected):
    """A declined (``n``) or interrupted TUI turn ends idle, like the CLI."""
    from gptme.constants import DECLINED_CONTENT
    from gptme.message import Message

    manager = LogManager([], logdir=tmp_path / "chat", lock=False)
    manager.append(Message("user", "run a tool"))
    app = GptmeApp(manager, workspace=tmp_path)
    app._terminal_outcome = "done"  # as set by _start_generation

    def call_from_thread(callback, *args, **kwargs):
        # Run the callbacks that decide the outcome; skip widget updates.
        if callback in (app._take_steer_prompts, app._generation_failed):
            return callback(*args, **kwargs)
        return None

    def reply(*args, **kwargs):
        if mode == "interrupt":
            raise KeyboardInterrupt
        if mode == "error":
            raise ValueError("provider unavailable")
        if mode == "declined":
            yield Message("system", DECLINED_CONTENT)
        else:
            yield Message("assistant", "Done.")

    monkeypatch.setattr(app, "call_from_thread", call_from_thread)
    monkeypatch.setattr(app, "_restore_terminal", lambda: None)
    monkeypatch.setattr("gptme.tui.app.trigger_hook", lambda *a, **kw: [])
    monkeypatch.delenv("GPTME_MAX_STEPS", raising=False)
    monkeypatch.setattr("gptme.tui.app.step", reply)
    app._generation_body()
    assert app._terminal_outcome == expected
    if mode == "reply":
        assert manager.log.messages[-1].content == "Done."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("interrupted", "error", "expected"),
    [(False, None, "done"), (False, "boom", "error"), (True, None, "idle")],
)
async def test_tui_background_command_outcome(
    tmp_path, monkeypatch, interrupted, error, expected
):
    """An interrupted background command (e.g. /compact + Esc) ends idle."""
    from gptme.tui.app import _CommandResult

    manager = LogManager([], logdir=tmp_path / "chat", lock=False)
    app = GptmeApp(manager, workspace=tmp_path)
    app._statusline_cmd = None
    reports = []
    monkeypatch.delenv("GPTME_PROGRAM_STATUS", raising=False)
    from textual.drivers.headless_driver import HeadlessDriver

    monkeypatch.setattr(
        HeadlessDriver,
        "write",
        lambda self, data: reports.append(data) if "7501;" in data else None,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.generating = True
        app._terminal_outcome = "done"
        if interrupted:
            app._interrupt_event.set()
        app._background_command_done(
            "compact", [], _CommandResult(handled=True, error=error)
        )
        await pilot.pause()
        assert app._terminal_outcome == expected
        assert reports[-1] == encode_report(expected)


@pytest.mark.asyncio
async def test_tui_url_fetch_after_approval_reports_working(tmp_path, monkeypatch):
    """Fetching approved URLs is part of the turn, not a pending approval."""
    from gptme.message import Message

    manager = LogManager([], logdir=tmp_path / "chat", lock=False)
    app = GptmeApp(manager, workspace=tmp_path)
    app._statusline_cmd = None
    reports = []
    monkeypatch.delenv("GPTME_PROGRAM_STATUS", raising=False)
    from textual.drivers.headless_driver import HeadlessDriver

    monkeypatch.setattr(
        HeadlessDriver,
        "write",
        lambda self, data: reports.append(data) if "7501;" in data else None,
    )
    during_fetch = []

    def slow_include_paths(msg, workspace, pre_confirmed_urls=None):
        during_fetch.append(reports[-1])
        return msg

    monkeypatch.setattr("gptme.tui.app.include_paths", slow_include_paths)
    monkeypatch.setattr(app, "_generation_body", lambda: None)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._report_program_status("blocked", "permission")
        app._send(Message("user", "read https://example.com"), ["https://example.com"])
        await app.workers.wait_for_complete()
        assert during_fetch == [encode_report("working")]


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", [False, True], ids=["complete", "interrupt"])
async def test_tui_approved_url_fetch_keeps_event_loop_responsive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupt: bool
) -> None:
    from gptme.util.workspace import workspace_scope

    url = "https://example.com"
    queued_url = "https://example.org"
    text = f"Read {url} and @notes.txt"
    followup = "A follow-up while fetching" if interrupt else f"Read {queued_url} too"
    (tmp_path / "notes.txt").write_text("local attachment")
    manager = LogManager([], logdir=tmp_path / "chat", lock=False)
    with workspace_scope(tmp_path):
        app = GptmeApp(manager, workspace=tmp_path)
    app._statusline_cmd = None
    entered = threading.Event()
    release = threading.Event()
    fetched = threading.Event()
    ui_thread = threading.get_ident()
    fetch_threads: list[int] = []
    step_prompts: list[str] = []

    def read_url(requested: str) -> str:
        fetch_threads.append(threading.get_ident())
        if requested == queued_url:
            return "queued attachment"
        assert requested == url
        entered.set()
        # Surface the inline call directly on base without stalling pytest.
        if threading.get_ident() != ui_thread:
            release.wait(10)
        fetched.set()
        return "fetched attachment"

    def reply(log: Log, **kwargs: object) -> Iterator[Message]:
        step_prompts.append(log[-1].content)
        yield Message("assistant", "Done.")

    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    monkeypatch.delenv("GPTME_PROGRAM_STATUS", raising=False)
    monkeypatch.setattr("gptme.util.context.use_fresh_context", lambda: False)
    monkeypatch.setattr("gptme.util.context.has_tool", lambda name: name == "browser")
    monkeypatch.setattr("gptme.tools.browser.read_url", read_url)
    monkeypatch.setattr("gptme.tui.app.step", reply)
    monkeypatch.setattr("gptme.tui.app.trigger_hook", lambda *args, **kwargs: [])
    monkeypatch.setattr("gptme.tui.app.maybe_start_auto_naming", lambda *args: None)

    with workspace_scope(tmp_path):
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = app.query_one("#input", ChatInput)
            composer.text = text
            await pilot.press("enter")
            assert isinstance(app.screen, UrlConfirmScreen)
            assert not entered.is_set()
            try:
                await pilot.press("y")
                assert await asyncio.to_thread(entered.wait, 5)
                assert ui_thread not in fetch_threads, (
                    "URL fetched on Textual's event loop"
                )
                assert app._last_program_status == ("working", None)
                assert not step_prompts
                composer.text = followup
                await pilot.press("enter")
                if interrupt:
                    assert app.prompt_queue == [followup]
                    await pilot.press("ctrl+c")
                    assert app.state == "interrupting…"
                else:
                    assert isinstance(app.screen, UrlConfirmScreen)
                    await pilot.press("y")
                    assert len(app.prompt_queue) == 1
                    assert len(fetch_threads) == 1
                assert not fetched.is_set()
            finally:
                release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await app.workers.wait_for_complete()
            if interrupt:
                assert len(fetch_threads) == 1
                assert step_prompts == []
                assert manager.log[-1].content == text
                assert composer.text == followup
                assert app._last_program_status == ("idle", None)
            else:
                assert len(fetch_threads) == 2
                assert ui_thread not in fetch_threads
                first, second = step_prompts
                assert first.startswith(text)
                assert "fetched attachment" in first
                assert "local attachment" in first
                assert second.startswith(followup)
                assert "queued attachment" in second


@pytest.mark.asyncio
async def test_tui_quit_during_url_fetch_saves_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quitting during a slow URL fetch keeps the approved prompt in the log."""
    from gptme.util.workspace import workspace_scope

    text = "Read https://example.com and @notes.txt"
    (tmp_path / "notes.txt").write_text("local attachment")
    manager = LogManager([], logdir=tmp_path / "chat", lock=False)
    with workspace_scope(tmp_path):
        app = GptmeApp(manager, workspace=tmp_path)
    app._statusline_cmd = None
    entered = threading.Event()
    release = threading.Event()
    ui_thread = threading.get_ident()
    fetch_threads: list[int] = []

    def read_url(requested: str) -> str:
        fetch_threads.append(threading.get_ident())
        assert requested == "https://example.com"
        entered.set()
        # Hold the fetch so the quit lands while the prompt is still pending.
        if threading.get_ident() != ui_thread:
            release.wait(5)
        return "fetched attachment"

    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    monkeypatch.delenv("GPTME_PROGRAM_STATUS", raising=False)
    monkeypatch.setattr("gptme.util.context.use_fresh_context", lambda: False)
    monkeypatch.setattr("gptme.util.context.has_tool", lambda name: name == "browser")
    monkeypatch.setattr("gptme.tools.browser.read_url", read_url)
    monkeypatch.setattr("gptme.tui.app.trigger_hook", lambda *args, **kwargs: [])
    monkeypatch.setattr("gptme.tui.app.maybe_start_auto_naming", lambda *args: None)

    with workspace_scope(tmp_path):
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = app.query_one("#input", ChatInput)
            composer.text = text
            await pilot.press("enter")
            assert isinstance(app.screen, UrlConfirmScreen)
            assert not entered.is_set()
            try:
                await pilot.press("y")
                assert await asyncio.to_thread(entered.wait, 5)
                assert ui_thread not in fetch_threads
                # The worker holds the prompt until the fetch returns.
                assert app._pending_prompt is not None
                app._request_exit()
            finally:
                release.set()
            await pilot.pause()
    # The quit saved the typed prompt to the conversation.
    assert manager.log[-1].content == text


@pytest.mark.asyncio
async def test_tui_resume_after_url_fetch_interrupt_keeps_attachments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fetch interrupted midway leaves the prompt (attachments included)
    in the log, so an empty-submit resume sends it all to the model."""
    from gptme.util.workspace import workspace_scope

    text = "Read https://example.com and @notes.txt"
    (tmp_path / "notes.txt").write_text("local attachment")
    manager = LogManager([], logdir=tmp_path / "chat", lock=False)
    with workspace_scope(tmp_path):
        app = GptmeApp(manager, workspace=tmp_path)
    app._statusline_cmd = None
    entered = threading.Event()
    release = threading.Event()
    ui_thread = threading.get_ident()
    fetch_threads: list[int] = []
    step_prompts: list[str] = []

    def read_url(requested: str) -> str:
        fetch_threads.append(threading.get_ident())
        assert requested == "https://example.com"
        entered.set()
        release.wait(5)
        return "fetched attachment"

    def reply(log: Log, **kwargs: object) -> Iterator[Message]:
        step_prompts.append(log[-1].content)
        yield Message("assistant", "Done.")

    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    monkeypatch.delenv("GPTME_PROGRAM_STATUS", raising=False)
    monkeypatch.setattr("gptme.util.context.use_fresh_context", lambda: False)
    monkeypatch.setattr("gptme.util.context.has_tool", lambda name: name == "browser")
    monkeypatch.setattr("gptme.tools.browser.read_url", read_url)
    monkeypatch.setattr("gptme.tui.app.step", reply)
    monkeypatch.setattr("gptme.tui.app.trigger_hook", lambda *args, **kwargs: [])
    monkeypatch.setattr("gptme.tui.app.maybe_start_auto_naming", lambda *args: None)

    with workspace_scope(tmp_path):
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = app.query_one("#input", ChatInput)
            composer.text = text
            await pilot.press("enter")
            assert isinstance(app.screen, UrlConfirmScreen)
            assert not entered.is_set()
            try:
                await pilot.press("y")
                assert await asyncio.to_thread(entered.wait, 5)
                assert ui_thread not in fetch_threads
                await pilot.press("ctrl+c")
                await pilot.pause()
                assert composer.text == ""
                assert app.prompt_queue == []
            finally:
                release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            # The interrupted fetch still logged the prompt, attachment included.
            interrupted_prompt = manager.log[-1].content
            assert interrupted_prompt.startswith(text)
            assert "local attachment" in interrupted_prompt
            # An empty submit resumes the turn with everything in the log.
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
    assert len(fetch_threads) == 1  # the URL was not fetched again
    assert step_prompts == [interrupted_prompt]


@pytest.mark.parametrize(
    ("pending_tools", "expected"),
    [(True, "idle"), (False, "done")],
)
def test_tui_max_steps_stop_outcome(tmp_path, monkeypatch, pending_tools, expected):
    """A step limit ends done only if the stopped turn had finished its work."""
    from gptme.message import Message
    from gptme.tools import init_tools

    init_tools(["shell"])
    manager = LogManager([], logdir=tmp_path / "chat", lock=False)
    manager.append(Message("user", "run a tool"))
    app = GptmeApp(manager, workspace=tmp_path)
    app._terminal_outcome = "done"  # as set by _start_generation

    def call_from_thread(callback, *args, **kwargs):
        if callback in (app._take_steer_prompts, app._generation_failed):
            return callback(*args, **kwargs)
        return None

    content = "Running it:\n```shell\necho hi\n```" if pending_tools else "All done."

    def reply(*args, **kwargs):
        yield Message("assistant", content)

    monkeypatch.setattr(app, "call_from_thread", call_from_thread)
    monkeypatch.setattr(app, "_restore_terminal", lambda: None)
    monkeypatch.setattr("gptme.tui.app.trigger_hook", lambda *a, **kw: [])
    monkeypatch.setenv("GPTME_MAX_STEPS", "1")
    monkeypatch.setattr("gptme.tui.app.step", reply)
    app._generation_body()
    assert app._terminal_outcome == expected
