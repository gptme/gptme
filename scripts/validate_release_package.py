#!/usr/bin/env python3
"""Validate that a built gptme package contains required runtime assets."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path

_WEBUI_INDEX = "gptme/server/webui-dist/index.html"
_WEBUI_ASSETS_PREFIX = "gptme/server/webui-dist/assets/"


def _archive_members(path: Path) -> set[str]:
    """Return normalized member names from a wheel or source archive."""
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return set(archive.namelist())
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getnames()
        # Source distributions wrap all files in a versioned root directory.
        return {name.split("/", 1)[-1] for name in members if "/" in name}
    raise ValueError(f"unsupported package archive: {path}")


def validate_package(path: Path) -> None:
    """Raise ValueError when a package omits the bundled modern web UI."""
    members = _archive_members(path)
    missing: list[str] = []
    if _WEBUI_INDEX not in members:
        missing.append(_WEBUI_INDEX)
    if not any(name.startswith(_WEBUI_ASSETS_PREFIX) for name in members):
        missing.append(f"{_WEBUI_ASSETS_PREFIX}*")
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"{path} is missing required bundled web UI assets: {joined}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path)
    args = parser.parse_args()

    try:
        for archive in args.archives:
            validate_package(archive)
            print(f"Validated bundled web UI in {archive}")
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
        parser.exit(1, f"error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
