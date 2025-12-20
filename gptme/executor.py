"""
Shared execution infrastructure for gptme.

This module provides common execution setup used by the server API,
evaluation agents, and subagent tool, ensuring consistent environment
initialization across execution contexts.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

from .config import Config, set_config
from .init import init_hooks, init_tools

if TYPE_CHECKING:
    from .config import ChatConfig
    from .tools import ToolSpec

logger = logging.getLogger(__name__)


def prepare_execution_environment(
    workspace: Path,
    tools: list[str] | None = None,
    chat_config: ChatConfig | None = None,
) -> tuple[Config, list[ToolSpec]]:
    """
    Prepare the execution environment with config, tools, and hooks.

    This is common setup needed by Server, evals, and subagents.
    It handles:
    - Loading configuration from workspace
    - Setting chat config (if provided)
    - Initializing tools
    - Initializing hooks
    - Loading .env files

    Args:
        workspace: The workspace directory
        tools: Optional list of tools to initialize (defaults to all)
        chat_config: Optional ChatConfig to set on the config

    Returns:
        Tuple of (Config, list of initialized ToolSpec)
    """
    # Load workspace config
    config = Config.from_workspace(workspace=workspace)

    # Set chat config if provided
    if chat_config:
        config.chat = chat_config

    set_config(config)

    # Load .env file if present
    load_dotenv(dotenv_path=workspace / ".env")

    # Initialize tools and hooks
    initialized_tools = init_tools(tools)
    init_hooks()

    return config, initialized_tools
