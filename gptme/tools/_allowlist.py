from fnmatch import fnmatchcase

from .base import ToolSpec


def allowlist_contains_glob(allowlist: list[str]) -> bool:
    """Return True when any allowlist entry uses shell-glob syntax."""

    return any(any(char in pattern for char in "*?[") for pattern in allowlist)


def matching_allowlist_tools(pattern: str, tools: list[ToolSpec]) -> list[ToolSpec]:
    """Return tools matched by an allowlist entry."""

    return [tool for tool in tools if fnmatchcase(tool.name, pattern)]


def tool_matches_allowlist(tool_name: str, allowlist: list[str]) -> bool:
    """Return True when a tool name matches any allowlist entry."""

    return any(fnmatchcase(tool_name, pattern) for pattern in allowlist)
