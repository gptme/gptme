"""OSC lifecycle reports use the Textual driver, not captured stdout."""

import pytest

pytest.importorskip("textual")

from gptme.logmanager import LogManager
from gptme.tui.app import GptmeApp, UrlConfirmScreen
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
    monkeypatch.setattr(app, "_start_generation", lambda: None)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._report_program_status("blocked", "permission")
        app._send(Message("user", "read https://example.com"), ["https://example.com"])
        assert during_fetch == [encode_report("working")]
