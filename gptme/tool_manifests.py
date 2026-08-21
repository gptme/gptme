from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_TOOL_MANIFEST_PATH = Path("state/task-manifests.jsonl")
TOOL_MANIFEST_PATH_ENV = "GPTME_TOOL_MANIFEST_PATH"

# Simple identifier pattern: lowercase letters, digits, underscores, hyphens
_BUILTIN_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass(frozen=True)
class TaskToolManifest:
    task_type: str
    tool_names: tuple[str, ...]
    path: Path
    # Optional explicit built-in tools; when non-empty, produces an explicit (non-additive)
    # allowlist instead of the default additive ("+") form.
    builtin_tools: tuple[str, ...] = field(default_factory=tuple)

    @property
    def all_tool_names(self) -> tuple[str, ...]:
        """All tools: built-in first, then MCP tools."""
        return self.builtin_tools + self.tool_names


def _resolve_manifest_path(workspace: Path, manifest_path: Path | None = None) -> Path:
    if manifest_path is None:
        env_path = os.environ.get(TOOL_MANIFEST_PATH_ENV)
        manifest_path = Path(env_path) if env_path else DEFAULT_TOOL_MANIFEST_PATH

    manifest_path = manifest_path.expanduser()
    if not manifest_path.is_absolute():
        manifest_path = workspace / manifest_path
    return manifest_path


def _tool_name_from_record(tool: Any, *, line_no: int, task_type: str) -> str:
    if not isinstance(tool, dict):
        raise ValueError(
            f"Invalid tool manifest entry for {task_type!r} on line {line_no}: "
            "tool entries must be objects"
        )

    server_name = tool.get("server_name")
    tool_name = tool.get("tool_name")
    if not isinstance(server_name, str) or not server_name.strip():
        raise ValueError(
            f"Invalid tool manifest entry for {task_type!r} on line {line_no}: "
            "server_name must be a non-empty string"
        )
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ValueError(
            f"Invalid tool manifest entry for {task_type!r} on line {line_no}: "
            "tool_name must be a non-empty string"
        )

    sn = server_name.strip()
    tn = tool_name.strip()

    # --- Allowlist-injection guards ---
    # init_tools() joins manifest tool names into a comma-separated allowlist string
    # ("+sn1.tn1,sn2.tn2,...").  It then splits on commas to recover individual
    # entries, so a comma anywhere in sn or tn injects extra entries.
    if "," in sn:
        raise ValueError(
            f"Invalid tool manifest entry for {task_type!r} on line {line_no}: "
            f"server_name {sn!r} must not contain commas"
        )
    if "," in tn:
        raise ValueError(
            f"Invalid tool manifest entry for {task_type!r} on line {line_no}: "
            f"tool_name {tn!r} must not contain commas"
        )

    # Manifest records select exact tools. Glob metacharacters would turn a
    # single record into a pattern and silently broaden the tool allowlist.
    for name, value in (("server_name", sn), ("tool_name", tn)):
        if any(char in value for char in "*?["):
            raise ValueError(
                f"Invalid tool manifest entry for {task_type!r} on line {line_no}: "
                f"{name} {value!r} must not contain glob metacharacters"
            )

    # --- Path-injection / arbitrary-code-execution guards ---
    # init_tools() treats any allowlist item whose text contains "/" or "\" or
    # ends with ".py" as a *file path* and calls load_from_file() on it, which
    # imports the file as Python.  A malicious manifest (e.g. from a cloned
    # repository) can exploit this to achieve arbitrary code execution:
    #   server_name="github", tool_name="../../evil.py"
    #   → combined name "github.../../evil.py"
    #   → init_tools sees "/" → load_from_file("github.../../evil.py")
    # We block forward-slash, backslash, path-traversal "..", and a ".py" suffix
    # in tool_name (which would make the combined name end in ".py").
    _PATH_FORBIDDEN: list[tuple[str, str]] = [
        ("/", "forward slashes"),
        ("\\", "backslashes"),
        ("..", "path traversal sequences"),
    ]
    for seq, label in _PATH_FORBIDDEN:
        if seq in sn:
            raise ValueError(
                f"Invalid tool manifest entry for {task_type!r} on line {line_no}: "
                f"server_name {sn!r} must not contain {label}"
            )
        if seq in tn:
            raise ValueError(
                f"Invalid tool manifest entry for {task_type!r} on line {line_no}: "
                f"tool_name {tn!r} must not contain {label}"
            )
    # tool_name must not end with ".py" — combined "sn.tn" would end in ".py"
    # and init_tools() would route it to load_from_file().
    if tn.endswith(".py"):
        raise ValueError(
            f"Invalid tool manifest entry for {task_type!r} on line {line_no}: "
            f"tool_name {tn!r} must not end with '.py'"
        )

    return f"{sn}.{tn}"


def _validate_builtin_tool_name(name: str, *, line_no: int, task_type: str) -> str:
    """Validate and return a built-in tool name from a ``builtin_tools`` entry."""
    if not isinstance(name, str):
        raise ValueError(
            f"Invalid builtin_tools entry for {task_type!r} on line {line_no}: "
            "entries must be strings"
        )
    name = name.strip()
    if not name:
        raise ValueError(
            f"Invalid builtin_tools entry for {task_type!r} on line {line_no}: "
            "entry must be a non-empty string"
        )
    if "," in name:
        raise ValueError(
            f"Invalid builtin_tools entry for {task_type!r} on line {line_no}: "
            f"{name!r} must not contain commas"
        )
    if not _BUILTIN_TOOL_NAME_RE.match(name):
        raise ValueError(
            f"Invalid builtin_tools entry for {task_type!r} on line {line_no}: "
            f"{name!r} must match [a-z][a-z0-9_-]* (no dots or path chars)"
        )
    return name


def load_task_manifest(
    task_type: str, workspace: Path, manifest_path: Path | None = None
) -> TaskToolManifest:
    """Load a task-specific tool manifest from JSONL.

    The manifest format supports two field types:

    - ``tools``: a list of ``{server_name, tool_name}`` objects for MCP-served tools
      (e.g. ``{"server_name": "github", "tool_name": "search_code"}``).
    - ``builtin_tools``: an optional list of plain strings naming built-in gptme
      tools (e.g. ``["read", "grep", "glob"]``).  When present, ``apply_tool_manifest``
      produces an explicit (non-additive) allowlist that combines the built-in tools
      with the MCP tools; otherwise the MCP tools are added to the full default set.

    Example record::

        {"task_type": "code_review",
         "builtin_tools": ["read", "grep", "glob", "shell"],
         "tools": [{"server_name": "github", "tool_name": "search_code"}]}
    """
    task_type = task_type.strip()
    if not task_type:
        raise ValueError("Task manifest type cannot be empty")

    path = _resolve_manifest_path(workspace, manifest_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Tool manifest file not found: {path}. "
            f"Set {TOOL_MANIFEST_PATH_ENV} or run from a workspace with "
            f"{DEFAULT_TOOL_MANIFEST_PATH}."
        )

    available_task_types: list[str] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Invalid JSON in tool manifest {path} on line {line_no}: {e}"
                ) from e
            if not isinstance(record, dict):
                raise ValueError(
                    f"Invalid tool manifest {path} on line {line_no}: record must be an object"
                )

            record_task_type = record.get("task_type")
            if isinstance(record_task_type, str):
                available_task_types.append(record_task_type)
            if record_task_type != task_type:
                continue

            # --- MCP tools (required) ---
            tools = record.get("tools")
            if not isinstance(tools, list) or not tools:
                raise ValueError(
                    f"Invalid tool manifest for {task_type!r} on line {line_no}: "
                    "tools must be a non-empty list"
                )

            tool_names = tuple(
                dict.fromkeys(
                    _tool_name_from_record(tool, line_no=line_no, task_type=task_type)
                    for tool in tools
                )
            )

            # --- Built-in tools (optional) ---
            raw_builtins = record.get("builtin_tools")
            if raw_builtins is not None:
                if not isinstance(raw_builtins, list):
                    raise ValueError(
                        f"Invalid tool manifest for {task_type!r} on line {line_no}: "
                        "builtin_tools must be a list"
                    )
                builtin_tools = tuple(
                    dict.fromkeys(
                        _validate_builtin_tool_name(
                            n, line_no=line_no, task_type=task_type
                        )
                        for n in raw_builtins
                    )
                )
            else:
                builtin_tools = ()

            return TaskToolManifest(
                task_type=task_type,
                tool_names=tool_names,
                path=path,
                builtin_tools=builtin_tools,
            )

    available = ", ".join(sorted(set(available_task_types))) or "none"
    raise ValueError(
        f"Unknown tool manifest task type {task_type!r}. Available task types: {available}"
    )


def get_manifest_preset_tools(
    task_type: str, workspace: Path, manifest_path: Path | None = None
) -> list[str] | None:
    """Try to resolve a manifest task type to its tool list.

    Returns the combined ``builtin_tools + tool_names`` from the manifest if the
    manifest file exists and the task type is found.  Returns ``None`` when:

    - The manifest file does not exist in the workspace (no ``GPTME_TOOL_MANIFEST_PATH``
      override and no ``state/task-manifests.jsonl`` present).
    - The task type is not found in the manifest.
    - The manifest record is otherwise invalid.

    This is the "probe" API used to resolve ``--tools <task_type>`` as a manifest
    alias.  Callers that need a hard error on missing manifests should call
    ``load_task_manifest()`` directly.
    """
    try:
        manifest = load_task_manifest(task_type, workspace, manifest_path)
    except (OSError, ValueError):
        return None
    return list(manifest.all_tool_names)
