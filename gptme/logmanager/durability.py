"""Filesystem barriers for acknowledged transcript writes."""

import os
from pathlib import Path


def sync_directory(path: Path) -> None:
    """Persist directory entries on POSIX; propagate failed acknowledgements.

    Python does not expose a portable Windows directory barrier. File fsync
    still applies there, but namespace durability is not guaranteed.
    """
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
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
        path.relative_to(root)
        while path != root:
            directories.add(path)
            path = path.parent
        directories.add(root)
    for path in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        sync_directory(path)
