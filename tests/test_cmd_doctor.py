"""The /doctor chat command must not parse the chat session's argv or exit it."""

import sys
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

import gptme.cli.doctor as doctor_mod
from gptme.commands.base import CommandContext
from gptme.commands.meta import cmd_doctor


def _ctx() -> CommandContext:
    return CommandContext(args=[], full_args="", manager=MagicMock())


@pytest.fixture
def failing_checks(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Diagnostics that report an error (exit code 1) without running real checks."""
    run = MagicMock(return_value=([], doctor_mod._summarize_results([])))
    monkeypatch.setattr(doctor_mod, "run_diagnostics", run)
    monkeypatch.setattr(doctor_mod, "print_results", lambda *a, **kw: 1)
    return run


def test_doctor_ignores_chat_argv_and_exit_code(
    monkeypatch: pytest.MonkeyPatch, failing_checks: MagicMock
) -> None:
    monkeypatch.setattr(sys, "argv", ["gptme", "-n", "-m", "mock/echo", "/doctor"])

    cmd_doctor(_ctx())  # must neither fail on gptme's options nor raise SystemExit

    failing_checks.assert_called_once_with(False)


def test_doctor_cli_still_exits_with_health_code(failing_checks: MagicMock) -> None:
    result = CliRunner().invoke(doctor_mod.main, [])

    assert result.exit_code == 1
    failing_checks.assert_called_once()


def test_doctor_lets_sigterm_exit_reach_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    # The chat's SIGTERM handler raises SystemExit(143); /doctor must not
    # swallow it, or the chat would ignore the shutdown request.
    run = MagicMock(side_effect=SystemExit(143))
    monkeypatch.setattr(doctor_mod, "run_diagnostics", run)

    with pytest.raises(SystemExit) as excinfo:
        cmd_doctor(_ctx())

    run.assert_called_once()
    assert excinfo.value.code == 143


def test_doctor_lets_ctrl_c_reach_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    # The chat loop handles KeyboardInterrupt; it must not become click.Abort.
    run = MagicMock(side_effect=KeyboardInterrupt)
    monkeypatch.setattr(doctor_mod, "run_diagnostics", run)

    with pytest.raises(KeyboardInterrupt):
        cmd_doctor(_ctx())

    run.assert_called_once()
