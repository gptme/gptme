"""Core configuration: Config class, context variables, and accessors.

The Config class aggregates user, project, and chat configurations.
Context variables provide thread-safe configuration storage.
"""

import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from typing_extensions import Self

from .chat import ChatConfig
from .models import MCPConfig, MCPServerConfig, ProjectConfig, UserConfig
from .project import (
    _config_logged_workspaces,
    _get_project_config_cached,
    get_project_config,
)
from .user import load_user_config

logger = logging.getLogger(__name__)


@dataclass()
class Config:
    """
    A complete configuration object, including user and project configurations.

    It is meant to be used to resolve configuration values, not to be passed around everywhere.
    Care must be taken to avoid this becoming a "god object" passed around loosely, or frequently used as a global.
    """

    user: UserConfig = field(default_factory=load_user_config)
    project: ProjectConfig | None = None
    chat: ChatConfig | None = None

    @classmethod
    def from_workspace(cls, workspace: Path) -> Self:
        """Load the configuration from a workspace directory. Clearing any cache."""
        _get_project_config_cached.cache_clear()
        _config_logged_workspaces.clear()
        return cls(
            user=load_user_config(),
            project=get_project_config(workspace),
        )

    @classmethod
    def from_logdir(cls, logdir: Path) -> Self:
        """Load the configuration from a log directory."""
        chat_config = ChatConfig.from_logdir(logdir)
        return cls(
            user=load_user_config(),
            project=get_project_config(chat_config.workspace),
            chat=chat_config,
        )

    @property
    def mcp(self) -> MCPConfig:
        """Get the MCP configuration, merging user and project configurations."""
        # Override MCP config from project config and chat config if present, merging mcp servers
        servers: list[MCPServerConfig] = []

        enabled = False
        auto_start = False

        # merge mcp servers
        if self.chat and self.chat.mcp:
            for server in self.chat.mcp.servers:
                if server.name not in [s.name for s in servers]:
                    servers.append(server)

        if self.project and self.project.mcp:
            for server in self.project.mcp.servers:
                if server.name not in [s.name for s in servers]:
                    servers.append(server)

        if self.user and self.user.mcp:
            for server in self.user.mcp.servers:
                if server.name not in [s.name for s in servers]:
                    servers.append(server)

        # merge mcp config
        if self.user and self.user.mcp:
            enabled = self.user.mcp.enabled
            auto_start = self.user.mcp.auto_start

        if self.project and self.project.mcp:
            enabled = self.project.mcp.enabled
            auto_start = self.project.mcp.auto_start

        if self.chat and self.chat.mcp:
            enabled = self.chat.mcp.enabled
            auto_start = self.chat.mcp.auto_start

        mcp = MCPConfig(
            enabled=enabled,
            auto_start=auto_start,
            servers=servers,
        )

        return mcp

    def get_env(self, key: str, default: str | None = None) -> str | None:
        """Gets an environment variable, checks the config file if it's not set in the environment.

        Checks both ``GPTME_<KEY>`` and ``<KEY>`` forms for environment variables,
        with the prefixed form taking precedence. Config file lookups always use
        the bare (unprefixed) key.
        """
        prefixed = f"GPTME_{key}" if not key.startswith("GPTME_") else key
        bare = key.removeprefix("GPTME_") if key.startswith("GPTME_") else key
        return (
            os.environ.get(prefixed)
            or os.environ.get(bare)
            or (self.chat and self.chat.env.get(bare))
            or (self.project and self.project.env.get(bare))
            or self.user.env.get(bare)
            or default
        )

    def get_env_bool(self, key: str, default: bool | None = None) -> bool | None:
        if env_value := self.get_env(key):
            return env_value.lower() in ("1", "true", "yes", "on")
        return default

    def get_env_required(self, key: str) -> str:
        """Gets an environment variable, checks the config file if it's not set in the environment."""
        if (
            val := os.environ.get(key)
            or (self.chat and self.chat.env.get(key))
            or (self.project and self.project.env.get(key))
            or self.user.env.get(key)
        ):
            return val
        raise KeyError(  # pragma: no cover
            f"Environment variable {key} not set in env or config, see README."
        )


# Context-local storage for config
# Each context (thread/async task) gets its own independent copy of the configuration
_config_var: ContextVar[Config | None] = ContextVar("config", default=None)

# Note: Configuration must be initialized in each context that needs it.
# The first call to get_config() in a context will create a new Config instance.
# Subsequent calls in the same context will return the same instance.


def get_config() -> Config:
    """Get the current configuration."""
    config = _config_var.get()
    if config is None:
        config = Config()
        _config_var.set(config)
    return config


def set_config(config: Config):
    """Set the configuration."""
    _config_var.set(config)


def set_config_from_workspace(workspace: Path):
    """Set the configuration to use a specific workspace, possibly having a project config."""
    _config_var.set(Config.from_workspace(workspace=workspace))


def reload_config() -> Config:
    """Reload the configuration files."""
    config = _config_var.get()
    if config is None:
        config = Config()
        _config_var.set(config)
    elif workspace := (config.project and config.project._workspace):
        config = Config.from_workspace(workspace=workspace)
        _config_var.set(config)
    else:
        config = Config()
        _config_var.set(config)

    # Clear tools cache so MCP tools are recreated with new config
    from gptme.tools import clear_tools  # fmt: skip

    clear_tools()

    assert config
    return config
