"""Persistent-shell death and restart semantics.

Every way the persistent bash can die (``exit``, ``exec``, ``kill -9 $$``,
``set -e`` + failure, closing its stdout, an external kill, a command timeout)
must:

* return promptly instead of spinning on the closed pipe until the timeout,
* restart the shell with the working directory and exported variables
  restored from the snapshot taken after the last completed command,
* queue a note that the tool response shows the model,
* never re-run the command that killed the shell (it may have executed).

A command timeout must kill the command's processes, not bash itself.
"""

import os
import signal
import sys
import time
from unittest.mock import patch

import pytest

from gptme.tools.shell import ShellSession, execute_shell

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX process semantics"
)


@pytest.fixture
def shell(tmp_path):
    sh = ShellSession()
    sh.run(f"cd {tmp_path} && export RESTART_MARKER=alive", output=False)
    yield sh
    sh.close()


def _state(sh: ShellSession) -> tuple[str, str, int]:
    rc, out, _ = sh.run("echo $PWD $RESTART_MARKER", output=False)
    assert rc == 0
    cwd, _, marker = out.strip().partition(" ")
    return cwd, marker, sh.process.pid


@pytest.mark.parametrize(
    ("cmd", "expected_rc"),
    [
        ("exit 3", 3),
        ("exec true", 0),
        ("kill -9 $$", -signal.SIGKILL),
        ("kill -- -$$", -signal.SIGTERM),
        ("set -e; false", 1),
        ("exec 1>&-", 0),
    ],
)
def test_shell_death_restarts_and_restores_state(shell, tmp_path, cmd, expected_rc):
    old_pid = shell.process.pid
    start = time.monotonic()
    rc, _, _ = shell.run(cmd, output=False)  # no timeout: must not spin
    assert time.monotonic() - start < 6.0
    assert rc == expected_rc

    cwd, marker, pid = _state(shell)
    assert pid != old_pid
    assert cwd == str(tmp_path)
    assert marker == "alive"

    notice = shell.consume_restart_notice()
    assert notice and "fresh shell" in notice
    assert "restored from a snapshot" in notice
    assert shell.consume_restart_notice() is None


def test_stdout_closed_but_shell_alive_is_restarted(shell, tmp_path):
    """``exec 1>&-; echo`` leaves bash alive but mute: restart instead of wedging."""
    old_pid = shell.process.pid
    start = time.monotonic()
    rc, _, _ = shell.run("exec 1>&-; echo hi", output=False, timeout=30)
    assert time.monotonic() - start < 8.0
    assert rc != 0
    cwd, marker, pid = _state(shell)
    assert (pid != old_pid, cwd, marker) == (True, str(tmp_path), "alive")


def test_killed_between_commands_restarts_before_next_command(shell, tmp_path):
    """External kill (OOM killer, registry close from another thread)."""
    os.kill(shell.process.pid, signal.SIGKILL)
    shell.process.wait(timeout=5)

    rc, out, _ = shell.run("echo alive-again", output=False)
    assert (rc, out.strip()) == (0, "alive-again")
    notice = shell.consume_restart_notice()
    assert notice and "before this command" in notice
    assert _state(shell)[:2] == (str(tmp_path), "alive")


def test_closed_stdin_restarts_instead_of_raising(shell):
    """close() from another thread must not leave a permanently broken shell."""
    shell.close()
    rc, out, _ = shell.run("echo back", output=False)
    assert (rc, out.strip()) == (0, "back")
    assert "before this command" in (shell.consume_restart_notice() or "")


def test_command_that_kills_shell_is_not_rerun(shell, tmp_path):
    marker = tmp_path / "ran"
    rc, _, _ = shell.run(f"{{ echo x >> {marker}; kill -9 $$; }}", output=False)
    assert rc == -signal.SIGKILL
    shell.run("true", output=False)
    assert marker.read_text() == "x\n"


def test_timeout_kills_command_but_keeps_shell(shell, tmp_path):
    pid = shell.process.pid
    start = time.monotonic()
    rc, _, _ = shell.run("sleep 30", output=False, timeout=1.0)
    assert time.monotonic() - start < 5.0
    assert rc == -124
    assert shell.process.pid == pid
    assert shell.process.poll() is None
    assert _state(shell) == (str(tmp_path), "alive", pid)
    assert shell.consume_restart_notice() is None


def test_timeout_kills_grandchildren_and_term_ignoring_children(shell, tmp_path):
    # Anchor the pgrep regex so it can't substring-match an unrelated process
    # (e.g. a concurrent `sleep 300` in a shared CI/agent container).
    pid = shell.process.pid
    rc, _, _ = shell.run(
        "bash -c 'trap \"\" TERM; (sleep 30); sleep 30'", output=False, timeout=1.0
    )
    assert rc == -124
    assert shell.process.pid == pid
    rc, out, _ = shell.run("pgrep -f 'sleep 30$' | wc -l", output=False)
    assert out.strip() == "0"


def test_timeout_fallback_when_bash_itself_stalls(shell, tmp_path):
    """A pure-bash busy loop (no child to kill) still ends in a restart."""
    pid = shell.process.pid
    start = time.monotonic()
    rc, _, _ = shell.run("while true; do :; done", output=False, timeout=1.0)
    assert rc == -124
    assert time.monotonic() - start < 8.0
    assert shell.process.pid != pid
    assert "timed out" in (shell.consume_restart_notice() or "")
    assert _state(shell)[:2] == (str(tmp_path), "alive")


def test_snapshot_failure_does_not_kill_shell_under_set_e(shell):
    """A failing state-snapshot redirect must not take bash down under `set -e`.

    The snapshot runs at the top level after the delimiter. Without ``|| true``,
    a failed redirect (read-only tmp dir, full disk) returns non-zero and, with
    sticky ``set -e``, exits the persistent shell — the exact spurious shell
    death this PR exists to eliminate.
    """
    pid = shell.process.pid
    shell._state_path = "/nonexistent-dir-xyz/state"  # redirect will fail
    shell.run("set -e", output=False)
    rc, _, _ = shell.run("echo after", output=False)
    assert rc == 0
    assert shell.process.pid == pid  # same shell, no restart
    assert shell.consume_restart_notice() is None


def test_state_file_removed_on_close():
    sh = ShellSession()
    path = sh._state_path
    assert path and os.path.exists(path)
    sh.run("true", output=False)
    sh.restart()
    assert os.path.exists(path)  # kept across restart
    sh.close()
    assert not os.path.exists(path)


def test_tool_response_carries_restart_note(tmp_path):
    from gptme.hooks.confirm import ConfirmationResult
    from gptme.tools.shell import get_shell

    sh = get_shell()
    sh.run(f"cd {tmp_path} && export RESTART_MARKER=alive", output=False)
    try:
        with patch(
            "gptme.hooks.get_confirmation",
            return_value=ConfirmationResult.confirm(),
        ):
            content = "\n".join(m.content for m in execute_shell("exit 3", [], None))
            assert "Return code: 3" in content
            assert "fresh shell" in content
            assert str(tmp_path) in content

            content = "\n".join(
                m.content
                for m in execute_shell('echo "$PWD" "$RESTART_MARKER"', [], None)
            )
            assert f"{tmp_path} alive" in content
            assert "fresh shell" not in content
    finally:
        sh.close()
