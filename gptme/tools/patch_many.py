"""
Gives the LLM agent the ability to patch multiple files atomically in one tool call.

Intended for cross-cutting changes ("rename this class", "add a param and update callers")
where the model would otherwise issue N separate patch calls, one per file.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ..message import Message
from ..util.ask_execute import execute_with_confirmation
from .base import Parameter, ToolSpec
from .patch import DIVIDER, ORIGINAL, UPDATED, Patch, apply

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping, Sequence

# Keep this under 1024 chars: it is the OpenAI tool description
# (tests/test_tools.py::test_tool_descriptions_within_openai_limit).
instructions = """
Apply patches to multiple files atomically.
Patches are validated in-memory: if ANY fails, NO files are written.

Simple (one hunk per file), paths in the fence header:
  ```patch_many path1.py
  <<<<<<< ORIGINAL
  old content
  =======
  new content
  >>>>>>> UPDATED
  ```

Multi-hunk (any number of hunks per file), using `=== PATH: ... ===` headers:
  ```patch_many
  === PATH: path1.py ===
  <<<<<<< ORIGINAL
  first hunk original
  =======
  first hunk updated
  >>>>>>> UPDATED
  <<<<<<< ORIGINAL
  second hunk original
  =======
  second hunk updated
  >>>>>>> UPDATED
  ```

Tool-call: `patches` is a JSON array of {"path": "...", "patch": "..."} entries.
Each patch string may hold multiple ORIGINAL/UPDATED blocks, so one entry lands
several hunks.

Repeated paths apply in order, each hunk seeing the previous result. Prefer one
entry per path with multiple blocks; repeat a path only when a later hunk depends
on an earlier one landing.
""".strip()


def _resolve_path(raw_path: str) -> Path:
    """Resolve a path and reject relative traversal outside the current directory."""
    path_display = Path(raw_path).expanduser()
    path = path_display.resolve()

    if not path_display.is_absolute():
        cwd = Path.cwd().resolve()
        try:
            path.relative_to(cwd)
        except ValueError as err:
            raise ValueError(
                f"Path traversal detected: {path_display} resolves to {path} "
                f"which is outside current directory {cwd}"
            ) from err

    return path


def _parse_patches_from_content(
    content: str, paths: list[Path]
) -> list[tuple[Path, str]]:
    """Parse multiple conflict-marker patches from markdown content and pair with paths."""
    # Count top-level ORIGINAL/UPDATED blocks here, not placeholder-expanded hunks.
    # A single patch block may legitimately expand to multiple replacements when
    # `apply()` handles placeholder markers like `# ...`.
    patches = [_stringify_patch(patch) for patch in Patch._from_codeblock(content)]
    if len(patches) != len(paths):
        if len(patches) > len(paths):
            raise ValueError(
                f"Got {len(patches)} patch(es) but {len(paths)} file path(s). "
                "The markdown format supports exactly one hunk per file. "
                "For multi-hunk patches, use the kwargs format: pass a 'patches' array "
                "where each entry's 'patch' field contains all hunks for that file."
            )
        raise ValueError(
            f"Got {len(patches)} patch(es) but {len(paths)} file path(s). "
            "Each file path must have a corresponding conflict-marker patch."
        )
    return list(zip(paths, patches))


def _parse_patches_from_kwargs(kwargs: Mapping[str, object]) -> list[tuple[Path, str]]:
    """Parse patches from kwargs format: {patches: [{path, patch}, ...]}."""
    raw = kwargs.get("patches")
    if raw is None:
        raise ValueError("Missing 'patches' in kwargs")

    if isinstance(raw, str):
        raw = json.loads(raw)

    if not isinstance(raw, list):
        raise ValueError("'patches' must be a list or a JSON-encoded list")

    result: list[tuple[Path, str]] = []
    for i, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Patch entry {i} must be an object")

        path_value = entry.get("path")
        patch_value = entry.get("patch")
        if path_value is None or patch_value is None:
            raise ValueError(f"Patch entry {i} must include both 'path' and 'patch'")
        if not isinstance(path_value, str) or not isinstance(patch_value, str):
            raise ValueError(
                f"Patch entry {i} must use string 'path' and 'patch' values"
            )

        result.append((_resolve_path(path_value), patch_value))

    return result


_CONFIRM_HEADER = "=== PATH: "
_CONFIRM_HEADER_RE = re.compile(
    rf"(?ms)^{re.escape(_CONFIRM_HEADER)}(?P<path>.+?) ===\n"
    r"(?P<patch>.*?)(?=^=== PATH: |\Z)"
)


def _stringify_patch(patch_src: str | Patch) -> str:
    """Serialize a patch source into the standard conflict-marker format."""
    if isinstance(patch_src, Patch):
        return (
            f"{ORIGINAL}{patch_src.original}{DIVIDER}{patch_src.updated}{UPDATED}"
        ).strip()
    return patch_src.strip()


def _serialize_confirmation_payload(
    patches: Sequence[tuple[Path, str | Patch]],
) -> str:
    """Build an editable multi-file payload for the confirmation hook."""
    return "\n\n".join(
        f"{_CONFIRM_HEADER}{path} ===\n{_stringify_patch(patch_src)}"
        for path, patch_src in patches
    )


def _parse_confirmation_payload(content: str) -> list[tuple[Path, str]]:
    """Parse the confirmation payload back into path/patch pairs."""
    matches = list(_CONFIRM_HEADER_RE.finditer(content.strip()))
    if not matches:
        raise ValueError(
            "Invalid patch_many payload: expected one or more '=== PATH: ... ===' sections"
        )

    return [
        (_resolve_path(match.group("path")), match.group("patch").strip())
        for match in matches
    ]


def _get_confirmation_path(
    code: str | None, args: list[str] | None, kwargs: dict[str, str] | None
) -> Path | None:
    """patch_many confirms a multi-file payload, so there is no single target path."""
    del code, args, kwargs
    return None


def _execute_patch_many_confirmed(
    content: str, path: Path | None
) -> Generator[Message, None, None]:
    """Execute a confirmed patch_many payload."""
    del path
    patches = _parse_confirmation_payload(content)
    yield from execute_patch_many_impl(patches)


def _stage_patch_content(path: Path, content: bytes, mode: int) -> Path:
    """Prepare a same-directory replacement without touching its target."""
    fd, name = tempfile.mkstemp(prefix=".patch-many-", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        info = path.stat()
        if hasattr(os, "chown"):
            staged_info = staged.stat()
            if (staged_info.st_uid, staged_info.st_gid) != (info.st_uid, info.st_gid):
                os.chown(staged, info.st_uid, info.st_gid)
        # Preserve access ACLs (and other xattrs) rather than granting the new
        # inode's default audience access. Failure aborts staging before writes.
        if hasattr(os, "listxattr"):
            source_attrs = set(os.listxattr(path))
            for name in set(os.listxattr(staged)) - source_attrs:
                os.removexattr(staged, name)
            for name in source_attrs:
                os.setxattr(staged, name, os.getxattr(path, name))
        # Widen permissions only after installing the source access ACL. Its
        # group mode bits are an ACL mask, not necessarily group permissions.
        staged.chmod(mode)
    except BaseException as error:
        try:
            staged.unlink(missing_ok=True)
        except BaseException as cleanup_error:
            error.add_note(f"staging cleanup failed for `{staged}`: {cleanup_error}")
        raise
    return staged


def _publish_patches(
    order: list[Path],
    contents: dict[Path, bytes],
    originals: dict[Path, bytes],
    modes: dict[Path, int],
    cleanup_errors: list[str],
) -> str | None:
    """Stage every replacement and backup, then publish with best-effort rollback."""
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    retained: set[Path] = set()
    attempted: list[Path] = []
    result: str | None = None
    pending_error: BaseException | None = None
    try:
        try:
            for path in order:
                backups[path] = _stage_patch_content(path, originals[path], modes[path])
                staged[path] = _stage_patch_content(path, contents[path], modes[path])
        except OSError as error:
            result = f"Atomic patch failed: staging write error: {error}. No files were written."
            if notes := getattr(error, "__notes__", []):
                result += "\n" + "\n".join(notes)
            return result

        try:
            for path in order:
                # Include the failing target: a publication wrapper can mutate then
                # raise. Restore from a fully prepared original, not another write.
                attempted.append(path)
                os.replace(staged[path], path)
        except BaseException as error:
            # Protect originals even if rollback itself is interrupted. Only a
            # successful restoration makes its recovery copy safe to clean up.
            retained.update(backups[target] for target in attempted)
            failures: list[str] = []
            for target in reversed(attempted):
                try:
                    os.replace(backups[target], target)
                    retained.discard(backups[target])
                except BaseException as restore_error:
                    failures.append(
                        f"`{target}`: {restore_error}; original retained at `{backups[target]}`"
                    )
            result = f"Atomic patch failed: write error on `{path}`: {error}. "
            if failures:
                result += (
                    "Restoration incomplete; files may be modified:\n"
                    + "\n".join(failures)
                )
            else:
                result += (
                    f"Rolled back {len(attempted)} file(s). Original contents restored."
                )
            if not isinstance(error, OSError):
                error.add_note(result)
                raise
            return result
    except BaseException as error:
        pending_error = error
        raise
    finally:
        cleanup_interrupt: BaseException | None = None
        for temporary in [*staged.values(), *backups.values()]:
            if temporary not in retained:
                try:
                    temporary.unlink(missing_ok=True)
                except BaseException as cleanup_error:
                    cleanup_errors.append(f"`{temporary}`: {cleanup_error}")
                    if (
                        not isinstance(cleanup_error, OSError)
                        and cleanup_interrupt is None
                    ):
                        cleanup_interrupt = cleanup_error
        # Exceptional exits bypass the caller's normal cleanup report. Keep
        # their diagnostics on the original exception, even if cleanup is
        # itself interrupted, and finish attempting the remaining removals.
        escaping_error = pending_error or cleanup_interrupt
        if escaping_error is not None:
            if pending_error is None and result:
                escaping_error.add_note(result)
            if cleanup_errors:
                escaping_error.add_note(
                    "Temporary-file cleanup incomplete:\n" + "\n".join(cleanup_errors)
                )
            if pending_error is None:
                raise escaping_error
    return None


def execute_patch_many_impl(
    patches: Sequence[tuple[Path, str | Patch]],
) -> Generator[Message, None, None]:
    """Resolve all patches in-memory before writing any files."""
    if not patches:
        yield Message("system", "Atomic patch aborted: no patches were provided.")
        return

    # Repeated paths accumulate: each entry patches the working content built by
    # earlier entries for that path, not the pristine on-disk text. Without this,
    # a second entry for one path would be applied to the original text and its
    # write would clobber the first (silent partial application).
    current: dict[Path, str] = {}
    originals: dict[Path, bytes] = {}
    modes: dict[Path, int] = {}
    newlines: dict[Path, str] = {}
    order: list[Path] = []
    hunks = 0

    for path, patch_src in patches:
        path = path.resolve()
        if path not in current:
            if not path.exists():
                yield Message(
                    "system",
                    f"Atomic patch aborted: file not found `{path}`. No files were written.",
                )
                return
            if not path.is_file():
                yield Message(
                    "system",
                    f"Atomic patch aborted: cannot patch non-regular file `{path}`. No files were written.",
                )
                return

            try:
                info = path.stat()
                # Replacing one hardlinked name would detach it from its aliases;
                # writing in place would clobber edits through another name.
                if info.st_nlink > 1:
                    yield Message(
                        "system",
                        f"Atomic patch aborted: hardlink alias target `{path}`. No files were written.",
                    )
                    return
                original_bytes = path.read_bytes()
                original = (
                    original_bytes.decode("utf-8")
                    .replace("\r\n", "\n")
                    .replace("\r", "\n")
                )
                modes[path] = stat.S_IMODE(info.st_mode)
                crlf = original_bytes.count(b"\r\n")
                newlines[path] = (
                    "\r\n"
                    if crlf and crlf >= original_bytes.count(b"\n") - crlf
                    else "\n"
                )
            except (UnicodeDecodeError, PermissionError, OSError) as e:
                yield Message(
                    "system",
                    f"Atomic patch aborted: could not read `{path}`: {e}. No files were written.",
                )
                return

            originals[path] = original_bytes
            current[path] = original
            order.append(path)

        try:
            # Count hunks: a Patch is one hunk; a codeblock string may hold
            # multiple ORIGINAL/UPDATED blocks, each applying as its own hunk.
            if isinstance(patch_src, Patch):
                hunks += 1
                new_content = patch_src.apply(current[path])
            else:
                hunks += patch_src.count(ORIGINAL)
                new_content = apply(patch_src, current[path])
        except ValueError as e:
            yield Message(
                "system",
                f"Atomic patch aborted: patch failed for `{path}`: {e}. "
                "No files were written.",
            )
            return

        current[path] = new_content

    # Replacement needs directory permission, not target write permission. Preserve
    # the old in-place writer's access check without truncating or changing bytes.
    # Check every target before staging or publishing any of them.
    for path in order:
        try:
            fd = os.open(path, os.O_WRONLY)
            os.close(fd)
        except OSError as access_error:
            yield Message(
                "system",
                f"Atomic patch aborted: cannot write `{path}`: {access_error}. No files were written.",
            )
            return

    # Content was read in universal-newline mode; preserve each file's style.
    contents = {
        path: current[path]
        .replace("\r\n", "\n")
        .replace("\n", newlines[path])
        .encode("utf-8")
        for path in order
    }
    cleanup_errors: list[str] = []
    error = _publish_patches(order, contents, originals, modes, cleanup_errors)
    result = error or (
        f"Applied {hunks} hunk(s) atomically to {len(order)} file(s):\n"
        + "\n".join(f"  - {p}" for p in order)
    )
    if cleanup_errors:
        result += "\nTemporary-file cleanup incomplete:\n" + "\n".join(cleanup_errors)
    yield Message("system", result)


def execute_patch_many(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    """Execute patch_many from a markdown block or tool/function-call kwargs."""
    if code is None and kwargs is not None and "patches" in kwargs:
        try:
            kwarg_entries = _parse_patches_from_kwargs(kwargs)
        except (ValueError, json.JSONDecodeError) as e:
            yield Message("system", f"Error parsing patches from kwargs: {e}")
            return

        yield from execute_with_confirmation(
            _serialize_confirmation_payload(kwarg_entries),
            args,
            kwargs,
            execute_fn=_execute_patch_many_confirmed,
            get_path_fn=_get_confirmation_path,
            allow_edit=True,
        )
        return

    if not code:
        yield Message("system", "No patch content provided")
        return

    # Multi-hunk format: paths embedded in content with === PATH: ... === headers
    # Use the anchored regex (not a bare substring check) to avoid misrouting
    # simple-format patches whose content happens to contain "=== PATH: ".
    if _CONFIRM_HEADER_RE.search(code):
        try:
            patch_entries: list[tuple[Path, str | Patch]] = list(
                _parse_confirmation_payload(code)
            )
        except ValueError as e:
            yield Message("system", f"Error parsing patches: {e}")
            return
        yield from execute_with_confirmation(
            _serialize_confirmation_payload(patch_entries),
            args,
            kwargs,
            execute_fn=_execute_patch_many_confirmed,
            get_path_fn=_get_confirmation_path,
            allow_edit=True,
        )
        return

    # Simple format: space-separated paths in fence header, one hunk per file
    if not args or not args[0]:
        yield Message(
            "system",
            "No file paths provided. Usage: ```patch_many path1 path2 ...",
        )
        return

    try:
        paths = [_resolve_path(arg) for arg in args]
        patch_pairs: list[tuple[Path, str | Patch]] = list(
            _parse_patches_from_content(code, paths)
        )
    except ValueError as e:
        yield Message("system", f"Error parsing patches: {e}")
        return

    yield from execute_with_confirmation(
        _serialize_confirmation_payload(patch_pairs),
        args,
        kwargs,
        execute_fn=_execute_patch_many_confirmed,
        get_path_fn=_get_confirmation_path,
        allow_edit=True,
    )


tool_patch_many = ToolSpec(
    name="patch_many",
    desc="Apply multiple patches to multiple files atomically",
    instructions=instructions,
    execute=execute_patch_many,
    block_types=["patch_many"],
    parameters=[
        Parameter(
            name="patches",
            type="string",
            description=(
                "JSON array string of {path, patch} objects. Each patch string uses "
                "the same conflict-marker format as the patch tool."
            ),
            required=True,
        )
    ],
)
__doc__ = tool_patch_many.get_doc(__doc__)
