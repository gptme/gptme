"""
Commands module for gptme.

This module provides the command system for gptme, including:
- Command registry and decorator
- Built-in commands (session, llm, export, meta)
- Plugin command registration
"""

import logging

logger = logging.getLogger(__name__)

# IMPORTANT: Import llm first to resolve the config<->llm circular dependency
# The original commands.py did this implicitly via `from . import llm`
# This triggers config to fully load before chat.py tries to import from it
from .. import llm as _llm  # noqa: F401

# Import command modules to register their commands
from . import (  # noqa: F401
    export,
    llm,
    meta,
    session,
)

# Re-export core types and functions from base
from .base import (
    CommandCompleter,
    CommandContext,
    CommandHandler,
    OriginalCommandHandler,
    _command_completers,
    _command_registry,
    command,
    execute_cmd,
    get_command_completer,
    get_commands_with_descriptions,
    get_registered_commands,
    get_user_commands,
    handle_cmd,
    register_command,
    unregister_command,
)

# Re-export _replay_tool and completers from export for backward compatibility
from .export import _complete_replay, _replay_tool

# Re-export completers from llm
from .llm import _complete_model, _complete_tools

# Re-export from meta for backward compatibility
from .meta import (
    COMMANDS,
    Actions,
    _complete_plugin,
    _gen_help,
    action_descriptions,
)

# Re-export completers from session
from .session import (
    _complete_delete,
    _complete_fork,
    _complete_log,
    _complete_rename,
)


def init_commands() -> None:
    """Initialize plugin commands via the unified registry."""
    from ..plugins.registry import get_all_plugins

    for plugin in get_all_plugins():
        if plugin.register_commands:
            try:
                plugin.register_commands()
                logger.debug("Registered commands from plugin: %s", plugin.name)
            except Exception as e:
                logger.warning(
                    "Failed to register commands for plugin %r: %s", plugin.name, e
                )


__all__ = [
    # Types
    "Actions",
    "CommandCompleter",
    "CommandContext",
    "CommandHandler",
    "OriginalCommandHandler",
    # Registry
    "_command_completers",
    "_command_registry",
    # Completers
    "_complete_delete",
    "_complete_fork",
    "_complete_log",
    "_complete_model",
    "_complete_tools",
    "_complete_plugin",
    "_complete_rename",
    "_complete_replay",
    # Functions
    "_gen_help",
    "_replay_tool",
    "action_descriptions",
    "command",
    "COMMANDS",
    "execute_cmd",
    "get_command_completer",
    "get_commands_with_descriptions",
    "get_registered_commands",
    "get_user_commands",
    "handle_cmd",
    "init_commands",
    "register_command",
    "unregister_command",
]
