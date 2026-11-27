"""Unit tests for tmux's sandbox refusal.

These mock every host command, so they need neither a tmux binary nor a real
session. They live outside ``test_tools_tmux.py`` because that module skips
entirely when tmux is missing and carries a real-session cleanup fixture.
"""

import subprocess

import pytest


@pytest.mark.parametrize("backend", ["bwrap", "firejail", "docker", "wasmtime"])
@pytest.mark.parametrize(
    ("operation", "args"),
    [
        ("new_session", ("echo host-command",)),
        ("send_keys", ("gptme_1", "echo host-command Enter")),
        ("send_keys", ("%1", "echo host-command Enter")),
        ("get_sessions", ()),
        ("inspect_pane", ("gptme_1",)),
        ("kill_session", ("gptme_1",)),
        ("wait_for_output", ("gptme_1",)),
        ("get_tmux_pane_cwd", ("gptme_1",)),
    ],
)
def test_sandbox_blocks_tmux_before_host_access(monkeypatch, backend, operation, args):
    """Direct calls must not reach new or pre-existing host tmux sessions."""
    from unittest.mock import Mock

    from gptme.tools import tmux

    with monkeypatch.context() as patch:
        patch.setenv("GPTME_SANDBOX", backend)
        run = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        patch.setattr(tmux.subprocess, "run", run)
        patch.setattr(tmux, "sleep", lambda _: None)
        with pytest.raises(RuntimeError, match="tmux is disabled.*GPTME_SANDBOX"):
            getattr(tmux, operation)(*args)
        run.assert_not_called()


@pytest.mark.parametrize("backend", ["bwrap", "firejail", "docker", "wasmtime"])
def test_sandbox_disables_tmux_tool_and_cached_execution(monkeypatch, backend):
    """Availability is dynamic; a previously loaded tool also refuses execution."""
    from unittest.mock import Mock

    from gptme.tools import tmux

    with monkeypatch.context() as patch:
        patch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")
        patch.setenv("GPTME_SANDBOX", "none")
        assert tmux.tool.is_available
        patch.setenv("GPTME_SANDBOX", backend)
        assert not tmux.tool.is_available
        run = Mock()
        patch.setattr(tmux.subprocess, "run", run)
        messages = list(
            tmux.execute_tmux("new-session echo host-command", args=[], kwargs=None)
        )
        assert len(messages) == 1
        assert "tmux is disabled" in messages[0].content
        run.assert_not_called()
        patch.delenv("GPTME_SANDBOX")
        assert tmux.tool.is_available


@pytest.mark.parametrize("backend", ["docker", "wasmtime", "bwrap", "firejail"])
def test_malformed_sandbox_timeout_does_not_break_availability(monkeypatch, backend):
    """A bad unrelated setting must not raise from an availability probe.

    ``gptme --help`` and the tool listing call ``tool.available``; a malformed
    ``GPTME_SANDBOX_TIMEOUT`` used to raise ``ValueError`` there.
    """
    from gptme.tools import tmux

    with monkeypatch.context() as patch:
        patch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")
        patch.setenv("GPTME_SANDBOX", backend)
        patch.setenv("GPTME_SANDBOX_TIMEOUT", "not-a-number")
        assert tmux.tool.is_available is False
