"""The /doctor chat command must not parse the chat session's argv."""

import sys
from unittest.mock import MagicMock

import pytest

import gptme.cli.doctor as doctor_mod
from gptme.commands.base import CommandContext
from gptme.commands.meta import cmd_doctor


def _ctx() -> CommandContext:
    return CommandContext(args=[], full_args="", manager=MagicMock())


def test_doctor_ignores_chat_session_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(sys, "argv", ["gptme", "-n", "-m", "mock/echo", "/doctor"])
    monkeypatch.setattr(doctor_mod.main, "callback", lambda **kw: calls.append(kw))

    cmd_doctor(_ctx())

    assert calls == [{"verbose": False, "output_json": False, "fix": False}]


def test_doctor_exit_code_does_not_end_session(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(**kw: object) -> None:
        sys.exit(1)

    monkeypatch.setattr(sys, "argv", ["gptme", "-y"])
    monkeypatch.setattr(doctor_mod.main, "callback", failing)

    cmd_doctor(_ctx())  # must not raise SystemExit
