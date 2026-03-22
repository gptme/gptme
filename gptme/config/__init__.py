"""Configuration system for gptme.

Split into sub-modules for maintainability:
- models: Configuration dataclasses (ProjectConfig, UserConfig, etc.)
- user: User config loading and merging
- project: Project config loading and caching
- chat: Chat session configuration (ChatConfig)
- core: Config aggregation class, context vars, get/set/reload
- cli_setup: CLI argument resolution and config initialization
"""

# Re-export everything for backward compatibility.
# All existing ``from gptme.config import X`` statements continue to work.

from .chat import ChatConfig
from .cli_setup import setup_config_from_cli
from .core import (
    Config,
    _config_var,
    get_config,
    reload_config,
    set_config,
    set_config_from_workspace,
)
from .models import (
    AgentConfig,
    ContextConfig,
    ContextSelectorConfig,
    LessonsConfig,
    MCPConfig,
    MCPServerConfig,
    PluginsConfig,
    ProjectConfig,
    ProviderConfig,
    RagConfig,
    UserConfig,
    UserIdentityConfig,
    UserPromptConfig,
)
from .project import get_project_config
from .user import (
    config_path,
    default_config,
    load_user_config,
    set_config_value,
)

__all__ = [
    # Models
    "AgentConfig",
    "ChatConfig",
    "Config",
    "ContextConfig",
    "ContextSelectorConfig",
    "LessonsConfig",
    "MCPConfig",
    "MCPServerConfig",
    "PluginsConfig",
    "ProjectConfig",
    "ProviderConfig",
    "RagConfig",
    "UserConfig",
    "UserIdentityConfig",
    "UserPromptConfig",
    # Core functions
    "_config_var",
    "get_config",
    "set_config",
    "set_config_from_workspace",
    "reload_config",
    # Loading functions
    "load_user_config",
    "get_project_config",
    "setup_config_from_cli",
    "set_config_value",
    # Constants
    "config_path",
    "default_config",
]


if __name__ == "__main__":
    config = get_config()
    print(config)
