"""
Commands module for gptme.

This module provides the command system for gptme, including:
- Command registry and decorator
- Built-in commands (session, llm, export, meta)
- Plugin command registration
"""

from pathlib import Path

# IMPORTANT: Import llm first to resolve the config<->llm circular dependency
# The original commands.py did this implicitly via `from . import llm`
# This triggers config to fully load before chat.py tries to import from it
from .. import llm as _llm  # noqa: F401

# Import command modules to register their commands
from . import (
    export,  # noqa: F401
    llm,  # noqa: F401
    meta,  # noqa: F401
    session,  # noqa: F401
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
    get_registered_commands,
    get_user_commands,
    handle_cmd,
    register_command,
    unregister_command,
)

# Re-export _replay_tool and completers from export for backward compatibility
from .export import _complete_replay, _replay_tool

# Re-export completers from llm
from .llm import _complete_model

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
    """Initialize plugin commands."""
    from ..config import get_config
    from ..plugins import register_plugin_commands

    config = get_config()
    if config.project and config.project.plugins and config.project.plugins.paths:
        register_plugin_commands(
            plugin_paths=[Path(p) for p in config.project.plugins.paths],
            enabled_plugins=config.project.plugins.enabled or None,
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
    "get_registered_commands",
    "get_user_commands",
    "handle_cmd",
    "init_commands",
    "register_command",
    "unregister_command",
]
