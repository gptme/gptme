"""Server conversations retain separate Python kernels across request contexts."""

from contextvars import Context
from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.hooks import current_conversation_id
from gptme.tools.python_kernel import get_backend, reset_backend


def _context(conversation: str) -> Context:
    context = Context()
    context.run(current_conversation_id.set, conversation)
    return context


def test_kernel_variables_are_private_to_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("jupyter_client")
    pytest.importorskip("ipykernel")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GPTME_SHELL_MEMORY_GUARD", "0")
    a, b = _context("python-state-a"), _context("python-state-b")
    try:
        first = a.run(get_backend)
        second = b.run(get_backend)
        assert first is not second
        assert a.run(first.execute, "secret = 'only a'").error is None
        result = b.run(second.execute, "'secret' in globals()")
        assert result.error is None
        assert result.result_text == "False"
        assert b.run(second.execute, "secret = 'only b'").error is None
        # A new request context for the same conversation retains its state.
        later = _context("python-state-a")
        assert later.run(get_backend) is first
        result = later.run(first.execute, "secret")
        assert result.error is None
        assert result.result_text == "'only a'"
    finally:
        for context in (a, b):
            context.run(reset_backend)


def test_conversations_and_cli_have_separate_backends() -> None:
    a, b, cli = _context("python-owner-a"), _context("python-owner-b"), Context()
    try:
        first, second, cli_backend = (c.run(get_backend) for c in (a, b, cli))
        assert len({id(first), id(second), id(cli_backend)}) == 3
        assert _context("python-owner-a").run(get_backend) is first
    finally:
        for context in (a, b, cli):
            context.run(reset_backend)


@pytest.mark.parametrize("cli_choice", [False, True])
def test_backend_choice_is_frozen_per_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_choice: bool
) -> None:
    from gptme.tools import python as py

    project, plain = tmp_path / "project", tmp_path / "plain"
    (project / ".venv").mkdir(parents=True)
    plain.mkdir()
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(py, "_IPYTHON_BACKEND", "auto")
    monkeypatch.setattr(py, "_kernel_mode_cache", cli_choice)
    monkeypatch.setattr(py, "_kernel_mode_frozen", True)
    a, b = _context("python-choice-a"), _context("python-choice-b")
    try:
        with patch.object(py.importlib.util, "find_spec", return_value=object()):
            monkeypatch.chdir(project)
            assert a.run(py._resolve_kernel_backend) is True
            monkeypatch.chdir(plain)
            assert b.run(py._resolve_kernel_backend) is False
            assert _context("python-choice-a").run(py._resolve_kernel_backend) is True
            monkeypatch.chdir(project)
            assert b.run(py._resolve_kernel_backend) is False
            assert py._kernel_mode_cache is cli_choice
    finally:
        for context in (a, b):
            context.run(reset_backend)


@pytest.mark.parametrize("guard", ["idle", "live_session", "active_command"])
def test_conversation_kernel_cleanup_respects_live_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guard: str
) -> None:
    pytest.importorskip("flask")
    from gptme.server.session_models import SessionManager

    a, b = _context("python-cleanup-a"), _context("python-cleanup-b")
    monkeypatch.setattr(
        SessionManager,
        "_conversation_sessions",
        {"python-cleanup-a": {"session"}} if guard == "live_session" else {},
    )
    monkeypatch.setattr(
        SessionManager,
        "_active_commands",
        {"python-cleanup-a": 1} if guard == "active_command" else {},
    )
    try:
        first, other = a.run(get_backend), b.run(get_backend)
        SessionManager._evict_idle_conversation_state(
            "python-cleanup-a", tmp_path / "a"
        )
        replacement = a.run(get_backend)
        if guard == "idle":
            assert replacement is not first
            assert first._bridge._sock is None
        else:
            assert replacement is first
            assert first._bridge._sock is not None
        assert b.run(get_backend) is other
    finally:
        for context in (a, b):
            context.run(reset_backend)
