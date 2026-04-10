"""CLI configuration setup.

Handles initialization of configuration from CLI arguments,
resolving precedence between CLI args, saved configs, env vars, and defaults.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..tools import get_toolchain
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


def setup_config_from_cli(
    workspace: Path,
    logdir: Path,
    model: str | None = None,
    tool_allowlist: str | None = None,
    tool_format: "ToolFormat | None" = None,
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

    # Handle tool allowlist with similar precedence
    resolved_tool_allowlist: list[str] | None = None
    if tool_allowlist is not None:
        # Check for additive syntax (starts with '+')
        if tool_allowlist.startswith("+"):
            # Strip the '+' prefix and parse the additional tools
            tool_list_str = tool_allowlist[1:]
            additional_tools = [
                tool.strip() for tool in tool_list_str.split(",") if tool.strip()
            ]
            # Get default tools and add the additional ones
            default_tools = [tool.name for tool in get_toolchain(None)]
            resolved_tool_allowlist = default_tools.copy()
            for tool in additional_tools:
                if tool not in resolved_tool_allowlist:
                    resolved_tool_allowlist.append(tool)
        elif tool_allowlist.startswith("-"):
            # Exclusion syntax: start with defaults, remove specified tools
            tool_list_str = tool_allowlist[1:]
            excluded_tools = [
                tool.strip() for tool in tool_list_str.split(",") if tool.strip()
            ]
            default_tools = [tool.name for tool in get_toolchain(None)]
            non_default = [t for t in excluded_tools if t not in default_tools]
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
                tool.strip() for tool in tool_allowlist.split(",")
            ]
    elif existing_chat_config and existing_chat_config.tools:
        # When resuming, use saved conversation tools unless CLI override provided
        resolved_tool_allowlist = existing_chat_config.tools
    elif tools_env := config.get_env("TOOL_ALLOWLIST"):
        # Fall back to env/config for new conversations or when no saved tools
        resolved_tool_allowlist = [tool.strip() for tool in tools_env.split(",")]

    # Automatically add 'complete' tool in non-interactive mode
    if not interactive:
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
            workspace=workspace,
            agent=resolved_agent_path,
        ),
    )

    # Set tools if not already set or if CLI override provided
    if config.chat.tools is None or tool_allowlist is not None:
        config.chat.tools = [
            tool.name for tool in get_toolchain(resolved_tool_allowlist)
        ]

    # Save and set the final config
    config.chat.save()
    set_config(config)
    return config
