"""CLI configuration setup.

Handles initialization of configuration from CLI arguments,
resolving precedence between CLI args, saved configs, env vars, and defaults.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..gears import parse_gear, resolve_gear
from ..profiles import get_profile
from ..tools import get_toolchain
from ..tools._allowlist import (
    TOOL_PRESETS,
    expand_tool_allowlist_presets,
)
from .chat import ChatConfig
from .core import Config, get_config, set_config, set_config_from_workspace

if TYPE_CHECKING:
    from ..tools.base import ToolFormat

logger = logging.getLogger(__name__)


def _get_model_default_tool_format(model: str | None) -> str | None:
    """Get the model's preferred tool format, if any.

    Returns the default_tool_format from ModelMeta, or None if not set."""
    if not model:
        return None
    try:
        from ..llm.models import get_model

        meta = get_model(model)
        return meta.default_tool_format
    except (ImportError, KeyError, ValueError, AttributeError):
        return None


def _is_tool_file_path(value: str) -> bool:
    return (
        value.endswith(".py")
        or value.startswith(("/", "./", "../", "~"))
        or (
            len(value) > 2 and value[1] == ":" and value[2] in "/\\"  # Windows C:\...
        )
    )


def _is_mcp_tool_name(value: str) -> bool:
    """Return True if *value* looks like a dotted MCP tool name (``server.tool``).

    MCP tool names use the ``<server>.<tool>`` convention and cannot be validated
    against the built-in toolchain at config-setup time because MCP servers are
    not yet initialized.  They are identified by the presence of a dot with
    non-empty text on both sides, and by NOT matching the file-path heuristics.
    """
    if _is_tool_file_path(value):
        return False
    dot_idx = value.find(".")
    return dot_idx > 0 and dot_idx < len(value) - 1


def _resolve_manifest_aliases(tool_allowlist: str, workspace: Path) -> str:
    """Resolve manifest aliases before applying CLI allowlist precedence."""
    if tool_allowlist.startswith(("+", "-")):
        return tool_allowlist

    from ..tool_manifests import load_task_manifest

    requested_tools = [
        tool.strip() for tool in tool_allowlist.split(",") if tool.strip()
    ]
    manifest_aliases: list[str] = []
    explicit_manifest_tools: list[str] = []
    non_alias_tools: list[str] = []
    has_mcp_only_alias = False
    for requested_tool in requested_tools:
        # Presets are capability policies, not workspace-extensible aliases. A
        # manifest from an untrusted workspace must never shadow one (for
        # example, ``read-only`` with an MCP tool named ``evil.exec``).
        if requested_tool in TOOL_PRESETS:
            non_alias_tools.append(requested_tool)
            continue

        try:
            get_toolchain([requested_tool])
        except ValueError as e:
            if "is unavailable" in str(e):
                raise
            try:
                manifest = load_task_manifest(requested_tool, workspace)
            except (OSError, ValueError):
                non_alias_tools.append(requested_tool)
                continue
            if manifest.builtin_tools:
                explicit_manifest_tools.extend(manifest.all_tool_names)
            else:
                has_mcp_only_alias = True
                manifest_aliases.extend(manifest.tool_names)
        else:
            non_alias_tools.append(requested_tool)

    if not has_mcp_only_alias:
        return tool_allowlist
    if explicit_manifest_tools:
        raise ValueError(
            "MCP-only manifest aliases cannot be combined with manifests "
            "that declare builtin_tools"
        )

    presets = [tool for tool in non_alias_tools if tool in TOOL_PRESETS]
    if presets:
        if len(non_alias_tools) != 1:
            preset_list = ", ".join(presets)
            raise ValueError(
                f"Tool preset(s) {preset_list} cannot be combined with other tools"
            )
        preset_tools = list(TOOL_PRESETS[presets[0]])
        return ",".join([*preset_tools, *manifest_aliases])

    return "+" + ",".join([*manifest_aliases, *non_alias_tools])


def _normalize_tool_allowlist(
    allowlist: list[str] | None, workspace: Path | None = None
) -> list[str] | None:
    """Normalize an allowlist while preserving custom tool file paths and preset names.

    Preset names (e.g. ``"read-only"``) are kept verbatim so that provenance is
    preserved when the config is saved and later resumed.  Expansion into concrete
    tool names happens at toolchain initialisation time (``tools/__init__.py``),
    not here.

    ``get_toolchain()`` validates and expands named tools, but custom tool files
    are loaded later by ``init_tools()`` and must remain as file paths.

    When *workspace* is provided, a single unknown item is checked against the
    workspace manifest (``state/task-manifests.jsonl``).  If it matches a task
    type, it is expanded to the manifest's ``builtin_tools + tool_names``
    list — making ``--tools code_review`` an ergonomic alias for
    ``--tool-manifest code_review``.

    MCP dotted tool names (``server.tool``) are passed through as-is because
    they cannot be validated against the built-in toolchain before MCP servers
    are initialized.
    """
    if allowlist is None:
        return None

    # If the allowlist is a single named preset, preserve it as-is so that
    # resumed sessions can still detect it as a preset (not just a tool list
    # that happens to match the preset's expansion).
    if len(allowlist) == 1 and allowlist[0] in TOOL_PRESETS:
        return list(allowlist)

    allowlist = expand_tool_allowlist_presets(allowlist)
    assert allowlist is not None
    normalized: list[str] = []
    seen: set[str] = set()

    for item in allowlist:
        if _is_tool_file_path(item):
            resolved = Path(item).expanduser().resolve()
            if not resolved.exists():
                raise ValueError(f"Tool file does not exist: {item}")
            if not resolved.is_file():
                raise ValueError(f"Tool path is not a file: {item}")
            if resolved.suffix != ".py":
                raise ValueError(f"Tool file must be a .py file: {item}")
            normalized_item = str(resolved)
            if normalized_item not in seen:
                normalized.append(normalized_item)
                seen.add(normalized_item)
            continue

        # MCP dotted tool names (server.tool) are passed through without
        # validation — the MCP layer resolves them at session startup time.
        if _is_mcp_tool_name(item):
            if item not in seen:
                normalized.append(item)
                seen.add(item)
            continue

        # Built-in tools always take priority over manifest aliases so that a
        # manifest task type named "read" can never silently replace the built-in
        # read tool.  Try get_toolchain first; only fall through to the manifest
        # lookup when the name is not a known built-in.
        try:
            toolspecs = list(get_toolchain([item]))
            for toolspec in toolspecs:
                if toolspec.name in seen:
                    continue
                normalized.append(toolspec.name)
                seen.add(toolspec.name)
            continue
        except ValueError as e:
            # Re-raise when the tool IS registered but unavailable: the name is
            # known to the toolchain, so the manifest must not silently shadow it.
            # Only fall through to the manifest lookup when the name is truly not
            # registered at all ("not found").
            if "is unavailable" in str(e):
                raise
            # name is completely unknown — check manifest next

        # Check if the item is a manifest task type alias when the workspace is
        # known.  This makes ``--tools code_review`` behave identically to
        # ``--tool-manifest code_review`` for workspaces that ship a manifest.
        if workspace is not None:
            from ..tool_manifests import load_task_manifest

            try:
                manifest = load_task_manifest(item, workspace)
            except (OSError, ValueError):
                manifest = None

            if manifest is not None:
                for manifest_tool_name in manifest.all_tool_names:
                    if manifest_tool_name not in seen:
                        normalized.append(manifest_tool_name)
                        seen.add(manifest_tool_name)
                continue

        # Not a known built-in and not a manifest alias — re-raise via get_toolchain
        # so the caller gets the standard "Tool 'X' not found" error message.
        for toolspec in get_toolchain([item]):
            if toolspec.name in seen:
                continue
            normalized.append(toolspec.name)
            seen.add(toolspec.name)

    return normalized


def setup_config_from_cli(
    workspace: Path,
    logdir: Path,
    model: str | None = None,
    tool_allowlist: str | None = None,
    manifest_workspace: Path | None = None,
    tool_format: "ToolFormat | None" = None,
    prune_tool_output: bool | None = None,
    gear: int | None = None,
    no_confirm: bool | None = None,
    stream: bool = True,
    interactive: bool = True,
    agent_path: Path | None = None,
) -> Config:
    """
    Initialize and return a complete config from CLI arguments and workspace.

    Handles the precedence: CLI args -> saved conversation config -> env vars -> config files -> defaults
    """

    # Load base config from workspace
    set_config_from_workspace(workspace)
    config = get_config()

    # Check if we're resuming an existing conversation
    existing_chat_config = None
    if logdir.exists() and (logdir / "config.toml").exists():
        existing_chat_config = ChatConfig.from_logdir(logdir)

    # Resolve configuration values with proper precedence
    # For resuming: CLI args -> saved conversation config -> env vars/config files
    # For new conversations: CLI args -> env vars/config files -> defaults
    resolved_model: str | None
    if model is not None:
        # CLI override always takes precedence
        resolved_model = model
    elif existing_chat_config and existing_chat_config.model:
        # When resuming, use saved conversation model unless CLI override provided
        resolved_model = existing_chat_config.model
    else:
        # Fall back to env/config for new conversations or when no saved model
        resolved_model = config.get_env("MODEL")

    resolved_gear = parse_gear(gear)
    if (
        resolved_gear is None
        and existing_chat_config
        and existing_chat_config.gear is not None
    ):
        resolved_gear = parse_gear(existing_chat_config.gear)
    if resolved_gear is None:
        settings_gear = (
            config.project.settings.gear
            if config.project and config.project.settings.gear is not None
            else config.user.settings.gear
        )
        resolved_gear = parse_gear(settings_gear)

    gear_profile_name: str | None = None
    gear_tool_allowlist: tuple[str, ...] | None = None
    gear_no_confirm: bool | None = None
    if resolved_gear is not None:
        gear_resolution = resolve_gear(resolved_gear)
        gear_profile_name = gear_resolution.profile_name
        gear_tool_allowlist = gear_resolution.tool_allowlist
        gear_no_confirm = gear_resolution.no_confirm

    # Handle tool allowlist with similar precedence. The configuration workspace
    # can differ from the workspace that owns task manifests (for ``@log``).
    resolved_tool_allowlist: list[str] | None = None
    requested_tool_allowlist = tool_allowlist
    if tool_allowlist is not None:
        tool_allowlist = _resolve_manifest_aliases(
            tool_allowlist, manifest_workspace or workspace
        )

        # Check for additive syntax (starts with '+')
        if tool_allowlist.startswith("+"):
            # Strip the '+' prefix and parse the additional tools
            tool_list_str = tool_allowlist[1:]
            additional_tools = [
                tool.strip() for tool in tool_list_str.split(",") if tool.strip()
            ]
            # Add to the configured tool policy when one exists; otherwise use
            # the built-in defaults. Additive CLI features such as task manifests
            # must not silently replace a project's TOOL_ALLOWLIST configuration.
            if existing_chat_config and existing_chat_config.tools is not None:
                base_tools = existing_chat_config.tools
            elif tools_env := config.get_env("TOOL_ALLOWLIST"):
                base_tools = [tool.strip() for tool in tools_env.split(",")]
            else:
                base_tools = [tool.name for tool in get_toolchain(None)]
            # A persisted/configured preset is valid by itself but cannot be
            # combined with additional names. Expand it before applying the
            # additive override so ``read-only`` + ``save`` becomes the concrete
            # allowlist ``read,save`` rather than an invalid mixed preset list.
            resolved_tool_allowlist = expand_tool_allowlist_presets(base_tools.copy())
            assert resolved_tool_allowlist is not None
            for tool in additional_tools:
                if tool not in resolved_tool_allowlist:
                    resolved_tool_allowlist.append(tool)
        elif tool_allowlist.startswith("-"):
            # Exclusion syntax: start with defaults, remove specified tools
            tool_list_str = tool_allowlist[1:]
            excluded_tools = [
                tool.strip() for tool in tool_list_str.split(",") if tool.strip()
            ]
            # Detect attempts to exclude preset names (they're not tools in the
            # default set; '-read-only' selects nothing and is almost certainly wrong).
            preset_exclusions = [t for t in excluded_tools if t in TOOL_PRESETS]
            if preset_exclusions:
                raise ValueError(
                    f"Cannot exclude preset name '{preset_exclusions[0]}' with '-' syntax. "
                    f"Presets select an exclusive tool boundary — use "
                    f"'--tools {preset_exclusions[0]}' to select one."
                )
            default_tools = [tool.name for tool in get_toolchain(None)]
            non_default = [
                t
                for t in excluded_tools
                if t not in default_tools and t not in TOOL_PRESETS
            ]
            if non_default:
                logger.warning(
                    "Tool(s) %s are not in the default toolset and cannot be excluded",
                    ", ".join(non_default),
                )
            resolved_tool_allowlist = [
                tool for tool in default_tools if tool not in excluded_tools
            ]
        elif tool_allowlist == "":
            # Explicitly empty: disable all tools (--tools none)
            resolved_tool_allowlist = []
        else:
            # Normal mode - CLI override replaces defaults
            resolved_tool_allowlist = [
                tool.strip() for tool in tool_allowlist.split(",") if tool.strip()
            ]
    elif gear_tool_allowlist is not None:
        if gear_tool_allowlist and gear_tool_allowlist[0].startswith("+"):
            default_tools = [tool.name for tool in get_toolchain(None)]
            resolved_tool_allowlist = default_tools.copy()
            for tool in (item.removeprefix("+") for item in gear_tool_allowlist):
                if tool not in resolved_tool_allowlist:
                    resolved_tool_allowlist.append(tool)
        else:
            resolved_tool_allowlist = list(gear_tool_allowlist)
    elif existing_chat_config and existing_chat_config.tools:
        # When resuming, use saved conversation tools unless CLI override provided
        resolved_tool_allowlist = existing_chat_config.tools
    elif tools_env := config.get_env("TOOL_ALLOWLIST"):
        # Fall back to env/config for new conversations or when no saved tools
        resolved_tool_allowlist = [
            tool.strip() for tool in tools_env.split(",") if tool.strip()
        ]

    # Profiles may override a gear's tool list. Apply that final override before
    # deciding whether non-interactive mode should add the completion signal.
    if gear_profile_name and not agent_path:
        gear_profile = get_profile(gear_profile_name)
        if gear_profile and gear_profile.tools is not None and tool_allowlist is None:
            resolved_tool_allowlist = list(gear_profile.tools)

    # Keep the exclusive boundary when an MCP-only manifest alias extends a
    # preset. Alias resolution expands that combination into concrete tools, so
    # compare the resolved CLI value with the original request before inspecting
    # the configured base preset. An ordinary explicit override such as
    # ``--tools read`` must retain non-interactive completion semantics.
    manifest_alias_resolved = (
        requested_tool_allowlist is not None
        and tool_allowlist != requested_tool_allowlist
    )
    requested_tool_names = (
        [tool.strip() for tool in requested_tool_allowlist.split(",")]
        if requested_tool_allowlist is not None
        else []
    )
    configured_base_tools = (
        existing_chat_config.tools
        if existing_chat_config and existing_chat_config.tools
        else (
            [tool.strip() for tool in tools_env.split(",") if tool.strip()]
            if (tools_env := config.get_env("TOOL_ALLOWLIST"))
            else None
        )
    )
    configured_base_is_preset = (
        configured_base_tools is not None
        and len(configured_base_tools) == 1
        and configured_base_tools[0] in TOOL_PRESETS
    )
    tool_preset_selected = (
        (
            resolved_tool_allowlist is not None
            and len(resolved_tool_allowlist) == 1
            and resolved_tool_allowlist[0] in TOOL_PRESETS
        )
        or any(tool in TOOL_PRESETS for tool in requested_tool_names)
        or (manifest_alias_resolved and configured_base_is_preset)
    )

    # Automatically add 'complete' tool in non-interactive mode, except for
    # exclusive named presets such as read-only audit mode.
    if not interactive and not tool_preset_selected:
        if resolved_tool_allowlist is None:
            # Get default tools and add complete to them
            default_tools = [tool.name for tool in get_toolchain(None)]
            resolved_tool_allowlist = default_tools
            if "complete" not in resolved_tool_allowlist:
                resolved_tool_allowlist.append("complete")
        elif "complete" not in resolved_tool_allowlist:
            resolved_tool_allowlist.append("complete")
        logger.debug("Added 'complete' tool to allowlist for non-interactive mode")

    # Handle tool_format with similar precedence
    if tool_format is not None:
        # CLI override always takes precedence
        resolved_tool_format = tool_format
    elif existing_chat_config and existing_chat_config.tool_format:
        # When resuming, use saved conversation tool_format unless CLI override provided
        resolved_tool_format = existing_chat_config.tool_format
    else:
        # Fall back to env/config, then model default, then "markdown"
        env_tool_format = config.get_env("TOOL_FORMAT")
        model_tool_format = _get_model_default_tool_format(resolved_model)
        if env_tool_format:
            resolved_tool_format = cast("ToolFormat", env_tool_format)
        elif model_tool_format:
            resolved_tool_format = cast("ToolFormat", model_tool_format)
            logger.info(
                "Using model default tool_format=%s for %s",
                model_tool_format,
                resolved_model,
            )
        else:
            resolved_tool_format = "markdown"

    resolved_no_confirm = gear_no_confirm if no_confirm is None else no_confirm

    # Handle agent_path with similar precedence
    resolved_agent_path: Path | None = agent_path
    if agent_path is None and existing_chat_config and existing_chat_config.agent:
        # When resuming, use saved conversation agent unless CLI override provided
        resolved_agent_path = existing_chat_config.agent

    # Create or load chat config with CLI overrides
    logdir.mkdir(parents=True, exist_ok=True)
    config.chat = ChatConfig.load_or_create(
        logdir=logdir,
        cli_config=ChatConfig(
            model=resolved_model,
            tool_format=resolved_tool_format,
            gear=resolved_gear,
            stream=stream,
            interactive=interactive,
            no_confirm=resolved_no_confirm,
            workspace=workspace,
            agent=resolved_agent_path,
        ),
    )

    if prune_tool_output is not None:
        config.chat.env = {
            **config.chat.env,
            "PRUNE_TOOL_OUTPUT": "1" if prune_tool_output else "0",
        }

    # Set tools if not already set or if CLI/gear override provided
    if (
        config.chat.tools is None
        or tool_allowlist is not None
        or gear_tool_allowlist is not None
    ):
        config.chat.tools = _normalize_tool_allowlist(
            resolved_tool_allowlist, workspace=manifest_workspace or workspace
        )

    # Save and set the final config
    config.chat.save()
    set_config(config)
    return config
