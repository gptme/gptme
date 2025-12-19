"""
Shared execution infrastructure for gptme.

This module provides common chat execution patterns used by both
the ACP agent and server API, reducing code duplication and ensuring
consistent behavior across execution contexts.

The executor handles:
- Configuration and environment setup
- Tool and hook initialization
- Chat step execution with proper context
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

from .chat import step as chat_step
from .config import Config, set_config
from .hooks import HookType, trigger_hook
from .init import init_hooks, init_tools
from .logmanager import LogManager
from .message import Message
from .tools import ToolFormat

if TYPE_CHECKING:
    from .config import ChatConfig
    from .tools import ToolSpec

logger = logging.getLogger(__name__)

# Type alias for confirm callback
ConfirmFunc = Callable[[str], bool]


@dataclass
class ExecutionContext:
    """
    Configuration for chat execution.

    Encapsulates all settings needed to run a chat step, making it easy
    to pass consistent configuration between different execution contexts.

    Attributes:
        workspace: Working directory for the execution
        model: Model identifier to use (e.g., "anthropic/claude-3-5-sonnet")
        stream: Whether to stream responses
        tool_format: Format for tool use ("markdown" or "tool")
        confirm: Callback for tool confirmation
        auto_confirm: Whether to auto-confirm all tools
        tools: Optional list of tool names for context/reference (tools are
            initialized separately via prepare_execution_environment())
    """

    workspace: Path
    model: str | None = None
    stream: bool = True
    tool_format: ToolFormat = "markdown"
    confirm: ConfirmFunc | None = None
    auto_confirm: bool = False
    tools: list[str] | None = None


def prepare_execution_environment(
    workspace: Path,
    tools: list[str] | None = None,
    chat_config: ChatConfig | None = None,
) -> tuple[Config, list[ToolSpec]]:
    """
    Prepare the execution environment with config, tools, and hooks.

    This is common setup needed by ACP, Server, evals, and subagents.
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


def create_confirm_callback(
    auto_confirm: bool = False,
    confirm_fn: ConfirmFunc | None = None,
) -> ConfirmFunc:
    """
    Create a confirmation callback for tool execution.

    Args:
        auto_confirm: If True, auto-confirm all tools
        confirm_fn: Custom confirmation function

    Returns:
        A confirm callback function

    Note:
        If neither auto_confirm nor confirm_fn is provided, the default
        behavior is to reject all tool executions. This is a deliberate
        safety measure for autonomous execution contexts where unchecked
        tool execution could be dangerous.
    """
    if auto_confirm:
        return lambda _: True
    if confirm_fn:
        return confirm_fn
    # Default: reject all (safe for autonomous execution)
    return lambda _: False


def execute_chat_step(
    log: LogManager,
    context: ExecutionContext,
) -> Generator[Message, None, None]:
    """
    Execute a single chat step with the given context.

    This is the core execution function that both ACP and Server can use.
    It wraps the chat.step() function with proper configuration and
    error handling.

    Args:
        log: The LogManager containing conversation history
        context: Execution configuration

    Yields:
        Response messages from the chat step
    """
    # Set up model
    if context.model:
        from .llm.models import set_default_model

        set_default_model(context.model)

    # Create confirm callback
    confirm = create_confirm_callback(
        auto_confirm=context.auto_confirm,
        confirm_fn=context.confirm,
    )

    # Run chat step
    yield from chat_step(
        log=log.log,
        stream=context.stream,
        confirm=confirm,
        tool_format=context.tool_format,
        workspace=context.workspace,
        model=context.model,
    )


def run_with_hooks(
    log: LogManager,
    context: ExecutionContext,
    on_message: Callable[[Message], None] | None = None,
) -> list[Message]:
    """
    Run chat step with pre/post hooks and message notifications.

    Higher-level function that handles the complete execution flow
    including hooks. Useful for simpler integrations.

    Args:
        log: The LogManager containing conversation history
        context: Execution configuration
        on_message: Optional callback for each message

    Returns:
        List of all response messages
    """
    messages: list[Message] = []

    # Trigger pre-process hook
    if pre_msgs := trigger_hook(
        HookType.MESSAGE_PRE_PROCESS,
        manager=log,
    ):
        for msg in pre_msgs:
            log.append(msg)
            messages.append(msg)
            if on_message:
                on_message(msg)

    # Run chat step
    for msg in execute_chat_step(log, context):
        log.append(msg)
        messages.append(msg)
        if on_message:
            on_message(msg)

    # Trigger post-process hook
    if post_msgs := trigger_hook(
        HookType.MESSAGE_POST_PROCESS,
        manager=log,
    ):
        for msg in post_msgs:
            log.append(msg)
            messages.append(msg)
            if on_message:
                on_message(msg)

    return messages
