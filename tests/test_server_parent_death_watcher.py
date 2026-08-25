"""Tests for the gptme-server parent-death watcher (gptme/gptme#2260)."""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from gptme.server.cli import (
    _install_sigterm_handler,
    _pid_alive,
    _start_parent_death_watcher,
)


def test_pid_alive_for_self():
    assert _pid_alive(os.getpid())


def test_pid_alive_returns_false_for_dead_pid():
    # PID 0 / very high never-allocated PIDs always look dead.
    # PID 1 is init/launchd, always alive on POSIX, so use a guaranteed-dead pid.
    assert not _pid_alive(2_147_483_646)


def test_watcher_skips_when_already_orphaned():
    # When watch_pid <= 1 we treat as already-orphaned and don't spawn the thread.
    threads_before = threading.active_count()
    _start_parent_death_watcher(watch_pid=1, poll_interval=0.01)
    _start_parent_death_watcher(watch_pid=0, poll_interval=0.01)
    # Give any (incorrectly-spawned) thread time to start.
    time.sleep(0.05)
    assert threading.active_count() == threads_before


def test_watcher_sends_sigterm_when_watched_pid_disappears():
    """When the watched PID is gone the watcher SIGTERMs the current process."""
    received: list[int] = []
    original = signal.getsignal(signal.SIGTERM)

    def _handler(signum, _frame):
        received.append(signum)

    signal.signal(signal.SIGTERM, _handler)
    try:
        # _pid_alive returns False immediately → watcher fires SIGTERM on the
        # first poll. Use a dead PID so we don't depend on real process state.
        with patch("gptme.server.cli._pid_alive", return_value=False):
            _start_parent_death_watcher(watch_pid=99_999_999, poll_interval=0.01)
            # Wait for the watcher to fire.
            for _ in range(50):
                if received:
                    break
                time.sleep(0.01)
        assert received == [signal.SIGTERM]
    finally:
        signal.signal(signal.SIGTERM, original)


def test_install_sigterm_handler_raises_keyboardinterrupt():
    """SIGTERM should re-raise as KeyboardInterrupt so the server's graceful
    shutdown path (Werkzeug catches KeyboardInterrupt → `finally` cleanup runs)
    fires on `systemctl stop` / container scale-down, not just on Ctrl+C."""
    original = signal.getsignal(signal.SIGTERM)
    try:
        # Reset to SIG_DFL so the callable-preservation guard doesn't skip
        # installation if a previous test left a callable handler in place.
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        _install_sigterm_handler()
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        # Invoking the handler directly must raise KeyboardInterrupt, which is
        # what routes SIGTERM into Werkzeug's clean-shutdown path.
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, original)


def _run_sigterm_probe(setup: str, check: str) -> subprocess.CompletedProcess:
    """Exercise ``_install_sigterm_handler()`` in a pristine subprocess.

    Signal dispositions are process-global, so an in-process test would be at
    the mercy of whatever else in the suite touched SIGTERM.  A subprocess also
    pins PYTHONPATH to the repo root so a pre-installed site-packages gptme
    cannot shadow the worktree package.
    """
    repo_root = str(Path(__file__).resolve().parents[1])
    env = os.environ.copy()
    env["PYTHONPATH"] = repo_root + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import signal, sys;"
                # Import FIRST: the module-level guard also installs a handler,
                # so applying `setup` before it would test that guard instead
                # of _install_sigterm_handler() (which is what this file owns).
                "from gptme.server.cli import _install_sigterm_handler;"
                f"{setup}"
                "_install_sigterm_handler();"
                "h = signal.getsignal(signal.SIGTERM);"
                f"sys.exit(0 if {check} else 1)"
            ),
        ],
        capture_output=True,
        timeout=30,
        check=False,
        cwd=repo_root,
        env=env,
    )


def test_install_sigterm_handler_preserves_custom_callable_handler():
    """A callable handler is the only disposition that proves embedder intent.

    POSIX resets callable handlers to ``SIG_DFL`` across ``execve``, so one that
    is still installed can only have been set by the current process — a
    deliberate choice that ``_install_sigterm_handler()`` must leave intact
    (gptme/gptme#3597 P2).
    """
    result = _run_sigterm_probe(
        setup="marker = lambda s, f: None;signal.signal(signal.SIGTERM, marker);",
        check="h is marker",
    )
    assert result.returncode == 0, (
        "Expected a custom callable SIGTERM handler to survive "
        "_install_sigterm_handler(), but the upgrade overrode it "
        "(gptme/gptme#3597 P2).\n"
        f"stdout: {result.stdout.decode(errors='replace')}\n"
        f"stderr: {result.stderr.decode(errors='replace')}"
    )


def test_install_sigterm_handler_upgrades_inherited_sig_ign():
    """``SIG_IGN`` is treated as vacant, exactly like ``SIG_DFL``.

    Unlike a callable handler, ``SIG_IGN`` *is* inherited across ``execve``, so
    a daemon supervisor or test runner that ignores SIGTERM silently imposes it
    on every child.  Honouring that as an embedder choice would disable the
    graceful-shutdown path from gptme/gptme#3589 for those servers, so we
    upgrade it (gptme/gptme#3597 P2).
    """
    result = _run_sigterm_probe(
        setup="signal.signal(signal.SIGTERM, signal.SIG_IGN);",
        check="callable(h)",
    )
    assert result.returncode == 0, (
        "Expected an inherited SIG_IGN to be upgraded to the graceful-shutdown "
        "handler, but it survived _install_sigterm_handler() "
        "(gptme/gptme#3589).\n"
        f"stdout: {result.stdout.decode(errors='replace')}\n"
        f"stderr: {result.stderr.decode(errors='replace')}"
    )
