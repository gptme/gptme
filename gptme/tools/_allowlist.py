from fnmatch import fnmatchcase

from .base import ToolSpec

_HINT_PREFIX = "hint:"


def is_hint_pattern(pattern: str) -> bool:
    """Return True if the pattern is a hint-based filter (e.g. 'hint:read-only')."""
    return pattern.startswith(_HINT_PREFIX)


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
