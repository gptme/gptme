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
