"""Publish a complete single-file replacement without truncating its original."""

import errno
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
        # A sibling inherits its parent's DACL, not the destination's explicit
        # Windows security descriptor. chmod cannot retain that restriction, and
        # even staging the contents could expose them before replacement.
        if os.name == "nt":
            raise ValueError(
                f"Cannot safely preserve Windows security descriptor for `{path}`"
            )
        # macOS hides security-bearing ACL metadata from listxattr(). Until a
        # native ACL-preserving implementation exists, do not replace its inode.
        if sys.platform == "darwin":
            raise ValueError(f"Cannot safely preserve macOS ACL metadata for `{path}`")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Cannot replace non-regular file `{path}`")
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
