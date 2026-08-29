from __future__ import annotations

import importlib
import logging
import pkgutil
import threading
import time
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

from ..constants import INTERRUPT_CONTENT
from ..message import Message
from ..plugins import get_plugin_tool_modules
from ..telemetry import trace_function
from ..util.interrupt import clear_interruptible
from ..util.terminal import terminal_state_title
from ._allowlist import (
    allowlist_contains_glob,
    expand_tool_allowlist_presets,
    is_hint_pattern,
    matching_allowlist_tools,
    tool_matches_allowlist,
)
from .base import (
    Parameter,
    ToolFormat,
    ToolFunction,
    ToolSpec,
    ToolUse,
    _iter_tool_specs,
    get_tool_format,
    set_tool_format,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from types import ModuleType

    from ..logmanager import Log

logger = logging.getLogger(__name__)


__all__ = [
    # types
    "ToolSpec",
    "ToolUse",
    "ToolFormat",
    "ToolFunction",
    "Parameter",
    # functions
    "get_tool_format",
    "set_tool_format",
    # context-local storage (for testing tool isolation)
    "_loaded_tools_var",
]

# Context-local storage for tools
# Each context (thread/async task) gets its own independent copy of tool state
_loaded_tools_var: ContextVar[list[ToolSpec] | None] = ContextVar(
    "loaded_tools", default=None
)
_available_tools_var: ContextVar[list[ToolSpec] | None] = ContextVar(
    "available_tools", default=None
)
# Effective operator allowlist from the last init_tools() in this context.
# None means unrestricted (default session); a list is a hard cap for model
# enablement via request_tool_change. /tools load still uses load_tool()
# directly and is not gated here.
_session_allowlist_var: ContextVar[list[str] | None] = ContextVar(
    "session_allowlist", default=None
)

# Note: Tools must be initialized in each context that needs them.
# This is particularly important for server environments where request handling
# happens in different contexts than where tools were initially loaded.


def _get_loaded_tools() -> list[ToolSpec]:
    tools = _loaded_tools_var.get()
    if tools is None:
        tools = []
        _loaded_tools_var.set(tools)
    return tools


def _get_available_tools_cache() -> list[ToolSpec] | None:
    return _available_tools_var.get()


def _set_available_tools_cache(tools: list[ToolSpec] | None) -> None:
    _available_tools_var.set(tools)


def _collect_tool_modules(
    module_name: str,
    module: ModuleType,
) -> list[ModuleType]:
    """Recursively collect a package/module and its public descendants."""
    modules = [module]
    if not hasattr(module, "__path__"):
        return modules

    for _, submodule_name, _ in pkgutil.iter_modules(module.__path__):
        if submodule_name.startswith("_"):
            continue
        full_submodule_name = f"{module_name}.{submodule_name}"
        try:
            submodule = importlib.import_module(full_submodule_name)
        except ModuleNotFoundError as e:
            logger.warning(
                "Missing dependency '%s' for module %s",
                e.name,
                full_submodule_name,
            )
            continue
        modules.extend(_collect_tool_modules(full_submodule_name, submodule))

    return modules


def _discover_tools(module_names: list[str]) -> list[ToolSpec]:
    """Discover tools in a package or module, given the module/package name as a string."""
    tools = []
    seen_specs: set[int] = set()
    for module_name in module_names:
        try:
            # Dynamically import the package or module
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            logger.warning("Module or package %s not found", module_name)
            continue

        modules = _collect_tool_modules(module_name, module)

        # Find instances of ToolSpec in the modules
        for module in modules:
            for obj in _iter_tool_specs(module):
                spec_id = id(obj)
                if spec_id in seen_specs:
                    continue
                seen_specs.add(spec_id)
                tools.append(obj)

    return tools


# Global lock for thread-safe tool initialization
_tools_init_lock = threading.Lock()
_warned_mcp_allowlists: set[tuple[str, ...]] = set()
_warned_mcp_allowlists_lock = threading.Lock()


def _init_single_tool(tool: ToolSpec) -> ToolSpec:
    """Initialize a single tool: run its init(), register hooks and commands.

    Caller is responsible for acquiring _tools_init_lock if needed.
    """
    if tool.init:
        tool = tool.init()
    tool.register_hooks()
    tool.register_commands()
    return tool


def init_tools(
    allowlist: list[str] | None = None,
) -> list[ToolSpec]:
    """Initialize tools in a thread-safe manner.

    This function is thread-safe and can be called from multiple threads.
    Each thread will get its own copy of the tools.

    If allowlist is not provided, it will be loaded from the environment variable
    TOOL_ALLOWLIST or the chat config (if set).

    Items in allowlist can be tool names (e.g. "shell") or paths to .py files
    containing ToolSpec definitions (e.g. "path/to/mytool.py").
    """
    from ..config import get_config  # fmt: skip

    with _tools_init_lock:
        loaded_tools = _get_loaded_tools()
        config = get_config()

        if allowlist is None:
            env_allowlist = config.get_env("TOOL_ALLOWLIST")
            if env_allowlist:
                allowlist = env_allowlist.split(",")
            elif config.chat and config.chat.tools:
                allowlist = config.chat.tools

        allowlist = expand_tool_allowlist_presets(allowlist)
        set_session_allowlist(allowlist)

        # Partition allowlist into file paths and tool names
        file_paths: list[str] = []
        tool_names: list[str] = []
        for item in allowlist or []:
            if item.endswith(".py") or "/" in item or "\\" in item:
                file_paths.append(item)
            else:
                tool_names.append(item)

        # Load tools from file paths first
        if file_paths:
            from .base import load_from_file

            for file_path in file_paths:
                path = Path(file_path).expanduser()
                for tool in load_from_file(path):
                    if not has_tool(tool.name):
                        tool = _init_single_tool(tool)
                        loaded_tools.append(tool)

        # Load built-in tools by name
        # When file paths are present, only load explicitly named built-in tools
        # (file_paths + no names = only file tools; file_paths + names = both)
        name_allowlist = tool_names if (tool_names or file_paths) else allowlist
        for tool in get_toolchain(name_allowlist):
            if has_tool(tool.name):
                continue
            tool = _init_single_tool(tool)
            loaded_tools.append(tool)

        available_tools = get_available_tools()
        for tool_name in tool_names:
            if is_hint_pattern(tool_name):
                continue  # hint patterns match 0+ tools by hint, no name validation
            if matching_allowlist_tools(tool_name, loaded_tools):
                continue
            matched_available = matching_allowlist_tools(tool_name, available_tools)
            if matched_available:
                if any(tool.is_available for tool in matched_available):
                    raise ValueError(
                        f"Tool '{tool_name}' matched available tools that should "
                        "have been loaded but were not found in loaded_tools"
                    )
                logger.warning(
                    "%s Skipping.", _unavailable_message(tool_name, matched_available)
                )
                continue
            raise ValueError(f"Tool '{tool_name}' not found")

        return loaded_tools


def _unavailable_message(tool_name: str, matched_tools: list[ToolSpec]) -> str:
    """Build an accurate 'unavailable' message, preferring a tool-provided hint."""
    hint = next((t.available_hint for t in matched_tools if t.available_hint), None)
    base = f"Tool '{tool_name}' is unavailable"
    if hint:
        hint = hint.rstrip()
        if hint[-1:] not in ".!?":
            hint += "."
        return f"{base}: {hint}"
    return (
        f"{base} — it was discovered but its availability check failed "
        "(a required service may not be running, or optional "
        "dependencies/credentials are missing)."
    )


def get_toolchain(
    allowlist: list[str] | None, *, strict: bool = True
) -> list[ToolSpec]:
    allowlist = expand_tool_allowlist_presets(allowlist)

    # Validate allowlist if provided
    # When strict=False, warn about missing/unavailable tools instead of raising.
    # Server contexts use strict=False since conversations may reference tools
    # that are no longer available.
    if allowlist is not None:
        available_tools = get_available_tools()
        available_tool_names = [tool.name for tool in available_tools]

        for tool_name in allowlist:
            if is_hint_pattern(tool_name):
                continue  # hint patterns match by tool hints, not by name
            matched_tools = matching_allowlist_tools(tool_name, available_tools)
            if not matched_tools:
                if strict:
                    raise ValueError(
                        f"Tool '{tool_name}' not found. Available tools: {', '.join(sorted(available_tool_names))}"
                    )
                logger.warning("Tool '%s' in allowlist not found, skipping", tool_name)
                continue

            if not any(tool.is_available for tool in matched_tools):
                msg = _unavailable_message(tool_name, matched_tools)
                if strict:
                    raise ValueError(msg)
                logger.warning("%s Skipping.", msg)
                continue

    tools = []
    warn_on_skipped_mcp = False
    if allowlist:
        warn_on_skipped_mcp = not allowlist_contains_glob(allowlist)
    skipped_mcp_tools = []
    for tool in get_available_tools():
        explicitly_allowed = allowlist is not None and tool_matches_allowlist(
            tool.name, allowlist, tool.hints
        )
        if allowlist is not None and not explicitly_allowed:
            if warn_on_skipped_mcp and tool.is_mcp and tool.is_available:
                skipped_mcp_tools.append(tool.name)
            continue
        if not tool.is_available:
            continue
        if tool.disabled_by_default:
            if not explicitly_allowed:
                continue
        tools.append(tool)
    if skipped_mcp_tools:
        allowlist_key = tuple(allowlist or [])
        with _warned_mcp_allowlists_lock:
            should_warn = allowlist_key not in _warned_mcp_allowlists
            if should_warn:
                _warned_mcp_allowlists.add(allowlist_key)
        if should_warn:
            logger.warning(
                "Tool allowlist excluded MCP tools: %s. Add glob patterns like "
                "'<server>.*' to include grouped MCP tools.",
                ", ".join(sorted(skipped_mcp_tools)),
            )
    return tools


@trace_function(name="tools.execute_msg", attributes={"component": "tools"})
def execute_msg(
    msg: Message,
    log: Log | None = None,
    workspace: Path | None = None,
    tool_timings: dict[str, float] | None = None,
) -> Generator[Message, None, None]:
    """Uses any tools called in a message and returns the response.

    Args:
        msg: The assistant message whose tool uses should be executed.
        log: Optional conversation log (passed through to tool execution).
        workspace: Optional workspace path (passed through to tool execution).
        tool_timings: Optional dict to accumulate per-tool wall-clock durations
            in milliseconds.  If provided, each executed tool's name is used as
            the key and its duration (ms) is *added* to any existing value so
            repeated calls to the same tool accumulate correctly.  Pass an empty
            dict ``{}`` from the caller and read it back after the generator is
            exhausted to obtain ``tool_ms_by_name`` for timing metadata.
    """
    assert msg.role == "assistant", "Only assistant messages can be executed"

    # Materialize every parsed tool_use first so each structured call still gets
    # exactly one result. Evaluate runnability once per call, immediately before
    # the branch, so an earlier request_tool_change in this response can enable
    # or disable a sibling without a concurrent load/unload skipping both the
    # execute and pairing paths (Anthropic 400, #554).
    classified = list(ToolUse.iter_from_content(msg.content))

    if not classified:
        return

    remaining = iter(classified)
    for tooluse in remaining:
        runnable = tooluse.is_runnable
        if runnable:
            with terminal_state_title(f"🛠️ running {tooluse.tool}"):
                t0 = time.monotonic()
                try:
                    yield from tooluse.execute(log=log, workspace=workspace)
                except KeyboardInterrupt:
                    clear_interruptible()
                    yield Message(
                        "system",
                        INTERRUPT_CONTENT,
                        call_id=tooluse.call_id,
                    )
                    # Drain the rest: any structured tool_use that's left in the
                    # message still needs a paired tool_result or the next API
                    # request will 400 with a dangling tool_use.
                    for rem_tu in remaining:
                        if rem_tu.call_id is not None:
                            yield Message(
                                "system",
                                f"Tool '{rem_tu.tool}' was not executed (interrupted).",
                                call_id=rem_tu.call_id,
                            )
                    return
                finally:
                    if tool_timings is not None:
                        elapsed_ms = (time.monotonic() - t0) * 1000
                        tool_timings[tooluse.tool] = (
                            tool_timings.get(tooluse.tool, 0.0) + elapsed_ms
                        )
        elif tooluse.call_id is not None:
            # A structured (tool-format) tool_use that isn't runnable still needs
            # a paired tool_result, or the next API request dangles it and 400s.
            # Markdown code blocks (call_id is None) are not API tool_uses, so
            # they're intentionally left unpaired.
            logger.warning(
                "Tool '%s' is not runnable; emitting an error tool_result to keep "
                "the tool_use/tool_result pairing valid.",
                tooluse.tool,
            )
            yield Message(
                "system",
                f"Tool '{tooluse.tool}' is not available for execution.",
                call_id=tooluse.call_id,
            )


def get_tool_for_langtag(lang: str) -> ToolSpec | None:
    """Get the tool that handles a given language tag.

    Called often when checking streaming output for executable blocks.
    Not cached since tools are thread-local and caching would be complex/brittle.
    """
    block_type = lang.split(" ")[0]
    for tool in _get_loaded_tools():
        if block_type in tool.block_types:
            return tool
    return None


def is_supported_langtag(lang: str) -> bool:
    return bool(get_tool_for_langtag(lang))


def get_available_tools(include_mcp: bool = True) -> list[ToolSpec]:
    from ..config import get_config  # fmt: skip
    from .mcp_adapter import create_mcp_tools  # fmt: skip

    # Only use cache if we want MCP tools (cache always includes MCP)
    available_tools = _get_available_tools_cache() if include_mcp else None

    if available_tools is None:
        # We need to load tools first
        config = get_config()

        tool_modules: list[str] = []
        env_tool_modules = config.get_env("TOOL_MODULES", "gptme.tools")

        if env_tool_modules:
            tool_modules = env_tool_modules.split(",")

        # Add plugin tool modules (user + project [plugins], layered)
        plugin_paths, enabled_plugins = config.get_plugin_config()
        if plugin_paths:
            plugin_tool_modules = get_plugin_tool_modules(
                plugin_paths,
                enabled_plugins=enabled_plugins,
            )
            tool_modules.extend(plugin_tool_modules)

        # Add tool modules from unified plugins (entry-point plugins)
        from ..plugins.registry import get_all_plugins

        all_plugins = get_all_plugins()
        for plugin in all_plugins:
            for mod in plugin.tool_modules:
                if mod not in tool_modules:
                    tool_modules.append(mod)

        available_tools = list(_discover_tools(tool_modules))

        # Add direct ToolSpec instances from unified plugins, then sort everything together
        for plugin in all_plugins:
            available_tools.extend(plugin.tools)

        available_tools.sort()

        if include_mcp:
            available_tools.extend(create_mcp_tools(config))
            # Only cache if we included MCP tools
            _set_available_tools_cache(available_tools)
        else:
            # Don't cache partial results
            return available_tools

    return available_tools


def clear_tools():
    """Clear all context-local tool state.

    Resets the ContextVar-bound tool list so the current context has a
    fresh empty list, fully decoupled from any other context (parent
    thread, sibling thread, etc.).

    Does NOT clear module-global state like _warned_mcp_allowlists, which
    is shared across all contexts for log-deduplication. Only the
    context-local tool list and its cache are reset.
    """
    _set_available_tools_cache(None)
    _loaded_tools_var.set([])
    _session_allowlist_var.set(None)


def get_tools() -> list[ToolSpec]:
    """Returns all loaded tools"""
    return _get_loaded_tools()


def set_tools(tools: list[ToolSpec]) -> None:
    """Set the loaded tools for the current context.

    Useful for restoring tools in a new asyncio task context where
    ContextVars from the parent context aren't visible.
    """
    _loaded_tools_var.set(tools)


def get_session_allowlist() -> list[str] | None:
    """Return the operator tool allowlist captured by the last init_tools()."""
    return _session_allowlist_var.get()


def set_session_allowlist(allowlist: list[str] | None) -> None:
    """Override the session tool allowlist for this context.

    ``None`` means unrestricted. An explicit list is the hard cap that
    ``request_tool_change`` enablement must honor.
    """
    _session_allowlist_var.set(list(allowlist) if allowlist is not None else None)


def unload_tool(tool_name: str) -> ToolSpec:
    """Unload one tool and best-effort unregister its session-local hooks.

    Commands are *not* unregistered. The slash-command registry is
    process-global (``gptme.commands.base._command_registry``), shared by every
    session in this process. Dropping a command here would remove that slash
    command from sibling conversations on gptme-server even though their
    context-local tool sets still contain the tool. Hooks are ContextVar-backed,
    so unregistering them is session-scoped and safe.

    Session isolation for commands is enforced at dispatch: ``handle_cmd``
    refuses to run a tool-owned command when that tool is not in this
    session's loaded set.
    """
    with _tools_init_lock:
        tool = get_tool(tool_name)
        if tool is None:
            raise ValueError(f"Tool '{tool_name}' is not loaded")

        from ..hooks import unregister_hook

        # Filter by identity: get_tool() matches name *or* block_types, so a
        # block-type argument would miss a name-only filter and leave a zombie
        # tool loaded after its hooks were unregistered.
        set_tools([loaded for loaded in get_tools() if loaded is not tool])
        for hook_name in tool.hooks:
            try:
                unregister_hook(f"{tool.name}.{hook_name}")
            except Exception:
                logger.exception(
                    "Failed to unregister hook '%s.%s' while unloading tool",
                    tool.name,
                    hook_name,
                )

        logger.info("Unloaded tool '%s' mid-conversation", tool_name)
        return tool


def get_tool(tool_name: str) -> ToolSpec | None:
    """Returns a loaded tool by name or block type."""
    loaded_tools = _get_loaded_tools()
    # check tool names
    for tool in loaded_tools:
        if tool.name == tool_name:
            return tool
    # check block types
    for tool in loaded_tools:
        if tool_name in tool.block_types:
            return tool
    return None


def has_tool(tool_name: str) -> bool:
    """Returns True if a tool is loaded."""
    return any(tool.name == tool_name for tool in _get_loaded_tools())


def notify_file_read(path: str, content: str) -> str | None:
    """Store a hashline snapshot unconditionally; return the tag only when hashline_edit is active.

    Always stores a snapshot so hashline_edit can edit files that were read before
    the tool was activated (e.g., via load_tool mid-session). Returns the tag only
    when hashline_edit is loaded so read.py can gate the [path#tag] header.

    This decouples read.py from the hashline_snapshot module: read.py calls this
    function without importing _hashline_snapshot directly.
    """
    from ._hashline_snapshot import store_snapshot

    tag = store_snapshot(path, content)
    return tag if has_tool("hashline_edit") else None


def load_tool(tool_name: str) -> ToolSpec:
    """Load a single tool by name mid-conversation.

    Finds the tool in available tools, initializes it, registers hooks/commands,
    and adds it to the loaded tools list.

    Thread-safe: uses _tools_init_lock to match init_tools() behavior.

    Raises:
        ValueError: If tool not found or already loaded.
    """
    with _tools_init_lock:
        if has_tool(tool_name):
            raise ValueError(f"Tool '{tool_name}' is already loaded")

        available = {t.name: t for t in get_available_tools()}
        if tool_name not in available:
            raise ValueError(
                f"Tool '{tool_name}' not found. Available: {', '.join(sorted(available.keys()))}"
            )

        tool = available[tool_name]
        if not tool.is_available:
            raise ValueError(_unavailable_message(tool_name, [tool]))

        # Initialize, register hooks/commands (shared logic)
        tool = _init_single_tool(tool)

        # Add to loaded tools
        _get_loaded_tools().append(tool)
        logger.info("Loaded tool '%s' mid-conversation", tool_name)

        return tool
