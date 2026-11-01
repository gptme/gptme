import os
import subprocess
import sys
from pathlib import Path

import pytest

from gptme.logmanager import LogManager


def _probe_other_process(logdir: Path) -> subprocess.CompletedProcess[str]:
    code = (
        "import sys; from pathlib import Path; "
        "from gptme.logmanager import LogManager; "
        "manager = LogManager(logdir=Path(sys.argv[1])); "
        "manager._release_lock()"
    )
    return subprocess.run(
        [sys.executable, "-c", code, str(logdir)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific msvcrt locking behavior")
def test_same_process_conversation_lock_lifetime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same-process users must keep the shared Windows lock until the last release."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    logdir = tmp_path / "conversation"

    first = LogManager(logdir=logdir)
    second = LogManager(logdir=logdir)
    try:
        first._release_lock()

        blocked = _probe_other_process(logdir)
        assert blocked.returncode != 0
        assert "Another gptme instance is using" in blocked.stderr
    finally:
        second._release_lock()
        first._release_lock()

    available = _probe_other_process(logdir)
    assert available.returncode == 0, available.stderr


def test_del_swallows_shutdown_attribute_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """__del__ must not raise if release_lock hits globals cleared during
    interpreter shutdown (reproduces the hazard without depending on Windows
    or actual interpreter teardown)."""
    logdir = tmp_path / "conversation"
    manager = LogManager(logdir=logdir)

    def boom(self: LogManager) -> None:
        raise AttributeError("module global cleared during shutdown")

    monkeypatch.setattr(LogManager, "_release_lock", boom)
    manager.__del__()  # must not propagate

    monkeypatch.undo()
    manager._release_lock()


def test_windows_registry_reuse_with_raw_handles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise Windows registry logic on every platform, not native OS trust."""
    from io import FileIO
    from types import SimpleNamespace

    from gptme.logmanager import manager as module

    first = LogManager(logdir=tmp_path / "conversation", lock=False)
    second = LogManager(logdir=first.logdir, lock=False)
    for manager in (first, second):
        manager._lockfile = manager.logdir / ".lock"
        manager._lockfile.touch(exist_ok=True)
        manager._lock_fd = FileIO(manager._lockfile, "r+")
    unlocks: list[int] = []
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt", getpid=os.getpid))
    monkeypatch.setattr(LogManager, "_platform_lock", staticmethod(lambda fd: None))
    monkeypatch.setattr(
        LogManager,
        "_platform_unlock",
        staticmethod(lambda fd: unlocks.append(fd.fileno())),
    )
    try:
        assert first._acquire_lock_fd()
        assert not second._acquire_lock_fd()
        state = module._windows_lock_owners[first._lockfile.resolve()]
        assert state.refcount == 2
        assert isinstance(state.fd, FileIO)
        assert first._lockfile.read_text() == str(os.getpid())
        first._release_lock()
        first._release_lock()
        assert state.refcount == 1
        assert not state.fd.closed
        assert not unlocks
        second._release_lock()
        second._release_lock()
        assert state.fd.closed
        assert len(unlocks) == 1
        assert first._lockfile.resolve() not in module._windows_lock_owners
    finally:
        first._release_lock()
        second._release_lock()


def test_partial_constructor_cleanup_is_harmless() -> None:
    manager = LogManager.__new__(LogManager)
    manager.__del__()
