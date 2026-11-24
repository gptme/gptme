"""CLI configuration setup.

Handles initialization of configuration from CLI arguments,
resolving precedence between CLI args, saved configs, env vars, and defaults.
"""

import logging
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..context.config import parse_context_budget
from ..tools import get_toolchain
from ..tools._allowlist import (
    TOOL_PRESETS,
    expand_tool_allowlist_presets,
)
from ..tools._allowlist import is_tool_file_path as _is_tool_file_path
from .chat import ChatConfig
from .core import (
    Config,
    get_config,
    resolve_model_source,
    set_config,
    set_config_from_workspace,
)

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


def _normalize_tool_allowlist(allowlist: list[str] | None) -> list[str] | None:
    """Normalize an allowlist while preserving custom tool file paths and preset names.

    Preset names (e.g. ``"read-only"``) are kept verbatim so that provenance is
    preserved when the config is saved and later resumed.  Expansion into concrete
    tool names happens at toolchain initialisation time (``tools/__init__.py``),
    not here.

    ``get_toolchain()`` validates and expands named tools, but custom tool files
    are loaded later by ``init_tools()`` and must remain as file paths.
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

        # Per-item resolution would warn about "excluded" MCP tools once per item
        for tool in get_toolchain([item], warn_skipped_mcp=False):
            if tool.name in seen:
                continue
            normalized.append(tool.name)
            seen.add(tool.name)

    return normalized


def parse_tool_specs(specs: Iterable[str]) -> str | None:
    """Parse ``--tools`` / ``TOOL_ALLOWLIST`` values into the internal spec string.

    Each spec may itself be comma-separated. Returns ``None`` (use defaults),
    ``""`` (``none``: disable all tools), ``"+a,b"`` (add to defaults),
    ``"-a,b"`` (remove from defaults) or ``"a,b"`` (exact list). Every name in
    ``+``/``-`` mode carries its own prefix, which is stripped here.

    Raises:
        ValueError: on ``none`` combined with other tools, mixed ``+``/``-``
            syntax, or bare names mixed with ``-`` exclusions.
    """
    tools_list = [t.strip() for spec in specs for t in spec.split(",") if t.strip()]

    if any(t.lower() == "none" for t in tools_list):
        non_none = [t for t in tools_list if t.lower() != "none"]
        if non_none:
            raise ValueError(
                f"Cannot combine 'none' with other tools: {', '.join(non_none)}"
            )
        return ""

    additive_mode = any(t.startswith("+") for t in tools_list)
    exclusion_mode = any(t.startswith("-") for t in tools_list)
    if additive_mode and exclusion_mode:
        raise ValueError(
            "Cannot mix '+tool' (additive) and '-tool' (exclusion) syntax. "
            "Use one or the other."
        )

    if additive_mode:
        additional_tools = [t for t in (t.removeprefix("+") for t in tools_list) if t]
        # Just '+' means use defaults
        return "+" + ",".join(additional_tools) if additional_tools else None

    if exclusion_mode:
        # Bare tool names mixed with '-' exclusion tools is ambiguous
        bare_tools = [t for t in tools_list if not t.startswith("-")]
        if bare_tools:
            raise ValueError(
                f"Cannot mix bare tool names ({', '.join(bare_tools)}) with '-tool' exclusion syntax. "
                "Prefix all tools with '-' to exclude them."
            )
        excluded_tools = [t for t in (t.removeprefix("-") for t in tools_list) if t]
        return "-" + ",".join(excluded_tools) if excluded_tools else None

    return ",".join(tools_list) if tools_list else None


def setup_config_from_cli(
    workspace: Path,
    logdir: Path,
    model: str | None = None,
    tool_allowlist: str | None = None,
    tool_format: "ToolFormat | None" = None,
    prune_tool_output: bool | None = None,
    no_confirm: bool | None = None,
    stream: bool = True,
    interactive: bool = True,
    agent_path: Path | None = None,
    allow_hosts: list[str] | None = None,
    context_budget: float | int | None = None,
    agent_profile: str | None = None,
) -> Config:
    """
    Initialize and return a complete config from CLI arguments and workspace.

    Model precedence is delegated to :func:`resolve_model_source`, which orders
    the layers by specificity: CLI flag -> saved conversation -> ``MODEL`` in the
    environment -> chat/project ``[env].MODEL`` -> ``[models].default`` -> user
    ``[env].MODEL`` -> auto-detection.
    """

    if context_budget is not None:
        context_budget = parse_context_budget(context_budget, "--context-budget")

    # Load base config from workspace
    set_config_from_workspace(workspace)
    config = get_config()

    # Approval must precede model/default resolution and any early tool loading.
    from .trust import approve_project_config

    approve_project_config(interactive, workspace=workspace)

    # Check if we're resuming an existing conversation
    existing_chat_config = None
    if logdir.exists() and (logdir / "config.toml").exists():
        existing_chat_config = ChatConfig.from_logdir(logdir)

    # Resolve the model through the shared layered resolver, so this path and
    # init_model() cannot drift apart again (see #3814).
    #
    # The existing chat config is not attached to `config` yet (that happens
    # below, via load_or_create), so hand the resolver a copy that carries it.
    # Otherwise the chat's own [env].MODEL — a more specific layer than the
    # global default — is invisible, and resuming a conversation that sets it
    # without a saved model would fall through to [models].default.
    resolution_config = (
        replace(config, chat=existing_chat_config) if existing_chat_config else config
    )
    resolution = resolve_model_source(
        resolution_config,
        cli_model=model,
        chat_model=existing_chat_config.model if existing_chat_config else None,
    )
    resolved_model: str | None = resolution[0] if resolution else None
    config._model_source = (
        (resolution[1], resolution[0]) if resolution is not None else None
    )

    # Handle tool allowlist with similar precedence
    resolved_tool_allowlist: list[str] | None = None
    # Saved tools of a resumed conversation; [] (saved --tools none) is a real
    # choice and must stay distinct from None (never set).
    saved_tools = existing_chat_config.tools if existing_chat_config else None
    # TOOL_ALLOWLIST (env or [env]) is documented to accept the same syntax as
    # --tools ("+x", "-x", "none"), so parse it with the same parser. It still
    # ranks below the CLI flag and below a resumed conversation's tools.
    allowlist_spec = tool_allowlist
    if allowlist_spec is None and saved_tools is None:
        if tools_env := config.get_env("TOOL_ALLOWLIST"):
            try:
                allowlist_spec = parse_tool_specs([tools_env])
            except ValueError as e:
                raise ValueError(f"Invalid TOOL_ALLOWLIST: {e}") from e
    if allowlist_spec is not None:
        # Check for additive syntax (starts with '+')
        if allowlist_spec.startswith("+"):
            # Strip the '+' prefix and parse the additional tools
            tool_list_str = allowlist_spec[1:]
            additional_tools = [
                tool.strip() for tool in tool_list_str.split(",") if tool.strip()
            ]
            # Get default tools and add the additional ones
            default_tools = [tool.name for tool in get_toolchain(None)]
            resolved_tool_allowlist = default_tools.copy()
            for tool in additional_tools:
                if tool not in resolved_tool_allowlist:
                    resolved_tool_allowlist.append(tool)
        elif allowlist_spec.startswith("-"):
            # Exclusion syntax: start with defaults, remove specified tools
            tool_list_str = allowlist_spec[1:]
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
        elif allowlist_spec == "":
            # Explicitly empty: disable all tools (--tools none)
            resolved_tool_allowlist = []
        else:
            # Normal mode - CLI override replaces defaults
            resolved_tool_allowlist = [
                tool.strip() for tool in allowlist_spec.split(",") if tool.strip()
            ]
    elif saved_tools is not None:
        # When resuming, use saved conversation tools unless CLI override provided.
        # Copy: the non-interactive 'complete' append below must not mutate
        # the saved config object.
        resolved_tool_allowlist = list(saved_tools)

    # A preset is "selected" if the allowlist is a literal preset name.
    # Preset names are now persisted verbatim (not expanded) so that resumed
    # sessions continue to be recognised here without ambiguity.
    tool_preset_selected = (
        resolved_tool_allowlist is not None
        and len(resolved_tool_allowlist) == 1
        and resolved_tool_allowlist[0] in TOOL_PRESETS
    )

    # Automatically add 'complete' tool in non-interactive mode, except for
    # exclusive named presets such as read-only audit mode.
    # Leave unrestricted defaults as None, including when saved/resumed. Tool
    # initialization adds complete without turning that default into an operator
    # boundary (which would incorrectly disable ACP delegation).
    if (
        not interactive
        and not tool_preset_selected
        and resolved_tool_allowlist is not None
    ):
        if "complete" not in resolved_tool_allowlist:
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

    resolved_no_confirm = no_confirm

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
            stream=stream,
            interactive=interactive,
            no_confirm=resolved_no_confirm,
            workspace=workspace,
            agent=resolved_agent_path,
            allow_hosts=allow_hosts,
            context_budget=context_budget,
            agent_profile=agent_profile,
        ),
    )
    # stream/interactive/no_confirm describe this run, not the conversation.
    # load_or_create only applies CLI values that differ from the dataclass
    # defaults, so a plain interactive resume of a conversation started with
    # --non-interactive would otherwise keep auto-confirming every tool.
    config.chat = replace(
        config.chat,
        stream=stream,
        interactive=interactive,
        no_confirm=resolved_no_confirm,
    )

    if prune_tool_output is not None:
        config.chat.env = {
            **config.chat.env,
            "PRUNE_TOOL_OUTPUT": "1" if prune_tool_output else "0",
        }

    # Set tools if not already set or if a CLI override was provided
    if config.chat.tools is None or tool_allowlist is not None:
        config.chat.tools = _normalize_tool_allowlist(resolved_tool_allowlist)
    elif (
        resolved_tool_allowlist is not None
        and "complete" in resolved_tool_allowlist
        and "complete" not in config.chat.tools
    ):
        # Non-interactive resume: add 'complete' to the saved filters verbatim.
        # Re-normalizing would expand hint:/glob filters against this run's
        # tools and drop any that match nothing now.
        config.chat.tools = [*config.chat.tools, "complete"]

    # Save and set the final config
    config.chat.save()
    set_config(config)
    return config
