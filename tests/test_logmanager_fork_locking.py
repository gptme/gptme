"""Real POSIX fork/exec ownership checks, isolated from pytest's lock bypass."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")

# Run the orchestration in a fresh interpreter: forking the pytest runner would
# inherit unrelated plugins/threads and its own registered cleanup callbacks.
SCENARIO = r'''
import atexit
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from io import FileIO

from gptme.logmanager import LogManager
from gptme.logmanager import manager as module

root = Path(sys.argv[1])
action = sys.argv[2]
contender = """
import sys
from gptme.logmanager import LogManager
try:
    manager = LogManager(logdir=sys.argv[1])
except RuntimeError:
    sys.exit(23)
manager._release_lock()
"""

def acquired(path):
    result = subprocess.run(
        [sys.executable, "-c", contender, str(path)],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode in (0, 23), result.stderr
    return result.returncode == 0

manager = LogManager(logdir=root / "parent")
assert isinstance(manager._lock_fd, FileIO), "ownership handle must not inherit buffered I/O locks"
assert not acquired(manager.logdir), "baseline lock is not enforced"
stop = threading.Event()
ready = threading.Event()

def hold_guard():
    with module._windows_lock_owners_guard:
        ready.set()
        stop.wait(20)

# Exercise the shared registry on POSIX too. Its production users on master
# are Windows managers; cleanup's shared-POSIX ownership also uses this state.
if action in ("registry", "thread-guard"):
    module._windows_lock_owners[manager._lockfile.resolve()] = module._WindowsLockState(manager._lock_fd)
if action == "thread-guard":
    thread = threading.Thread(target=hold_guard)
    thread.start()
    assert ready.wait(5)

pid = os.fork()
if pid == 0:
    try:
        if action == "normal-exit":
            # Normal shutdown runs inherited atexit callbacks; os._exit does not.
            sys.exit(0)
        if action == "close-only":
            manager._lock_fd.close()
        elif action == "destructor":
            manager.__del__()
        elif action == "atexit":
            atexit._run_exitfuncs()
        elif action == "context":
            with manager:
                pass
        else:
            manager._release_lock()
        manager._release_lock()  # inherited repeated release is harmless
        if action in ("registry", "thread-guard"):
            assert not module._windows_lock_owners, "inherited ownership was reused"
            assert module._windows_lock_owners_guard.acquire(timeout=1), "inherited guard is held"
            module._windows_lock_owners_guard.release()
        if action == "new-manager":
            try:
                LogManager(logdir=manager.logdir)
            except RuntimeError:
                pass
            else:
                raise AssertionError("child reused parent ownership")
        own = LogManager(logdir=root / "child")
        assert not acquired(own.logdir), "child-local manager is not protected"
        own._release_lock()
        own._release_lock()
        assert acquired(own.logdir), "child-local release failed"
    except SystemExit:
        raise
    except BaseException:
        import traceback
        traceback.print_exc()
        os._exit(2)
    os._exit(0)

try:
    deadline = time.monotonic() + 35
    while True:
        waited, status = os.waitpid(pid, os.WNOHANG)
        if waited:
            assert status == 0, f"fork child failed: {status}"
            break
        if time.monotonic() >= deadline:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            raise AssertionError("fork child deadlocked")
        time.sleep(.02)
    assert not acquired(manager.logdir), "child cleanup unlocked living parent"
finally:
    stop.set()
    if action == "thread-guard":
        thread.join(5)
    manager._release_lock()
    manager._release_lock()
    # On master only Windows uses the registry; on the adjacent cleanup branch
    # parent release consumes the real shared POSIX entry itself.
    module._windows_lock_owners.pop(manager._lockfile.resolve(), None)
assert acquired(manager.logdir), "normal parent release did not admit contender"
'''


@pytest.mark.parametrize(
    "action",
    [
        "release",
        "destructor",
        "atexit",
        "normal-exit",
        "context",
        "close-only",
        "new-manager",
        "registry",
        "thread-guard",
    ],
)
def test_fork_cleanup_preserves_parent(tmp_path: Path, action: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GPTME_")}
    env.pop("PYTEST_CURRENT_TEST", None)
    env.update(
        HOME=str(tmp_path / "home"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        GPTME_LOGS_HOME=str(tmp_path / "logs"),
    )
    result = subprocess.run(
        [sys.executable, "-c", SCENARIO, str(tmp_path), action],
        env=env,
        capture_output=True,
        text=True,
        timeout=65,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


HANDOVER = r'''
import os
import signal
import subprocess
import sys
from pathlib import Path
from gptme.logmanager import LogManager
from gptme.logmanager import manager as module

root = Path(sys.argv[1])
old = LogManager(logdir=root / "conversation")
# Simulate the reused manager's key-only reference on master too. This makes
# the test cover stale callbacks touching a fresh entry regardless of merge order.
old._windows_lock_key = old._lockfile.resolve()
read, write = os.pipe()
pid = os.fork()
if pid == 0:
    try:
        signal.alarm(20)
        os.close(write)
        assert os.read(read, 1) == b"r"
        os.close(read)
        fresh = LogManager(logdir=old.logdir)
        # The adjacent cleanup PR publishes this entry itself on POSIX.
        state = module._windows_lock_owners.setdefault(
            old._windows_lock_key, module._WindowsLockState(fresh._lock_fd)
        )
        before = state.refcount
        old._release_lock()
        old.__del__()
        assert state.refcount == before, "inherited cleanup consumed child ownership"
        assert not state.fd.closed, "inherited cleanup closed child descriptor"
        result = subprocess.run(
            [sys.executable, "-c", """
import sys
from gptme.logmanager import LogManager
try:
    manager = LogManager(logdir=sys.argv[1])
except RuntimeError:
    sys.exit(23)
manager._release_lock()
""", str(old.logdir)], capture_output=True, timeout=10,
        )
        assert result.returncode == 23, result.stderr
        fresh._release_lock()
        module._windows_lock_owners.pop(fresh._lockfile.resolve(), None)
    except BaseException:
        import traceback
        traceback.print_exc()
        os._exit(2)
    os._exit(0)
os.close(read)
old._release_lock()
os.write(write, b"r")
os.close(write)
_, status = os.waitpid(pid, 0)  # child's alarm bounds failures, including deadlock
assert status == 0, f"child handover failed: {status}"
'''


def test_inherited_cleanup_cannot_release_fresh_child_owner(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GPTME_")}
    env.pop("PYTEST_CURRENT_TEST", None)
    env.update(
        HOME=str(tmp_path / "home"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        GPTME_LOGS_HOME=str(tmp_path / "logs"),
    )
    result = subprocess.run(
        [sys.executable, "-c", HANDOVER, str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=35,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
