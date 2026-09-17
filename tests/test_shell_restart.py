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
import threading
import time

import pytest

from gptme.tools.shell import ShellSession, execute_shell_impl, get_shell

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


def test_closed_pipe_preserves_existing_restart_notice(shell):
    """Two shell deaths before notice consumption must both reach the model."""
    os.kill(shell.process.pid, signal.SIGKILL)
    shell.process.wait(timeout=5)

    shell.run("exec 1>&-", output=False, timeout=30)

    notice = shell.consume_restart_notice()
    assert notice
    assert "before this command" in notice
    assert "during this command" in notice
    assert notice.index("before this command") < notice.index("during this command")


def test_killed_between_commands_restarts_before_next_command(shell, tmp_path):
    """External kill (OOM killer, registry close from another thread)."""
    os.kill(shell.process.pid, signal.SIGKILL)
    shell.process.wait(timeout=5)

    rc, out, _ = shell.run("echo alive-again", output=False)
    assert (rc, out.strip()) == (0, "alive-again")
    notice = shell.consume_restart_notice()
    assert notice and "before this command" in notice
    assert _state(shell)[:2] == (str(tmp_path), "alive")


def test_explicitly_closed_shell_stays_closed(shell):
    """SESSION_END cleanup must not be undone by a stale ContextVar reference."""
    shell.close()
    with pytest.raises(RuntimeError, match="Shell session is closed"):
        shell.run("echo back", output=False)


def test_close_racing_with_eof_does_not_restart_shell(shell):
    """SESSION_END must win when an active read sees the pipes close."""
    command_started = threading.Event()
    run_finished = threading.Event()

    def run_command():
        command_started.set()
        try:
            shell.run("sleep 30", output=False)
        finally:
            run_finished.set()

    thread = threading.Thread(target=run_command)
    thread.start()
    assert command_started.wait(timeout=1)
    time.sleep(0.1)

    shell.close()
    thread.join(timeout=5)

    assert run_finished.is_set()
    assert shell._closed
    assert shell.process.poll() is not None


def test_command_that_kills_shell_is_not_rerun(shell, tmp_path):
    marker = tmp_path / "ran"
    rc, _, _ = shell.run(f"{{ echo x >> {marker}; kill -9 $$; }}", output=False)
    assert rc == -signal.SIGKILL
    shell.run("true", output=False)
    assert marker.read_text() == "x\n"


def test_broken_pipe_zero_bytes_retries_command(shell, tmp_path, monkeypatch):
    """EPIPE before any bytes landed: the command was never received, retry once."""
    marker = tmp_path / "ran"
    orig_write = os.write
    stdin_fd = shell.process.stdin.fileno()
    raised = {"done": False}

    def write_fd(fd, data):
        if fd == stdin_fd and not raised["done"]:
            raised["done"] = True
            raise BrokenPipeError
        return orig_write(fd, data)

    monkeypatch.setattr(os, "write", write_fd)
    rc, _, _ = shell.run(f"echo x >> {marker}", output=False)
    assert rc == 0
    assert marker.read_text() == "x\n"
    notice = shell.consume_restart_notice()
    assert notice and "before this command was received" in notice
    assert shell.run("echo ok", output=False)[1].strip() == "ok"


def test_broken_pipe_partial_write_is_not_retried(shell, tmp_path, monkeypatch):
    """EPIPE mid-payload must not re-send: the statement may have run.

    Tests the no-retry invariant directly: after BrokenPipeError fires on the
    second write (the end-marker part of the shell protocol), the command bytes
    must never be re-sent to the restarted shell's stdin.

    Whether bash actually executed the delivered command before being restarted
    is inherently racy (bash scheduling), so the assertion watches stdin writes
    after the restart rather than depending on a marker file.
    """
    command = f"echo x >> {tmp_path}/ran"
    cmd_bytes = command.encode()
    orig_write = os.write
    stdin_fd = shell.process.stdin.fileno()
    state: dict = {
        "partial": False,
        "raised": False,
        "retried": False,
        "post_epipe": bytearray(),
    }

    def _is_shell_stdin(fd: int) -> bool:
        # The restarted shell gets a fresh stdin pipe; descriptor-number reuse
        # is an implementation detail, so compare against the *current* stdin
        # rather than the fd captured at setup. Otherwise a retry delivered on
        # a different fd would be missed and this test would pass vacuously.
        stdin = shell.process.stdin
        return bool(stdin) and not stdin.closed and fd == stdin.fileno()

    def write_fd(fd, data):
        # First write: deliver through the command's newline (partial write).
        if fd == stdin_fd and not state["partial"]:
            state["partial"] = True
            end = data.index(cmd_bytes) + len(cmd_bytes) + 1
            return orig_write(fd, data[:end])
        # Second write: simulate mid-payload EPIPE.
        if fd == stdin_fd and not state["raised"]:
            state["raised"] = True
            raise BrokenPipeError
        # After the restart: accumulate all stdin writes into a buffer so that
        # a retry delivered in chunks (across multiple os.write calls) is still
        # detected. A per-write substring check would miss such cases.
        if state["raised"] and _is_shell_stdin(fd):
            state["post_epipe"] += data
            if cmd_bytes in state["post_epipe"]:
                state["retried"] = True
        return orig_write(fd, data)

    monkeypatch.setattr(os, "write", write_fd)
    shell.run(command, output=False)
    assert state["partial"], (
        "write_fd never delivered the command — test is misconfigured"
    )
    assert state["raised"], (
        "BrokenPipeError was never triggered — test is misconfigured"
    )
    # The no-retry invariant: the command must not be re-sent after EPIPE.
    assert not state["retried"], (
        "command was re-sent to the restarted shell (retry bug)"
    )
    notice = shell.consume_restart_notice()
    assert notice and "during this command" in notice
    assert shell.run("echo ok", output=False)[1].strip() == "ok"


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
    # Use a unique sleep duration so pgrep doesn't match concurrent parallel test
    # workers that also use `sleep 30` (pytest-xdist runs up to 16 workers; other
    # test files start `sleep 30` processes that would cause a false count).
    pid = shell.process.pid
    rc, _, _ = shell.run(
        "bash -c 'trap \"\" TERM; (sleep 7979); sleep 7979'", output=False, timeout=1.0
    )
    assert rc == -124
    assert shell.process.pid == pid
    rc, out, _ = shell.run("pgrep -f 'sleep 7979$' | wc -l", output=False)
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

    Without ``|| true``, a failed redirect (read-only tmp dir, full disk)
    returns non-zero and, with sticky ``set -e``, exits the persistent shell —
    the exact spurious shell death this PR exists to eliminate.
    """
    pid = shell.process.pid
    shell._state_path = "/nonexistent-dir-xyz/state"  # redirect will fail
    shell.run("set -e", output=False)
    rc, _, _ = shell.run("echo after", output=False)
    assert rc == 0
    assert shell.process.pid == pid  # same shell, no restart
    assert shell.consume_restart_notice() is None


def test_snapshot_is_written_before_run_returns(shell, tmp_path):
    """A successful run must not expose stale state to an immediate restart."""
    shell.run(f"cd {tmp_path} && export SNAPSHOT_MARKER=current", output=False)
    assert shell._state_path
    with open(shell._state_path) as state_file:
        snapshot = state_file.read()
    assert f"cd -- {tmp_path}" in snapshot
    assert 'SNAPSHOT_MARKER="current"' in snapshot


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
    from gptme.tools import shell as shell_module

    sh = get_shell()
    sh.run(f"cd {tmp_path} && export RESTART_MARKER=alive", output=False)
    try:
        content = "\n".join(m.content for m in execute_shell_impl("exit 3", None))
        assert "Return code: 3" in content or "code 3" in content
        assert "fresh shell" in content
        assert str(tmp_path) in content

        content = "\n".join(
            m.content for m in execute_shell_impl('echo "$PWD" "$RESTART_MARKER"', None)
        )
        assert f"{tmp_path} alive" in content
        assert "fresh shell" not in content
    finally:
        sh.close()
        shell_module._shell_var.set(None)
