"""Tests for streaming one-shot output in _run_with_tty.

The persistent-session path (_run_pipe → _read_output_unix) has always streamed
output incrementally. _run_with_tty used proc.communicate() which is fully
blocking: nothing appears until the process exits. This file tests the fix.
"""

import os
import signal
import threading
import time

import pytest

from gptme.tools.shell import ShellSession


@pytest.fixture
def shell():
    s = ShellSession()
    yield s
    s.close()


def test_run_with_tty_basic(shell):
    """_run_with_tty returns correct output and return code."""
    ret, out, err = shell._run_with_tty("echo hello", output=False)
    assert ret == 0
    assert "hello" in out


def test_run_with_tty_multi_line(shell):
    """_run_with_tty collects all lines of output."""
    ret, out, err = shell._run_with_tty(
        "echo line1; echo line2; echo line3", output=False
    )
    assert ret == 0
    assert "line1" in out
    assert "line2" in out
    assert "line3" in out


def test_run_with_tty_collects_output_queued_at_exit(shell):
    """Fast process exit cannot race the reader threads and truncate output."""
    expected_size = 2**20
    ret, out, err = shell._run_with_tty(
        f"head -c {expected_size} /dev/zero | tr '\\0' x", output=False
    )
    assert ret == 0
    assert out == "x" * expected_size
    assert err == ""


def test_run_with_tty_drains_buffered_output_after_slow_consumer(shell, monkeypatch):
    """Reader cleanup does not truncate output that takes over a second to drain."""
    real_read = os.read

    def slow_read(fd, size):
        time.sleep(0.02)
        return real_read(fd, min(size, 4096))

    monkeypatch.setattr(os, "read", slow_read)
    expected_size = 256 * 1024
    ret, out, err = shell._run_with_tty(
        f"python3 -c 'import sys; sys.stdout.write(\"x\" * {expected_size})'",
        output=False,
    )

    assert ret == 0
    assert out == "x" * expected_size
    assert err == ""


def test_run_with_tty_does_not_timeout_after_direct_child_exits(shell):
    """A descendant retaining the pipe cannot turn child success into timeout."""
    ret, out, err = shell._run_with_tty(
        "(while true; do printf x; sleep 0.01; done) & echo foreground",
        output=False,
        timeout=0.2,
    )

    assert ret == 0
    assert "foreground" in out
    assert err == ""


def test_run_with_tty_does_not_wait_for_background_descendant(shell):
    """A descendant retaining the output pipes does not hang the caller."""
    started = time.monotonic()
    ret, out, err = shell._run_with_tty("sleep 10 & echo foreground", output=False)
    elapsed = time.monotonic() - started

    assert ret == 0
    assert out == "foreground"
    assert err == ""
    assert elapsed < 3


def test_run_with_tty_continuous_descendant_output_has_bounded_grace(shell):
    """Continuous descendant output cannot extend the post-exit grace forever."""
    started = time.monotonic()
    ret, out, err = shell._run_with_tty(
        "(while true; do printf x; sleep 0.01; done) & echo foreground",
        output=False,
    )
    elapsed = time.monotonic() - started

    assert ret == 0
    assert "foreground" in out
    assert err == ""
    assert elapsed < 3


def test_run_with_tty_closed_output_still_honors_timeout(shell):
    """EOF on both output pipes does not bypass the process timeout."""
    started = time.monotonic()
    ret, out, err = shell._run_with_tty(
        "exec 1>&-; exec 2>&-; sleep 10", output=False, timeout=0.3
    )
    elapsed = time.monotonic() - started

    assert ret == -124
    assert out == ""
    assert err == ""
    assert elapsed < 3


def test_run_with_tty_preserves_utf8_split_across_read_chunks(shell, monkeypatch):
    """Streaming uses incremental decoders for split multibyte characters."""
    real_read = os.read
    first_stdout_read = True

    def split_read(fd, size):
        nonlocal first_stdout_read
        if first_stdout_read and size == 2**16:
            first_stdout_read = False
            size = 1
        return real_read(fd, size)

    monkeypatch.setattr(os, "read", split_read)
    ret, out, err = shell._run_with_tty("printf 'é'", output=False)

    assert ret == 0
    assert out == "é"
    assert err == ""


def test_run_with_tty_stderr(shell):
    """_run_with_tty captures stderr separately."""
    ret, out, err = shell._run_with_tty(
        "echo stdout_msg; echo stderr_msg >&2", output=False
    )
    assert ret == 0
    assert "stdout_msg" in out
    assert "stderr_msg" in err
    assert "stderr_msg" not in out


def test_run_with_tty_nonzero_exit(shell):
    """_run_with_tty returns the correct non-zero exit code."""
    ret, _out, _err = shell._run_with_tty("exit 42", output=False)
    assert ret == 42


def test_run_with_tty_timeout_returns_partial_output(shell):
    """Streaming is verified: output collected before timeout fires is returned.

    With the old proc.communicate() approach: communicate() blocks until the
    process is killed, so partial output visibility was coincidental (the drain
    after kill happened to collect it). With the new reader-thread loop, output is
    stored in chunks as it arrives, so the timeout path always returns whatever
    was printed before the timeout.

    The key assertion is that 'partial_line' appears in stdout DESPITE the
    command never finishing — proving the read loop collected it mid-flight.
    """
    ret, out, err = shell._run_with_tty(
        "printf partial_line; exec sleep 10",
        output=False,
        timeout=0.5,
    )
    assert ret == -124, f"Expected timeout exit code -124, got {ret}"
    assert "partial_line" in out, (
        f"Expected partial output before timeout, got stdout={out!r}"
    )


def test_run_with_tty_timeout_no_output_still_returns_minus_124(shell):
    """Timeout with no output still returns -124 and empty strings (not crash)."""
    ret, out, err = shell._run_with_tty("exec sleep 10", output=False, timeout=0.3)
    assert ret == -124
    assert isinstance(out, str)
    assert isinstance(err, str)


def test_run_with_tty_timeout_does_not_wait_for_background_descendant(shell):
    """Timeout returns even when a descendant retains the output pipes."""
    started = time.monotonic()
    ret, out, err = shell._run_with_tty(
        "sleep 10 & printf before-timeout; exec sleep 10",
        output=False,
        timeout=0.3,
    )
    elapsed = time.monotonic() - started

    assert ret == -124
    assert out == "before-timeout"
    assert err == ""
    assert elapsed < 3


def test_run_with_tty_interrupt_returns_all_partial_output(shell):
    """KeyboardInterrupt reaps the child and drains output already produced."""

    def interrupt() -> None:
        time.sleep(0.2)
        os.kill(os.getpid(), signal.SIGINT)

    interrupter = threading.Thread(target=interrupt)
    interrupter.start()
    with pytest.raises(KeyboardInterrupt) as exc_info:
        shell._run_with_tty("printf before-interrupt; exec sleep 10", output=False)
    interrupter.join()

    stdout, stderr = exc_info.value.args[0]
    assert stdout == "before-interrupt"
    assert stderr == ""


def test_run_with_tty_interrupt_does_not_wait_for_background_descendant(shell):
    """KeyboardInterrupt surfaces promptly when a descendant retains the pipes."""

    def interrupt() -> None:
        time.sleep(0.2)
        os.kill(os.getpid(), signal.SIGINT)

    interrupter = threading.Thread(target=interrupt)
    interrupter.start()
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt) as exc_info:
        shell._run_with_tty(
            "sleep 10 & printf before-interrupt; exec sleep 10", output=False
        )
    interrupter.join()

    elapsed = time.monotonic() - started
    stdout, stderr = exc_info.value.args[0]
    assert stdout == "before-interrupt"
    assert stderr == ""
    assert elapsed < 3
