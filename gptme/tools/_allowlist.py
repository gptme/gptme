import difflib
from collections.abc import Iterable
from fnmatch import fnmatchcase

from .base import ToolSpec

_HINT_PREFIX = "hint:"
READ_ONLY_TOOL_PRESET = ("read",)
TOOL_PRESETS: dict[str, tuple[str, ...]] = {
    "read-only": READ_ONLY_TOOL_PRESET,
}
TOOL_PRESET_NAMES = tuple(TOOL_PRESETS)


def is_tool_file_path(name: str) -> bool:
    """Return True if *name* uses supported custom Python-tool path syntax."""
    return (
        name.endswith(".py")
        or name.startswith(("/", "./", "../", "~"))
        or (len(name) > 2 and name[1] == ":" and name[2] in "/\\")
    )


def is_glob_allowlist_pattern(name: str) -> bool:
    """Return True if *name* uses glob metacharacters as an allowlist pattern."""
    return any(c in name for c in "*?[")


def _is_mcp_tool_name(name: str) -> bool:
    """Return True if the name looks like an MCP dotted tool (``server.tool``)."""
    if is_tool_file_path(name) or "/" in name:
        return False
    dot_idx = name.find(".")
    return dot_idx > 0 and dot_idx < len(name) - 1


def expand_tool_allowlist_presets(allowlist: list[str] | None) -> list[str] | None:
    """Expand named tool presets into concrete tool names.

    Presets are exclusive capability boundaries, not shortcuts that can be mixed
    with arbitrary builtin tools.  However, MCP dotted names (``server.tool``)
    are additive—they extend a session with external-server capabilities and
    do not dilute the preset's builtin boundary.  A stored allowlist of
    ``["read-only", "search.query"]`` is therefore valid and is expanded to
    ``["read", "search.query"]``.

    Use hint-based allowlists for intentionally broad category matching.
    """
    if allowlist is None:
        return None

    presets = [item for item in allowlist if item in TOOL_PRESETS]
    if not presets:
        return allowlist
    if len(presets) > 1:
        raise ValueError(
            f"Tool preset(s) {', '.join(presets)} cannot be combined with other tools"
        )
    if len(allowlist) != 1:
        # MCP dotted names may accompany a preset: they are purely additive and
        # do not widen the builtin capability boundary.
        non_preset_non_mcp = [
            item
            for item in allowlist
            if item not in TOOL_PRESETS and not _is_mcp_tool_name(item)
        ]
        if non_preset_non_mcp:
            preset_list = ", ".join(presets)
            raise ValueError(
                f"Tool preset(s) {preset_list} cannot be combined with other tools"
            )
        # Expand the preset and keep the MCP tools verbatim.
        mcp_tools = [item for item in allowlist if _is_mcp_tool_name(item)]
        return [*TOOL_PRESETS[presets[0]], *mcp_tools]
    return list(TOOL_PRESETS[presets[0]])


def looks_like_builtin_typo(
    name: str, known_names: Iterable[str], *, cutoff: float = 0.8
) -> bool:
    """Return True if *name* is a close misspelling of a known tool or preset.

    Exact matches are not typos — those are handled by builtin precedence.
    Close matches such as ``shel`` vs ``shell`` must not silently resolve as
    workspace manifest aliases.
    """
    if name in TOOL_PRESETS or name in known_names:
        return False
    corpus = [*TOOL_PRESETS, *known_names]
    return bool(difflib.get_close_matches(name, corpus, n=1, cutoff=cutoff))


def expand_exclusive_preset_if_mixed(tools: list[str]) -> list[str]:
    """Expand an exclusive preset when mixed with extra builtin names.

    Presets may sit next to MCP dotted names without expanding. Extra
    builtins force expansion so later exclusive-preset checks do not reject
    the combined list. Duplicate preset names are collapsed first so a
    surviving ``read-only`` joined with an explicit ``read-only`` does not
    look like two presets.
    """
    tools = list(dict.fromkeys(tools))
    extra_non_mcp = [
        tool
        for tool in tools
        if tool not in TOOL_PRESETS and not _is_mcp_tool_name(tool)
    ]
    if extra_non_mcp and any(tool in TOOL_PRESETS for tool in tools):
        preset_and_mcp = [
            tool for tool in tools if tool in TOOL_PRESETS or _is_mcp_tool_name(tool)
        ]
        expanded = expand_tool_allowlist_presets(preset_and_mcp)
        assert expanded is not None
        return list(dict.fromkeys([*expanded, *extra_non_mcp]))
    return tools


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
