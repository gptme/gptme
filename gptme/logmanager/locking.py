"""Short-lived lifecycle gate shared by startup, queue publication and cleanup.

Gate files live outside the removable directory and are never unlinked: waiters
must keep referring to the same inode even after a conversation is deleted.
"""

import errno
import hashlib
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def conversation_lifecycle_lock(
    logdir: Path, *, blocking: bool = True
) -> Iterator[None]:
    from .manager import LogManager, _msvcrt, fcntl

    if fcntl is None and _msvcrt is None:
        raise OSError("Conversation lifecycle locking is unavailable")
    logdir = logdir.resolve()
    lockdir = logdir.parent / ".conversation-locks"
    lockdir.mkdir(exist_ok=True)
    key = hashlib.sha256(logdir.name.encode()).hexdigest()
    with (lockdir / key).open("a+") as fd:
        # Windows byte-range locks may cover bytes beyond EOF. Do not initialise
        # byte zero before locking: a concurrent first opener may already own it.
        while True:
            fd.seek(0)
            try:
                LogManager._platform_lock(fd)
                break
            except OSError as exc:
                if not blocking or exc.errno not in (
                    errno.EACCES,
                    errno.EAGAIN,
                    errno.EDEADLK,
                ):
                    raise
                time.sleep(0.01)
        try:
            yield
        finally:
            LogManager._platform_unlock(fd)


@contextmanager
def unowned_conversation(logdir: Path) -> Iterator[None]:
    """Strictly acquire the real lifetime lock, never reuse a same-process owner.

    The caller holds the lifecycle gate. Close before removal for Windows;
    startup cannot acquire a replacement lifetime lock until the gate releases.
    """
    from .manager import LogManager

    lockfile = logdir / ".lock"
    if not lockfile.exists():
        yield
        return
    with lockfile.open("r+") as fd:
        fd.seek(0)
        LogManager._platform_lock(fd)
        try:
            yield
        finally:
            LogManager._platform_unlock(fd)
