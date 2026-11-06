"""
Utilities for storing large tool outputs before truncation.

Saves full output under the conversation-owned ``tool-outputs/`` namespace and
provides a path reference for later retrieval.

Writer safety (see ``docs/design/file-backed-output.md``, slice 1):

- ``output_type`` is reduced to a single safe path component.
- Directories created here are private (``0700``) and files are created
  privately (``0600``) with exclusive, unguessable temporary names.
- Where the platform supports it, traversal and creation are descriptor-relative
  and refuse symlinked or non-directory ``tool-outputs`` components.
- Content is written to a private temporary file and then published under its
  final name without overwriting any existing file; a failed write leaves no
  final file behind.
- Distinct calls keep distinct files: a same-second, same-content collision gets
  a numeric suffix instead of silently reusing (or clobbering) the earlier file.
"""

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


def _encode(content: str) -> bytes:
    """UTF-8 bytes for ``content``; never depends on the locale encoding.

    ``surrogateescape`` restores bytes that were decoded with that handler; any
    other lone surrogate falls back to replacement rather than failing the save.
    """
    try:
        return content.encode("utf-8", errors="surrogateescape")
    except UnicodeEncodeError:
        return content.encode("utf-8", errors="replace")


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


def _publish_dir_fd(logdir: Path, base: Path, data: bytes) -> Path:
    """Descriptor-relative private write + no-clobber publish (POSIX)."""
    logdir.mkdir(parents=True, exist_ok=True)
    fds: list[int] = []
    try:
        fds.append(os.open(logdir, os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC))
        fds.append(_open_private_subdir(fds[-1], TOOL_OUTPUTS_DIRNAME))
        fds.append(_open_private_subdir(fds[-1], base.parent.name))
        dir_fd = fds[-1]

        tmp_name = f".{base.stem}.{secrets.token_hex(8)}.tmp"
        fd = os.open(tmp_name, _FILE_CREATE_FLAGS, 0o600, dir_fd=dir_fd)
        try:
            try:
                _write_all(fd, data)
            finally:
                os.close(fd)
            for name in _candidate_names(base):
                try:
                    os.link(
                        tmp_name,
                        name,
                        src_dir_fd=dir_fd,
                        dst_dir_fd=dir_fd,
                    )
                except FileExistsError:
                    continue
                return base.parent / name
            raise FileExistsError(
                errno.EEXIST, f"too many tool-output collisions for {base.name}"
            )
        finally:
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
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

    tmp_path = base.parent / f".{base.stem}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp_path, _FILE_CREATE_FLAGS, 0o600)
    try:
        try:
            _write_all(fd, data)
        finally:
            os.close(fd)
        for name in _candidate_names(base):
            final = base.parent / name
            try:
                if hasattr(os, "link"):
                    os.link(tmp_path, final)
                else:  # pragma: no cover - platforms without hard links
                    if os.path.lexists(final):
                        continue
                    # Windows rename refuses to replace an existing target.
                    os.rename(tmp_path, final)
            except FileExistsError:
                continue
            return final
        raise FileExistsError(
            errno.EEXIST, f"too many tool-output collisions for {base.name}"
        )
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass


def _write_private_output(logdir: Path, base: Path, data: bytes) -> Path:
    if _USE_DIR_FD:
        return _publish_dir_fd(logdir, base, data)
    return _publish_by_path(logdir, base, data)


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
