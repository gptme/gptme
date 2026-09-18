"""Filesystem barriers for acknowledged transcript writes."""

import errno
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Errnos that mean "this filesystem has no directory barrier to offer", as
# opposed to "the write did not land". Some network, overlay and FUSE mounts
# reject fsync on a directory fd outright. A missing namespace barrier weakens
# the guarantee; it is not a reason to fail a turn whose contents were written
# and synced successfully, so these are warned about once and then tolerated.
#
# Deliberately excludes EACCES and EPERM: a permission or policy denial is not
# evidence that the filesystem lacks a barrier, and suppressing it would let the
# barrier report success having done nothing. Genuine I/O failures (EIO,
# ENOSPC, ...) propagate for the same reason.
_TOLERATED_ERRNOS = frozenset(
    {
        errno.EINVAL,
        errno.ENOSYS,
        errno.ENOTSUP,
        errno.EOPNOTSUPP,
    }
)

# Warn once per directory: this runs on every acknowledged turn.
_warned: set[Path] = set()


def _tolerate_or_raise(path: Path, error: OSError) -> None:
    if error.errno not in _TOLERATED_ERRNOS:
        raise error
    if path not in _warned:
        _warned.add(path)
        logger.warning(
            "No directory barrier available for %s (%s). Transcript contents are "
            "still synced; their directory entries are not.",
            path,
            error,
        )


def sync_directory(path: Path) -> None:
    """Persist directory entries on POSIX; propagate failed acknowledgements.

    Python does not expose a portable Windows directory barrier. File fsync
    still applies there, but namespace durability is not guaranteed.
    """
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as error:
        _tolerate_or_raise(path, error)
        return
    try:
        os.fsync(fd)
    except OSError as error:
        _tolerate_or_raise(path, error)
    finally:
        os.close(fd)


def existing_parent(path: Path) -> Path:
    """Find the established namespace above a directory before creating it."""
    parent = path.resolve().parent
    while not parent.exists():
        parent = parent.parent
    return parent


def sync_directories(paths: set[Path], root: Path) -> None:
    """Sync children before parents up to the previously existing namespace."""
    directories: set[Path] = set()
    root = root.resolve()
    for path in paths:
        path = path.resolve()
        if not path.is_relative_to(root):
            # A symlinked branch/view directory can resolve outside the logdir.
            # Sync the target itself rather than walking to the filesystem root,
            # and never fail the barrier over an unexpected layout.
            directories.add(path)
            continue
        while path != root:
            directories.add(path)
            path = path.parent
        directories.add(root)
    for path in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        sync_directory(path)
