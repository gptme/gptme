import logging
import os
from contextvars import ContextVar
from dataclasses import (
    asdict,
    dataclass,
    field,
    fields,
    replace,
)
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
from tomlkit import TOMLDocument
from tomlkit.container import Container
from typing_extensions import Self

from .util import console, path_with_tilde


if TYPE_CHECKING:
    from .tools import ToolFormat

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """Configuration for a MCP server."""

    name: str
    enabled: bool = True
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)


@dataclass
class MCPConfig:
    """Configuration for :ref:`Model Context Protocol <MCP>` support, including which MCP servers to use."""

    enabled: bool = False
    auto_start: bool = False
    servers: list[MCPServerConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, doc: dict) -> Self:
        """Create a MCPConfig instance from a dictionary. Warns about unknown keys."""
        enabled = doc.pop("enabled", False)
        auto_start = doc.pop("auto_start", False)
        servers = [MCPServerConfig(**server) for server in doc.pop("servers", [])]
        if doc:
            logger.warning(f"Unknown keys in MCP config: {doc.keys()}")
        return cls(
            enabled=enabled,
            auto_start=auto_start,
            servers=servers,
        )


@dataclass
class UserPromptConfig:
    """User-level configuration for user-specific prompts and project descriptions."""

    about_user: str | None = None
    response_preference: str | None = None
    project: dict[str, str] = field(default_factory=dict)


@dataclass
class UserConfig:
    """User-level configuration, such as user-specific prompts and environment variables."""

    prompt: UserPromptConfig = field(default_factory=UserPromptConfig)

    env: dict[str, str] = field(default_factory=dict)
    mcp: MCPConfig = field(default_factory=MCPConfig)


@dataclass
class RagConfig:
    """Configuration for :ref:`retrieval-augmented generation <RAG>` support."""

    enabled: bool = False
    max_tokens: int | None = None
    min_relevance: float | None = None
    post_process: bool = True
    post_process_model: str | None = None
    post_process_prompt: str | None = None
    workspace_only: bool = True
    paths: list[str] = field(default_factory=list)


@dataclass
class ProjectConfig:
    """Project-level configuration, such as which files to include in the context by default.

    This is loaded from a gptme.toml :ref:`project-config` file in the project directory or .github directory.
    """

    _workspace: Path | None = None

    base_prompt: str | None = None
    prompt: str | None = None
    files: list[str] = field(default_factory=list)
    rag: RagConfig = field(default_factory=RagConfig)

    env: dict[str, str] = field(default_factory=dict)
    mcp: MCPConfig | None = None


ABOUT_ACTIVITYWATCH = """ActivityWatch is a free and open-source automated time-tracker that helps you track how you spend your time on your devices."""
ABOUT_GPTME = "gptme is a CLI to interact with large language models in a Chat-style interface, enabling the assistant to execute commands and code on the local machine, letting them assist in all kinds of development and terminal-based work."


# TODO: include this in docs
default_config = UserConfig(
    prompt=UserPromptConfig(
        about_user="I am a curious human programmer.",
        response_preference="Basic concepts don't need to be explained.",
        project={
            "activitywatch": ABOUT_ACTIVITYWATCH,
            "gptme": ABOUT_GPTME,
        },
    ),
    env={
        # toml doesn't support None
        # "OPENAI_API_KEY": None
    },
)


def load_user_config(path: str | None = None) -> UserConfig:
    """Load the user configuration from the config file."""
    config = _load_config_doc(path).unwrap()
    assert "prompt" in config, "prompt key missing in config"
    assert "env" in config, "env key missing in config"

    prompt = UserPromptConfig(**config.pop("prompt", {}))
    env = config.pop("env", {})
    mcp = MCPConfig.from_dict(config.pop("mcp", {}))

    if config:
        logger.warning(f"Unknown keys in config: {config.keys()}")

    return UserConfig(prompt=prompt, env=env, mcp=mcp)


def _load_config_doc(path: str | None = None) -> tomlkit.TOMLDocument:
    if path is None:
        path = config_path
    # Check if the config file exists
    if not os.path.exists(path):
        # If not, create it and write some default settings
        os.makedirs(os.path.dirname(path), exist_ok=True)
        toml = tomlkit.dumps(asdict(default_config))
        with open(path, "w") as config_file:
            config_file.write(toml)
        console.log(f"Created config file at {path}")
        doc = tomlkit.loads(toml)
        return doc
    else:
        with open(path) as config_file:
            doc = tomlkit.load(config_file)
        return doc


def set_config_value(key: str, value: str) -> None:  # pragma: no cover
    """Set a value in the user config file."""
    doc: TOMLDocument | Container = _load_config_doc()

    # Set the value
    keypath = key.split(".")
    d = doc
    for key in keypath[:-1]:
        d = d.get(key, {})
    d[keypath[-1]] = value

    # Write the config
    with open(config_path, "w") as config_file:
        tomlkit.dump(doc, config_file)

    # Reload config
    reload_config()


@lru_cache(maxsize=1)
def get_project_config(workspace: Path | None) -> ProjectConfig | None:
    """
    Get a cached copy of or load the project configuration from a gptme.toml file in the workspace or .github directory.

    Run :func:`reload_config` or :func:`Config.from_workspace` to reset cache and reload the project config.
    """
    if workspace is None:
        return None
    project_config_paths = [
        p
        for p in (
            workspace / "gptme.toml",
            workspace / ".github" / "gptme.toml",
        )
        if p.exists()
    ]
    if project_config_paths:
        project_config_path = project_config_paths[0]
        console.log(
            f"Using project configuration at {path_with_tilde(project_config_path)}"
        )
        # load project config
        with open(project_config_path) as f:
            config_data = tomlkit.load(f).unwrap()

        prompt = config_data.pop("prompt", "")
        files = config_data.pop("files", [])
        rag = RagConfig(**config_data.pop("rag", {}))
        mcp = MCPConfig.from_dict(config_data.pop("mcp", {}))

        # Check for unknown keys
        if config_data:
            logger.warning(f"Unknown keys in project config: {config_data.keys()}")

        return ProjectConfig(
            _workspace=workspace,
            prompt=prompt,
            files=files,
            rag=rag,
            mcp=mcp,
            **config_data,
        )
    return None


@dataclass
class ChatConfig:
    """Configuration for a chat session."""

    _logdir: Path | None = None

    # these are under a [chat] namespace in the toml
    model: str | None = None
    tools: list[str] | None = None
    tool_format: "ToolFormat | None" = None
    stream: bool = True
    interactive: bool = True

    # TODO: support env in chat config
    env: dict = field(default_factory=dict)
    # TODO: support mcp in chat config
    mcp: MCPConfig = field(default_factory=MCPConfig)

    @classmethod
    def from_dict(cls, config_data: dict) -> Self:
        """Create a ChatConfig instance from a dictionary. Warns about unknown keys."""
        _logdir = config_data.pop("_logdir", None)

        # Extract chat settings
        chat_data = config_data.pop("chat", {})

        env = config_data.pop("env", {})
        mcp = MCPConfig.from_dict(config_data.pop("mcp", {}))

        # Check for unknown keys
        if config_data:
            logger.warning(f"Unknown keys in chat config: {config_data.keys()}")

        return cls(
            _logdir=_logdir,
            **chat_data,
            env=env,
            mcp=mcp,
        )

    @classmethod
    def from_logdir(cls, path: Path) -> Self:
        """Load ChatConfig from a log directory."""
        chat_config_path = path / "config.toml"
        if not chat_config_path.exists():
            return cls(_logdir=path)
        try:
            with open(chat_config_path) as f:
                config_data = tomlkit.load(f).unwrap()
            config_data["_logdir"] = path
            return cls.from_dict(config_data)
        except (OSError, tomlkit.exceptions.TOMLKitError) as e:
            logger.warning(f"Failed to load chat config from {chat_config_path}: {e}")
            return cls()

    def save(self) -> None:
        """Save the chat config to the log directory."""
        if not self._logdir:
            raise ValueError("ChatConfig has no logdir set")
        self._logdir.mkdir(parents=True, exist_ok=True)
        chat_config_path = self._logdir / "config.toml"

        # Convert to dict and remove None values
        config_dict = {
            k: v
            for k, v in asdict(self).items()
            if v is not None and not k.startswith("_")
        }

        # Save chat-specific settings into [chat] table
        for k in list(config_dict):
            if k not in ["mcp", "env"]:
                if "chat" not in config_dict:
                    config_dict["chat"] = {}
                config_dict["chat"][k] = config_dict.pop(k)

        # sort in chat -> env -> mcp order
        config_dict = {
            "chat": config_dict.pop("chat", {}),
            "env": config_dict.pop("env", {}),
            "mcp": config_dict.pop("mcp", {}),
        }

        # TODO: load and update this properly as TOMLDocument to preserve formatting
        with open(chat_config_path, "w") as f:
            tomlkit.dump(config_dict, f)

    @classmethod
    def load_or_create(cls, logdir: Path, cli_config: Self) -> Self:
        """Load or create a chat config, applying CLI overrides."""
        # Load existing config if it exists
        config = cls.from_logdir(logdir)
        defaults = cls()

        # Apply CLI overrides (only if they differ from defaults)
        for _field in fields(cli_config):
            if _field.name.startswith("_"):
                continue
            cli_value = getattr(cli_config, _field.name)
            default_value = getattr(defaults, _field.name)
            # TODO: note that this isn't a great check: CLI values equal to defaults won't override existing config values
            if cli_value != default_value:
                # logger.info(f"Overriding {field_name} with CLI value: {cli_value}")
                config = replace(config, **{_field.name: cli_value})

        # Save the config
        config.save()

        return config


@dataclass(frozen=True)
class Config:
    """
    A complete configuration object, including user and project configurations.

    It is meant to be used to resolve configuration values, not to be passed around everywhere.
    Care must be taken to avoid this becoming a "god object" passed around loosely, or frequently used as a global.
    """

    user: UserConfig = field(default_factory=load_user_config)
    project: ProjectConfig | None = None

    @classmethod
    def from_workspace(cls, workspace: Path) -> Self:
        """Load the configuration from a workspace directory. Clearing any cache."""
        get_project_config.cache_clear()
        return cls(
            user=load_user_config(),
            project=get_project_config(workspace),
        )

    @property
    def mcp(self) -> MCPConfig:
        """Get the MCP configuration, merging user and project configurations."""
        mcp = self.user.mcp

        # Override MCP config from project config if present, merging mcp servers
        if self.project and self.project.mcp:
            servers = []
            for server in self.project.mcp.servers:
                servers.append(server)
            for server in self.user.mcp.servers:
                if server.name not in [s.name for s in servers]:
                    servers.append(server)

            mcp = MCPConfig(
                enabled=self.project.mcp.enabled,
                auto_start=self.project.mcp.auto_start,
                servers=servers,
            )

        return mcp

    def get_env(self, key: str, default: str | None = None) -> str | None:
        """Gets an environment variable, checks the config file if it's not set in the environment."""
        return (
            os.environ.get(key)
            or (self.project and self.project.env.get(key))
            or self.user.env.get(key)
            or default
        )

    def get_env_required(self, key: str) -> str:
        """Gets an environment variable, checks the config file if it's not set in the environment."""
        if (
            val := os.environ.get(key)
            or (self.project and self.project.env.get(key))
            or self.user.env.get(key)
        ):
            return val
        raise KeyError(  # pragma: no cover
            f"Environment variable {key} not set in env or config, see README."
        )


# Define the path to the config file
config_path = os.path.expanduser("~/.config/gptme/config.toml")

# Context-local storage for config
# Each context gets its own independent copy of the configuration
_config_var: ContextVar[Config | None] = ContextVar("config", default=None)


def get_config() -> Config:
    """Get the current configuration."""
    config = _config_var.get()
    if config is None:
        config = Config()
        _config_var.set(config)
    return config


def set_config(workspace: Path):
    """Set the configuration to use a specific workspace, possibly having a project config."""
    _config_var.set(Config.from_workspace(workspace=workspace))


def reload_config() -> Config:
    """Reload the configuration files."""
    config = _config_var.get()
    if config is None:
        config = Config()
    elif workspace := (config.project and config.project._workspace):
        config = Config.from_workspace(workspace=workspace)
    else:
        config = Config()
    _config_var.set(config)
    return config


if __name__ == "__main__":
    config = get_config()
    print(config)
