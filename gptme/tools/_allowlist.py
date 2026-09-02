from fnmatch import fnmatchcase

from .base import ToolSpec

_HINT_PREFIX = "hint:"
READ_ONLY_TOOL_PRESET = ("read",)
TOOL_PRESETS: dict[str, tuple[str, ...]] = {
    "read-only": READ_ONLY_TOOL_PRESET,
}
TOOL_PRESET_NAMES = tuple(TOOL_PRESETS)


def expand_tool_allowlist_presets(allowlist: list[str] | None) -> list[str] | None:
    """Expand named tool presets into concrete tool names.

    Presets are exclusive capability boundaries, not shortcuts that can be mixed
    with arbitrary tools. Use hint-based allowlists for intentionally broad
    category matching.
    """
    if allowlist is None:
        return None

    presets = [item for item in allowlist if item in TOOL_PRESETS]
    if not presets:
        return allowlist
    if len(allowlist) != 1:
        preset_list = ", ".join(presets)
        raise ValueError(
            f"Tool preset(s) {preset_list} cannot be combined with other tools"
        )
    return list(TOOL_PRESETS[presets[0]])


def is_hint_pattern(pattern: str) -> bool:
    """Return True if the pattern is a hint-based filter (e.g. 'hint:read-only')."""
    return pattern.startswith(_HINT_PREFIX)


def is_mcp_allowlist_entry(pattern: str) -> bool:
    """Return True for MCP-qualified allowlist entries (``server.tool`` / ``server.*``).

    File paths (``*.py`` or containing a slash) are tool-file entries, not MCP names.
    """
    if pattern.endswith(".py") or "/" in pattern or "\\" in pattern:
        return False
    return "." in pattern


def allowlist_contains_glob(allowlist: list[str]) -> bool:
    """Return True when any allowlist entry uses shell-glob syntax or a hint: prefix.

    Hint patterns are treated like globs because they match multiple tools implicitly,
    so skipped-MCP-tool warnings are suppressed when hint patterns are present.
    """
    return any(
        is_hint_pattern(p) or any(char in p for char in "*?[") for p in allowlist
    )


def matching_allowlist_tools(pattern: str, tools: list[ToolSpec]) -> list[ToolSpec]:
    """Return tools matched by an allowlist entry (name glob or hint: prefix)."""
    if is_hint_pattern(pattern):
        hint = pattern[len(_HINT_PREFIX) :]
        return [tool for tool in tools if hint in tool.hints]
    return [tool for tool in tools if fnmatchcase(tool.name, pattern)]


def tool_matches_allowlist(
    tool_name: str,
    allowlist: list[str],
    hints: frozenset[str] = frozenset(),
) -> bool:
    """Return True when a tool name (or hint) matches any allowlist entry."""
    for pattern in allowlist:
        if is_hint_pattern(pattern):
            hint = pattern[len(_HINT_PREFIX) :]
            if hint in hints:
                return True
        elif fnmatchcase(tool_name, pattern):
            return True
    return False
