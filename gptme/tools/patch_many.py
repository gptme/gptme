"""
Gives the LLM agent the ability to patch multiple files atomically in one tool call.

Intended for cross-cutting changes ("rename this class", "add a param and update callers")
where the model would otherwise issue N separate patch calls, one per file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ..message import Message
from .base import Parameter, ToolSpec
from .patch import Patch, apply

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping, Sequence

instructions = """
Apply multiple patches to multiple files in a single atomic operation.

All patches are resolved in-memory first. If ANY patch fails, NO files are written.
Use this for cross-cutting changes that touch multiple files.

### Format

The codeblock takes space-separated file paths and one conflict-marker patch per file:

    ```patch_many path/to/file1.py path/to/file2.py

    <<<<<<< ORIGINAL
    original content
    =======
    updated content
    >>>>>>> UPDATED

    <<<<<<< ORIGINAL
    original content
    =======
    updated content
    >>>>>>> UPDATED
    ```

Each patch corresponds to the file at the same position in the path list.
All patches are validated in-memory before writing any file.

For tool/function-call format, pass a `patches` JSON array string:

    [
      {"path": "path/to/file1.py", "patch": "<<<<<<< ORIGINAL\\n..."},
      {"path": "path/to/file2.py", "patch": "<<<<<<< ORIGINAL\\n..."}
    ]
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
) -> list[tuple[Path, Patch]]:
    """Parse multiple conflict-marker patches from markdown content and pair with paths."""
    patches = list(Patch.from_codeblock(content))
    if len(patches) != len(paths):
        raise ValueError(
            f"Got {len(patches)} patch(es) but {len(paths)} file path(s). "
            "The number of patches must match the number of file paths."
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


def execute_patch_many_impl(
    patches: Sequence[tuple[Path, str | Patch]],
) -> Generator[Message, None, None]:
    """Resolve all patches in-memory before writing any files."""
    if not patches:
        yield Message("system", "Atomic patch aborted: no patches were provided.")
        return

    resolved: list[tuple[Path, str]] = []

    for path, patch_src in patches:
        if not path.exists():
            yield Message(
                "system",
                f"Atomic patch aborted: file not found `{path}`. No files were written.",
            )
            return

        try:
            original = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError) as e:
            yield Message(
                "system",
                f"Atomic patch aborted: could not read `{path}`: {e}. No files were written.",
            )
            return

        try:
            if isinstance(patch_src, Patch):
                new_content = patch_src.apply(original)
            else:
                new_content = apply(patch_src, original)
        except ValueError as e:
            yield Message(
                "system",
                f"Atomic patch aborted: patch failed for `{path}`: {e}. "
                "No files were written.",
            )
            return

        resolved.append((path, new_content))

    written: list[str] = []
    for path, new_content in resolved:
        path.write_text(new_content, encoding="utf-8")
        written.append(str(path))

    yield Message(
        "system",
        f"Applied {len(written)} patch(es) atomically to:\n"
        + "\n".join(f"  - {path}" for path in written),
    )


def execute_patch_many(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    """Execute patch_many from a markdown block or tool/function-call kwargs."""
    if code is None and kwargs is not None and "patches" in kwargs:
        try:
            patch_entries = _parse_patches_from_kwargs(kwargs)
        except (ValueError, json.JSONDecodeError) as e:
            yield Message("system", f"Error parsing patches from kwargs: {e}")
            return

        yield from execute_patch_many_impl(patch_entries)
        return

    if not code:
        yield Message("system", "No patch content provided")
        return

    if not args or not args[0]:
        yield Message(
            "system",
            "No file paths provided. Usage: ```patch_many path1 path2 ...",
        )
        return

    try:
        paths = [_resolve_path(arg) for arg in args]
        patch_pairs = _parse_patches_from_content(code, paths)
    except ValueError as e:
        yield Message("system", f"Error parsing patches: {e}")
        return

    yield from execute_patch_many_impl(patch_pairs)


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
