"""Wire-format, nested lifecycle, opt-out and PTY checks for OSC 7501."""

import os
import subprocess
import sys
from io import StringIO
from types import SimpleNamespace

import pytest

from gptme.util import program_status as status


@pytest.fixture
def output(monkeypatch):
    stream = StringIO()
    monkeypatch.setattr(stream, "isatty", lambda: True)
    monkeypatch.setattr(status, "sys", SimpleNamespace(stdout=stream))
    monkeypatch.delenv("GPTME_PROGRAM_STATUS", raising=False)
    from gptme.message import _output_format

    output_token = _output_format.set("text")
    token = status._current.set(("clear", None))
    yield stream
    status._current.reset(token)
    _output_format.reset(output_token)


def test_lifecycle(output):
    with (
        status.program_status("idle"),
        status.program_status("working", after="done"),
        status.program_status("blocked", "permission"),
    ):
        pass
    assert output.getvalue() == "".join(
        f"\033]7501;state={state}:app=gptme{kind}\033\\"
        for state, kind in [
            ("idle", ""),
            ("working", ""),
            ("blocked", ":kind=permission"),
            ("working", ""),
            ("done", ""),
            ("clear", ""),
        ]
    )


def test_error_and_session_cleanup(output):
    with (
        pytest.raises(ValueError, match="failure"),
        status.program_status("idle"),
        status.program_status("working", after="done"),
    ):
        raise ValueError("failure")
    assert "state=error" in output.getvalue()
    assert output.getvalue().endswith("\033]7501;state=clear:app=gptme\033\\")


def test_disabled(output, monkeypatch):
    monkeypatch.setenv("GPTME_PROGRAM_STATUS", "0")
    status.report("working")
    assert output.getvalue() == ""


def test_no_redirected_output(monkeypatch):
    stream = StringIO()
    monkeypatch.setattr(status, "sys", SimpleNamespace(stdout=stream))
    status.report("working")
    assert stream.getvalue() == ""


def test_broken_terminal_is_nonfatal(output, monkeypatch):
    def broken_write(value):
        raise OSError("disconnected")

    monkeypatch.setattr(output, "write", broken_write)
    status.report("working")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY")
def test_subprocess_pty():
    """Exercise the real isatty path in a fresh interpreter, without an LLM."""
    import pty

    master, slave = pty.openpty()
    env = {k: v for k, v in os.environ.items() if k != "GPTME_PROGRAM_STATUS"}
    try:
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                'from gptme.util.program_status import report; report("working")',
            ],
            stdout=slave,
            env=env,
            check=True,
        )
        assert process.returncode == 0
        assert os.read(master, 4096) == b"\x1b]7501;state=working:app=gptme\x1b\\"
    finally:
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize("fmt", ["json", "quiet"])
def test_machine_output_is_clean(output, fmt):
    from gptme.message import _output_format

    token = _output_format.set(fmt)
    try:
        status.report("working")
    finally:
        _output_format.reset(token)
    assert output.getvalue() == ""


def test_cli_question_restores_working(output, monkeypatch):
    from gptme.hooks.elicitation import ElicitationRequest, cli_elicit

    def answer(prompt):
        assert "state=blocked:app=gptme:kind=question" in output.getvalue()
        return "yes"

    monkeypatch.setattr("builtins.input", answer)
    with status.program_status("working"):
        response = cli_elicit(ElicitationRequest(type="text", prompt="Question?"))
        assert response.value == "yes"
        assert output.getvalue().endswith("\033]7501;state=working:app=gptme\033\\")
