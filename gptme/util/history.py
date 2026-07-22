"""Prompt-toolkit-compatible persistent input history."""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

from prompt_toolkit.history import FileHistory

try:
    fcntl: Any = importlib.import_module("fcntl")
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:
    msvcrt: Any = importlib.import_module("msvcrt")
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None


@contextmanager
def _history_lock(path: Path) -> Iterator[None]:
    """Lock the stable sidecar for all gptme history writers."""
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - Windows
            lock_file.seek(0)
            lock_file.write(b"\0")
            lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


class LockedFileHistory(FileHistory):
    """A ``FileHistory`` whose reads and writes share a portable lock."""

    def __init__(self, filename: str | Path) -> None:
        self.path = Path(filename)
        super().__init__(str(self.path))

    def load_history_strings(self) -> Iterable[str]:
        with _history_lock(self.path):
            return list(super().load_history_strings())

    def store_string(self, string: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _history_lock(self.path):
            super().store_string(string)
            # FileHistory closes the stream before returning, so its complete
            # entry is flushed before the lock is released.


def load_history(path: Path) -> list[str]:
    """Read prompt-toolkit history entries oldest-first."""
    try:
        return list(reversed(list(LockedFileHistory(path).load_history_strings())))
    except OSError:
        return []


def append_history(path: Path, text: str) -> None:
    """Append one prompt-toolkit history entry under the shared writer lock."""
    LockedFileHistory(path).store_string(text)
