"""The /doctor chat command must not parse the chat session's argv or exit it."""

import sys
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

import gptme.cli.doctor as doctor_mod
from gptme.cli.doctor import CheckResult, CheckStatus
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


def test_doctor_returns_2_when_config_repair_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        doctor_mod, "_repair_config_corruption", lambda: ([], ["broken config"])
    )

    assert doctor_mod.doctor(fix=True) == 2


def _patch_interactive_repair(monkeypatch: pytest.MonkeyPatch, confirm) -> None:
    """Set up ``doctor(fix=True)`` in a terminal with a repairable provider."""
    results = [
        CheckResult("Model: Default", CheckStatus.ERROR, "Provider not configured")
    ]
    monkeypatch.setattr(
        doctor_mod, "_repair_config_corruption", lambda: (["fixed config"], [])
    )
    monkeypatch.setattr(
        doctor_mod,
        "run_diagnostics",
        lambda verbose: (results, doctor_mod._summarize_results(results)),
    )
    monkeypatch.setattr(doctor_mod, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(doctor_mod, "_validate_oauth_for_repair", lambda results: None)
    monkeypatch.setattr(doctor_mod, "print_results", lambda *a, **kw: 1)
    monkeypatch.setattr(doctor_mod, "_model_override_blocking_repair", lambda: None)
    monkeypatch.setattr(
        doctor_mod,
        "_subscription_default_candidate",
        lambda results: "openai-subscription",
    )
    monkeypatch.setattr(doctor_mod.click, "confirm", confirm)


def test_doctor_keeps_health_code_when_subscription_switch_declined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_interactive_repair(monkeypatch, confirm=lambda *a, **kw: False)

    assert doctor_mod.doctor(fix=True) == 1


def test_doctor_keeps_health_code_when_provider_repair_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import click

    def _abort(*args, **kwargs):
        raise click.Abort

    _patch_interactive_repair(monkeypatch, confirm=_abort)

    assert doctor_mod.doctor(fix=True) == 1
