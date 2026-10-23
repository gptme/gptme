"""Shared attachment copying and reference remapping for conversation forks."""

import shutil
from dataclasses import replace
from pathlib import Path

from ..message import Message
from ..util.file_storage import get_stored_path
from ..util.uri import URI, FilePath


def _fork_message_file_reference(
    file_ref: FilePath, source_attachments: Path, dest_attachments: Path
) -> tuple[FilePath, bool]:
    """Re-root attachment file paths into the forked conversation when needed."""
    if isinstance(file_ref, URI):
        return file_ref, False

    path = Path(file_ref)
    try:
        rel = path.relative_to(source_attachments)
        return dest_attachments / rel, True
    except ValueError:
        pass

    if path.parts[:1] == ("attachments",):
        return dest_attachments / Path(*path.parts[1:]), True

    return path, False


def copy_messages_for_fork(
    messages: list[Message],
    source_logdir: Path,
    dest_logdir: Path,
    *,
    files_already_copied: bool = False,
) -> list[Message]:
    """Copy a message slice with its attachments and content-addressed files.

    CLI forks copy the whole file trees first, including unreferenced files.
    In that case only remap references; do not copy through preserved symlinks.
    """
    source_attachments = source_logdir / "attachments"
    dest_attachments = dest_logdir / "attachments"
    attachment_copies: set[tuple[Path, Path]] = set()
    snapshot_copies: set[tuple[Path, Path]] = set()
    copied_messages: list[Message] = []

    for msg in messages:
        new_files: list[FilePath] = []
        path_map: dict[str, str] = {}
        for file_ref in msg.files:
            new_ref, copied = _fork_message_file_reference(
                file_ref, source_attachments, dest_attachments
            )
            new_files.append(new_ref)
            if copied:
                assert isinstance(new_ref, Path)
                path_map[str(file_ref)] = str(new_ref)
                source_path = source_attachments / new_ref.relative_to(dest_attachments)
                attachment_copies.add((source_path, new_ref))

        new_file_hashes = {
            path_map.get(path, path): digest for path, digest in msg.file_hashes.items()
        }
        dead_refs: set[str] = set()
        for path, digest in msg.file_hashes.items():
            source_snapshot = get_stored_path(source_logdir, digest, Path(path).suffix)
            if source_snapshot is not None:
                snapshot_copies.add(
                    (source_snapshot, dest_logdir / "files" / source_snapshot.name)
                )
            else:
                # Snapshot not found in source: drop the stale hash so the
                # fork's LogManager.snapshot_message_files re-stores from the
                # live file with a fresh consistent hash, rather than silently
                # overwriting the stale hash with a potentially different one.
                new_path = path_map.get(path, path)
                new_file_hashes.pop(new_path, None)
                # If the live file is also gone, drop the dangling file
                # reference so the fork's LogManager never sees a reference it
                # cannot snapshot or resolve for the provider.
                live_path = Path(path)
                if path in path_map:
                    live_path = source_attachments / Path(new_path).relative_to(
                        dest_attachments
                    )
                if not live_path.is_file():
                    dead_refs.add(new_path)
        filtered_files = [f for f in new_files if str(f) not in dead_refs]
        copied_messages.append(
            replace(msg, files=filtered_files, file_hashes=new_file_hashes)
        )

    if files_already_copied:
        return copied_messages

    for source_path, dest_path in attachment_copies:
        if not source_path.exists():
            continue
        if source_path.is_dir():
            shutil.copytree(source_path, dest_path, dirs_exist_ok=True)
        else:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, dest_path)

    for source_path, dest_path in snapshot_copies:
        if not source_path.exists():
            continue
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, dest_path)

    return copied_messages
