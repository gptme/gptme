"""Publish a complete single-file replacement without truncating its original."""

import os
import stat
import uuid
from pathlib import Path


def write_text_preserving_original(path: Path, content: str, *, newline: str) -> None:
    """Stage UTF-8 text beside its resolved destination, then replace on success.

    Symlinks retain their target semantics. Hard-linked destinations are refused:
    replacing just one name would silently split aliases. This is not an fsync or
    concurrent-writer guarantee. Existing permission bits are retained; new files
    use the ordinary 0666-with-umask policy without changing the process umask.
    """
    path = path.resolve()
    try:
        info = path.stat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Cannot replace non-regular file `{path}`")
        if info.st_nlink > 1:
            raise ValueError(f"Cannot safely replace hard-linked file `{path}`")
        # Do not silently drop security-bearing inode metadata. Extended ACLs
        # and labels are exposed as xattrs on supported POSIX filesystems.
        if hasattr(os, "listxattr") and os.listxattr(path):
            raise ValueError(
                f"Cannot safely replace file with extended metadata `{path}`"
            )
        if hasattr(os, "listxattr") and "system.posix_acl_default" in os.listxattr(
            path.parent
        ):
            raise ValueError(f"Cannot safely stage inherited ACL metadata for `{path}`")
        # Replacement requires directory access too; do not use it to bypass an
        # existing destination's write permissions.
        permission_fd = os.open(path, os.O_WRONLY)
        os.close(permission_fd)

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
