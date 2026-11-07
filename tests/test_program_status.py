"""Wire-format, nested lifecycle, opt-out and PTY checks for OSC 7501."""

import os
import subprocess
import sys
from io import StringIO
from types import SimpleNamespace

import pytest

from gptme.util import program_status as status


@pytest.fixture(autouse=True)
def isolated_status():
    """Every test starts outside any session; report() always updates state."""
    token = status._current.set(("clear", None))
    yield
    status._current.reset(token)


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
    # State is still tracked, so nesting detection works without a TTY.
    assert status._current.get() == ("working", None)


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


def test_thread_cannot_overwrite_parent_terminal(output):
    import contextvars
    import threading

    def child():
        with (
            status.program_status("idle"),
            status.program_status("working", after="done"),
        ):
            pass

    with status.program_status("working"):
        before = output.getvalue()
        thread = threading.Thread(target=lambda: contextvars.Context().run(child))
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert output.getvalue() == before


def test_successful_completion_is_not_error(output):
    from gptme.tools.complete import SessionCompleteException

    with (
        pytest.raises(SessionCompleteException),
        status.program_status(
            "working", after="done", success_exceptions=(SessionCompleteException,)
        ),
    ):
        raise SessionCompleteException("finished")
    assert output.getvalue().endswith(status.encode_report("done"))
    assert "state=error" not in output.getvalue()


def test_confirmation_editor_stays_blocked(output, monkeypatch):
    from gptme.hooks import cli_confirm
    from gptme.tools.base import ToolUse

    monkeypatch.setattr(cli_confirm, "check_auto_confirm", lambda: (False, None))
    monkeypatch.setattr(cli_confirm, "print_preview", lambda *a, **kw: None)
    monkeypatch.setattr(cli_confirm, "print_bell", lambda: None)
    monkeypatch.setattr(cli_confirm, "flush_stdin", lambda: None)
    monkeypatch.setattr(cli_confirm, "prompt_alert", lambda prompt: "e")

    def edit(content, ext):
        assert output.getvalue().endswith(status.encode_report("blocked", "permission"))
        return content + " edited"

    monkeypatch.setattr(cli_confirm, "edit_text_with_editor", edit)
    with status.program_status("working"):
        cli_confirm.cli_confirm_hook(ToolUse("save", ["example.txt"], "original"))
        assert output.getvalue().endswith(status.encode_report("working"))


@pytest.mark.parametrize("outcome", ["interrupt", "decline", "complete"])
def test_cli_turn_end_state(output, tmp_path, outcome):
    """A cancelled or declined CLI turn waits for the user (idle), not done."""
    import importlib
    from unittest.mock import patch

    from gptme.constants import DECLINED_CONTENT
    from gptme.logmanager import LogManager
    from gptme.message import Message

    chat = importlib.import_module("gptme.chat")
    manager = LogManager(
        [Message("system", "System"), Message("user", "Do work")],
        logdir=tmp_path / "conversation",
    )

    def fake_step(*args, **kwargs):
        if outcome == "interrupt":
            raise KeyboardInterrupt
        if outcome == "decline":
            return [Message("system", DECLINED_CONTENT)]
        return [Message("assistant", "Done")]

    with (
        patch.object(chat, "step", side_effect=fake_step),
        patch.object(chat, "get_default_model", return_value=None),
        patch.object(chat, "trigger_hook", return_value=[]),
        patch.object(chat, "_run_post_tool_compaction", return_value=False),
    ):
        interrupted = chat._process_message_conversation(
            manager, False, "markdown", None
        )

    assert interrupted is (outcome != "complete")
    expected = status.encode_report("done" if outcome == "complete" else "idle")
    assert output.getvalue().endswith(expected)
    assert output.getvalue().count("state=done") == (outcome == "complete")


def test_cli_turn_interrupt_outside_step_is_idle(output, tmp_path):
    """Ctrl+C escaping the turn (e.g. in a TURN_POST hook) is not an error."""
    import importlib
    from unittest.mock import patch

    from gptme.logmanager import LogManager
    from gptme.message import Message

    chat = importlib.import_module("gptme.chat")
    manager = LogManager(
        [Message("system", "System"), Message("user", "Do work")],
        logdir=tmp_path / "conversation",
    )

    def hook(hook_type, **kwargs):
        if hook_type == chat.HookType.TURN_POST:
            raise KeyboardInterrupt
        return []

    with (
        patch.object(chat, "step", return_value=[Message("assistant", "Done")]),
        patch.object(chat, "get_default_model", return_value=None),
        patch.object(chat, "trigger_hook", side_effect=hook),
        patch.object(chat, "_run_post_tool_compaction", return_value=False),
        pytest.raises(KeyboardInterrupt),
    ):
        chat._process_message_conversation(manager, False, "markdown", None)

    assert output.getvalue().endswith(status.encode_report("idle"))
    assert "state=error" not in output.getvalue()


@pytest.mark.parametrize("failure", [None, KeyboardInterrupt, ValueError])
def test_cli_turn_pre_hooks_run_while_working(output, tmp_path, failure):
    """The working state spans TURN_PRE hooks, which run before generation."""
    import importlib
    from unittest.mock import patch

    from gptme.logmanager import LogManager
    from gptme.message import Message

    chat = importlib.import_module("gptme.chat")
    manager = LogManager(
        [Message("system", "System"), Message("user", "Do work")],
        logdir=tmp_path / "conversation",
    )
    status.report("done")  # the previous turn's outcome

    def hook(hook_type, **kwargs):
        assert hook_type == chat.HookType.TURN_PRE
        assert output.getvalue().endswith(status.encode_report("working"))
        if failure is not None:
            raise failure
        return [Message("system", "pre")]

    with patch.object(chat, "trigger_hook", side_effect=hook):
        if failure is None:
            chat._trigger_turn_pre(manager)
        else:
            with pytest.raises(failure):
                chat._trigger_turn_pre(manager)

    expected = {
        None: status.encode_report("working"),
        KeyboardInterrupt: status.encode_report("idle"),
        ValueError: status.encode_report("error"),
    }
    assert output.getvalue().endswith(expected[failure])
    assert (manager.log.messages[-1].content == "pre") is (failure is None)


def test_nested_chat_session_keeps_parent_working(output):
    """An inline subagent chat inside a parent's tool call stays silent,
    except for blocking prompts the user must answer."""

    @status.turn_status()
    def nested_turn() -> bool:
        with status.turn_start():
            pass
        with status.program_status("blocked", "permission"):
            pass
        return False

    with status.session_status():
        assert output.getvalue().endswith(status.encode_report("idle"))
        with status.program_status("working", after="done"):
            before = output.getvalue()
            with status.session_status():  # nested chat()
                nested_turn()
            assert output.getvalue() == before + "".join(
                [
                    status.encode_report("blocked", "permission"),
                    status.encode_report("working"),
                ]
            )
        assert output.getvalue().endswith(status.encode_report("done"))
    assert output.getvalue().endswith(status.encode_report("clear"))
