"""Publish a complete single-file replacement without truncating its original."""

import errno
import io
import os
import stat
import sys
import uuid
from pathlib import Path


def _listxattr(path: Path) -> list[str]:
    """Inspect xattrs; Linux ENOTSUP means this filesystem has none to retain."""
    if not hasattr(os, "listxattr"):
        raise ValueError(f"Cannot inspect extended metadata for `{path}`")
    try:
        return os.listxattr(path)
    except OSError as exc:
        if sys.platform.startswith("linux") and exc.errno == errno.ENOTSUP:
            return []
        raise


def _encode(content: str, newline: str) -> bytes:
    """Encode exactly as the staged text stream would, before touching any file."""
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="utf-8", newline=newline)
    stream.write(content)
    stream.flush()
    data = buffer.getvalue()
    stream.detach()
    return data


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(errno.EIO, "Short write")
        view = view[written:]


def _rewrite_in_place(path: Path, info: os.stat_result, data: bytes) -> None:
    """Overwrite the existing inode, keeping ACLs, xattrs and security descriptors.

    The content is already encoded, so an encoding error cannot truncate the
    original. The original bytes are kept in memory: if writing or resizing fails
    partway (disk full, file size limit, I/O error), they are written back as a
    best effort before the error is re-raised. Hard links stay shared because the
    inode itself is updated.
    """
    flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        # Refuse if the name was swapped for another file after it was inspected.
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            info.st_dev,
            info.st_ino,
        ):
            raise ValueError(f"File changed while preparing to write `{path}`")
        chunks = []
        while chunk := os.read(fd, 1 << 20):
            chunks.append(chunk)
        original = b"".join(chunks)
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            _write_all(fd, data)
            os.ftruncate(fd, len(data))
        except BaseException as exc:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                _write_all(fd, original)
                os.ftruncate(fd, len(original))
            except OSError as restore_exc:
                warning = (
                    f"Restoring the original contents of `{path}` also failed "
                    f"({restore_exc}); the file may be damaged"
                )
                if isinstance(exc, OSError):
                    # Tools surface only str(error): put the warning in it.
                    raise OSError(exc.errno, f"{exc}; {warning}") from exc
                exc.add_note(warning)
            raise
    finally:
        os.close(fd)


def write_text_preserving_original(path: Path, content: str, *, newline: str) -> None:
    """Stage UTF-8 text beside its resolved destination, then replace on success.

    Symlinks retain their target semantics. Hard-linked destinations are refused:
    replacing just one name would silently split aliases. This is not an fsync or
    concurrent-writer guarantee. Existing permission bits are retained; new files
    use the ordinary 0666-with-umask policy without changing the process umask.

    On macOS and Windows an existing file is rewritten in place instead (see
    `_rewrite_in_place`), because replacing its inode could drop ACLs or the
    security descriptor.
    """
    path = path.resolve()
    try:
        info = path.stat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Cannot replace non-regular file `{path}`")
        # A sibling inherits its parent's DACL, not the destination's explicit
        # Windows security descriptor, and macOS hides security-bearing ACL
        # metadata from listxattr(). Replacing the inode could silently drop
        # either, so rewrite the existing inode, which keeps all of it.
        if os.name == "nt" or sys.platform == "darwin":
            _rewrite_in_place(path, info, _encode(content, newline))
            return
        if info.st_nlink > 1:
            raise ValueError(f"Cannot safely replace hard-linked file `{path}`")
        # Do not silently drop security-bearing inode metadata. Linux extended
        # ACLs and labels are exposed as xattrs; inspection errors fail closed.
        if _listxattr(path):
            raise ValueError(
                f"Cannot safely replace file with extended metadata `{path}`"
            )
        if "system.posix_acl_default" in _listxattr(path.parent):
            raise ValueError(f"Cannot safely stage inherited ACL metadata for `{path}`")
        # Replacement requires directory access too; do not use it to bypass an
        # existing destination's write permissions.
        permission_fd = os.open(path, os.O_WRONLY)
        os.close(permission_fd)

    if os.name == "posix":
        parent = path.parent.stat()
        # A different directory owner can change its mode; group/other writers
        # can replace the staging name even while its private fd remains open.
        # POSIX ACL write grants are also reflected in the group class mask.
        if parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
            raise ValueError(
                f"Cannot safely stage in directory writable by other users `{path.parent}`"
            )

    temporary = path.with_name(f".gptme-write-{uuid.uuid4().hex}.tmp")
    mode = 0o600 if info is not None else 0o666
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        try:
            if info is not None and os.name == "posix":
                staged = os.fstat(fd)
                if (staged.st_uid, staged.st_gid) != (info.st_uid, info.st_gid):
                    raise ValueError(
                        f"Cannot safely preserve ownership metadata for `{path}`"
                    )
            stream = os.fdopen(fd, "w", encoding="utf-8", newline=newline)
        except BaseException:
            os.close(fd)
            raise
        with stream:
            stream.write(content)
        if info is not None:
            os.chmod(temporary, stat.S_IMODE(info.st_mode))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
