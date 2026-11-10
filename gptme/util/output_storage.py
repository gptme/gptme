"""
Utilities for storing large tool outputs before truncation.

Saves full output under the conversation-owned ``tool-outputs/`` namespace and
provides a path reference for later retrieval.

Writer safety (see ``docs/design/file-backed-output.md``, slice 1).
Slice 2 live spools use :func:`open_private_output_file` for exclusive
append-owned files that share the same containment and permission rules:

- ``output_type`` is reduced to a single safe path component.
- Directories created here are private (``0700``) and files are created
  privately (``0600``) with exclusive, unguessable temporary names.
- Where the platform supports it, traversal and creation are descriptor-relative
  and refuse symlinked or non-directory ``tool-outputs`` components.
- Content is written to a private temporary file and then published under its
  final name via hard link, without overwriting any existing file; a failed
  write leaves no final file behind. Filesystems that reject hard links fall
  back to exclusive creation of the final name (no-clobber, not atomic).
- Distinct calls keep distinct files: a same-second, same-content collision gets
  a numeric suffix instead of silently reusing (or clobbering) the earlier file.
"""

import codecs
import errno
import hashlib
import logging
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

TOOL_OUTPUTS_DIRNAME = "tool-outputs"

_UNSAFE_TYPE_CHARS = re.compile(r"[^A-Za-z0-9_.-]")
_MAX_TYPE_LEN = 64
_MAX_NAME_LEN = 128
_MAX_COLLISION_SUFFIX = 1000

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)

_DIR_OPEN_FLAGS = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
_FILE_CREATE_FLAGS = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC | _O_BINARY
)

# Descriptor-relative traversal needs dir_fd support for every call we make
# plus O_DIRECTORY/O_NOFOLLOW to refuse symlinked components (POSIX).
_USE_DIR_FD = bool(_O_DIRECTORY and _O_NOFOLLOW) and all(
    fn in os.supports_dir_fd for fn in (os.open, os.mkdir, os.unlink, os.link)
)


class UnsafeOutputPathError(OSError):
    """Raised when the output directory is a symlink or not a directory."""


def _safe_output_type(output_type: str) -> str:
    """Reduce ``output_type`` to a single, non-hidden path component."""
    cleaned = _UNSAFE_TYPE_CHARS.sub("_", output_type or "")[:_MAX_TYPE_LEN]
    cleaned = cleaned.lstrip(".")
    return cleaned or "tool"


def _safe_filename(name: str) -> str:
    """Reduce a file name to a single, non-hidden path component."""
    cleaned = _UNSAFE_TYPE_CHARS.sub("_", name or "")[:_MAX_NAME_LEN]
    cleaned = cleaned.lstrip(".")
    return cleaned or "output"


class PrivateOutputFile:
    """Exclusive private file under ``tool-outputs``, owned by a held fd.

    Created once (``O_EXCL``, mode ``0600``). Later ``append``/``replace``
    calls use that descriptor, so a swapped symlink at the path cannot
    redirect writes. Slice 2 live spools append; snapshot job logs replace.
    """

    def __init__(self, path: Path, fd: int):
        self.path = path
        self._fd = fd
        self._closed = False
        self.bytes_written = 0

    def append(self, data: bytes) -> None:
        if self._closed:
            raise ValueError("append to closed PrivateOutputFile")
        if not data:
            return
        view = memoryview(data)
        while view:
            written = os.write(self._fd, view)
            if written <= 0:  # pragma: no cover - os.write raises instead
                raise OSError(errno.EIO, "short write while appending tool output")
            view = view[written:]
            self.bytes_written += written

    def replace(self, data: bytes) -> None:
        if self._closed:
            raise ValueError("replace on closed PrivateOutputFile")
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.ftruncate(self._fd, 0)
        self.bytes_written = 0
        self.append(data)
        os.fsync(self._fd)

    def flush(self) -> None:
        if not self._closed:
            os.fsync(self._fd)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            os.fsync(self._fd)
        finally:
            os.close(self._fd)

    def __del__(self) -> None:  # pragma: no cover - best-effort fd leak guard
        try:
            self.close()
        except Exception:
            pass


def _surrogateescape_or_replace(exc: UnicodeError) -> tuple[bytes, int]:
    """Codec error handler: restore surrogateescaped bytes, replace the rest.

    Applied per offending character, so one unrelated lone surrogate does not
    downgrade recoverable ``surrogateescape`` bytes elsewhere in the output.
    """
    if not isinstance(exc, UnicodeEncodeError):
        raise exc
    out = bytearray()
    for ch in exc.object[exc.start : exc.end]:
        cp = ord(ch)
        out += bytes([cp - 0xDC00]) if 0xDC80 <= cp <= 0xDCFF else b"?"
    return bytes(out), exc.end


_ENCODE_ERRORS = "gptme-output-storage-surrogates"
codecs.register_error(_ENCODE_ERRORS, _surrogateescape_or_replace)


def _encode(content: str) -> bytes:
    """UTF-8 bytes for ``content``; never depends on the locale encoding.

    Surrogateescaped characters (U+DC80..U+DCFF) are restored to their original
    bytes; any other lone surrogate is replaced with ``?`` individually.
    """
    return content.encode("utf-8", errors=_ENCODE_ERRORS)


def large_output_path(
    logdir: Path,
    content: str,
    output_type: str = "tool",
    now: datetime | None = None,
) -> Path:
    """Path :func:`save_large_output` would use, without writing a file.

    Single source of truth for the ``<logdir>/tool-outputs/<type>/<ts>-<hash>.txt``
    layout, shared with callers that only need the path for accounting. If a file
    with this name already exists, :func:`save_large_output` publishes under a
    ``-<n>`` suffixed name instead of overwriting it.
    """
    timestamp = (now or datetime.now(tz=timezone.utc)).strftime("%Y%m%d_%H%M%S")
    content_hash = hashlib.sha256(_encode(content)).hexdigest()[:8]
    return (
        logdir
        / TOOL_OUTPUTS_DIRNAME
        / _safe_output_type(output_type)
        / f"{timestamp}-{content_hash}.txt"
    )


def _candidate_names(base: Path):
    yield base.name
    for n in range(1, _MAX_COLLISION_SUFFIX):
        yield f"{base.stem}-{n}{base.suffix}"


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:  # pragma: no cover - defensive, os.write raises instead
            raise OSError(errno.EIO, "short write while saving tool output")
        view = view[written:]
    os.fsync(fd)


def _open_private_subdir(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    try:
        return os.open(name, _DIR_OPEN_FLAGS, dir_fd=parent_fd)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
            raise UnsafeOutputPathError(
                e.errno, f"refusing unsafe output directory component {name!r}"
            ) from e
        raise


def _exclusive_write(path: str, data: bytes, dir_fd: int | None) -> bool:
    """Create ``path`` exclusively and write ``data``; False if it already exists.

    Used when the filesystem rejects hard links. Never overwrites or follows an
    existing entry, but the final name is visible while it is being written; a
    failed write removes the partial file.
    """
    try:
        fd = os.open(path, _FILE_CREATE_FLAGS, 0o600, dir_fd=dir_fd)
    except FileExistsError:
        return False
    try:
        try:
            _write_all(fd, data)
        finally:
            os.close(fd)
    except BaseException:
        try:
            os.unlink(path, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        raise
    return True


def _publish(base: Path, data: bytes, dir_fd: int | None) -> Path:
    """Write ``data`` privately and publish it under a fresh name next to ``base``.

    With ``dir_fd`` set, names are resolved relative to that directory descriptor;
    otherwise they are resolved by path under ``base.parent``.
    """

    def ref(name: str) -> str:
        return name if dir_fd is not None else str(base.parent / name)

    tmp = ref(f".{base.stem}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, _FILE_CREATE_FLAGS, 0o600, dir_fd=dir_fd)
    use_link = hasattr(os, "link")
    try:
        try:
            _write_all(fd, data)
        finally:
            os.close(fd)
        for name in _candidate_names(base):
            if use_link:
                try:
                    os.link(tmp, ref(name), src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
                    return base.parent / name
                except FileExistsError:
                    continue
                except OSError as e:
                    # os.link existing does not mean the filesystem supports hard
                    # links (exFAT/FAT, some network/FUSE mounts): fall back to
                    # exclusive creation of the final name.
                    logger.debug("hard-link publish unavailable (%s); falling back", e)
                    use_link = False
            if _exclusive_write(ref(name), data, dir_fd):
                return base.parent / name
        raise FileExistsError(
            errno.EEXIST, f"too many tool-output collisions for {base.name}"
        )
    finally:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except FileNotFoundError:
            pass


def _publish_dir_fd(logdir: Path, base: Path, data: bytes) -> Path:
    """Descriptor-relative private write + no-clobber publish (POSIX)."""
    logdir.mkdir(parents=True, exist_ok=True)
    fds: list[int] = []
    try:
        fds.append(os.open(logdir, os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC))
        fds.append(_open_private_subdir(fds[-1], TOOL_OUTPUTS_DIRNAME))
        fds.append(_open_private_subdir(fds[-1], base.parent.name))
        return _publish(base, data, fds[-1])
    finally:
        for fd in reversed(fds):
            os.close(fd)


def _ensure_private_dir_by_path(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    if path.is_symlink() or not path.is_dir():
        raise UnsafeOutputPathError(
            errno.ENOTDIR, f"refusing unsafe output directory {path}"
        )


def _publish_by_path(logdir: Path, base: Path, data: bytes) -> Path:
    """Path-based fallback for platforms without ``dir_fd`` support."""
    logdir.mkdir(parents=True, exist_ok=True)
    _ensure_private_dir_by_path(base.parent.parent)
    _ensure_private_dir_by_path(base.parent)
    return _publish(base, data, None)


def _write_private_output(logdir: Path, base: Path, data: bytes) -> Path:
    if _USE_DIR_FD:
        return _publish_dir_fd(logdir, base, data)
    return _publish_by_path(logdir, base, data)


def _open_exclusive_named(base: Path, dir_fd: int | None) -> PrivateOutputFile:
    """Create ``base`` exclusively; suffix on collision. Never follows/clobbers."""

    def ref(name: str) -> str:
        return name if dir_fd is not None else str(base.parent / name)

    for name in _candidate_names(base):
        try:
            fd = os.open(ref(name), _FILE_CREATE_FLAGS, 0o600, dir_fd=dir_fd)
        except FileExistsError:
            continue
        return PrivateOutputFile(base.parent / name, fd)
    raise FileExistsError(
        errno.EEXIST, f"too many tool-output collisions for {base.name}"
    )


def open_private_output_file(
    logdir: Path, output_type: str, name: str
) -> PrivateOutputFile:
    """Create an exclusive private file at ``tool-outputs/<type>/<name>``.

    Directories are ``0700``. The file is ``0600`` and opened ``O_EXCL``. A
    same-name collision gets a numeric suffix rather than clobbering. Symlinked
    or non-directory parents raise :class:`UnsafeOutputPathError`.
    """
    safe_type = _safe_output_type(output_type)
    safe_name = _safe_filename(name)
    base = logdir / TOOL_OUTPUTS_DIRNAME / safe_type / safe_name
    if _USE_DIR_FD:
        logdir.mkdir(parents=True, exist_ok=True)
        fds: list[int] = []
        try:
            fds.append(os.open(logdir, os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC))
            fds.append(_open_private_subdir(fds[-1], TOOL_OUTPUTS_DIRNAME))
            fds.append(_open_private_subdir(fds[-1], safe_type))
            return _open_exclusive_named(base, fds[-1])
        finally:
            for fd in reversed(fds):
                os.close(fd)
    logdir.mkdir(parents=True, exist_ok=True)
    _ensure_private_dir_by_path(logdir / TOOL_OUTPUTS_DIRNAME)
    _ensure_private_dir_by_path(logdir / TOOL_OUTPUTS_DIRNAME / safe_type)
    return _open_exclusive_named(base, None)


def save_large_output(
    content: str,
    logdir: Path,
    output_type: str = "tool",
    command_info: str | None = None,
    original_tokens: int | None = None,
) -> tuple[str, Path]:
    """
    Save large output to filesystem before truncation.

    Args:
        content: Full output text to save
        logdir: Conversation directory for saving
        output_type: Type of tool output (e.g., "shell", "python")
        command_info: Optional command that generated output
        original_tokens: Optional token count of original content

    Returns:
        Tuple of (summary_text, saved_path)
    """
    # Path is derived by the shared helper so callers can predict the filename;
    # an existing file with that name is never overwritten (a suffix is added).
    base_path = large_output_path(logdir, content, output_type)
    saved_path = _write_private_output(logdir, base_path, _encode(content))
    logger.info(f"Saved large output to {saved_path}")

    # Create summary text
    summary_parts = [f"[Large {output_type} output saved]"]

    if command_info:
        summary_parts.append(f"Command: {command_info}")

    if original_tokens:
        summary_parts.append(f"Original: {original_tokens} tokens")

    summary_parts.append(f"Full output: {saved_path}")

    summary_text = "\n".join(summary_parts)

    return (summary_text, saved_path)


def create_tool_result_summary(
    content: str,
    original_tokens: int,
    logdir: Path | None,
    tool_name: str = "tool",
) -> str:
    """
    Create a summary of a large tool result, with content saved to file.

    This is used by autocompact to summarize massive tool outputs.

    Args:
        content: Full tool result content
        original_tokens: Number of tokens in original content
        logdir: Path to conversation directory for saving removed output
        tool_name: Name of tool that generated output

    Returns:
        Summary message with reference to saved file
    """
    # Try to extract command info from content
    lines = content.split("\n")
    command_info = None

    for line in lines[:10]:  # Check first 10 lines
        if line.startswith(("Ran command:", "Executed:", "Command:")):
            command_info = line.strip()
            break

    # Create base message without status inference
    base_msg = f"[Large tool output removed - {original_tokens} tokens]"

    if command_info:
        base_msg += f" ({command_info})"

    # If no logdir, return simple message
    if not logdir:
        base_msg += "."
        return f"{base_msg} Output was automatically removed due to size to allow conversation continuation."

    # Save the output
    _, saved_path = save_large_output(
        content=content,
        logdir=logdir,
        output_type=tool_name,
        command_info=command_info,
        original_tokens=original_tokens,
    )

    summary_msg = f"{base_msg}. Full output saved to: {saved_path}\nYou can read or grep this file if needed."

    # Record telemetry for the autocompact savings. The replacement message
    # is what stays in the conversation, so kept_tokens is its token count.
    try:
        from ..llm.models import get_default_model
        from .context_savings import record_context_savings
        from .tokens import len_tokens

        model = get_default_model()
        model_name = model.model if model else "cl100k_base"
        record_context_savings(
            logdir=logdir,
            source=tool_name,
            original_tokens=original_tokens,
            kept_tokens=len_tokens(summary_msg, model_name),
            command_info=command_info,
            saved_path=saved_path,
        )
    except Exception as e:
        logger.warning("Failed to record autocompact context savings: %s", e)

    return summary_msg
