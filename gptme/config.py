import logging
import os
import threading
from dataclasses import (
    asdict,
    dataclass,
    field,
    replace,
)
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
from tomlkit import TOMLDocument
from tomlkit.container import Container
from tomlkit.exceptions import TOMLKitError
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
    mcp: MCPConfig | None = None


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
    context_cmd: str | None = None
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
        toml = tomlkit.dumps(
            {k: v for k, v in asdict(default_config).items() if v is not None}
        )
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
        context_cmd = config_data.pop("context_cmd", None)
        rag = RagConfig(**config_data.pop("rag", {}))
        if mcp := config_data.pop("mcp", None):
            mcp = MCPConfig.from_dict(mcp)

        # Check for unknown keys
        if config_data:
            logger.warning(f"Unknown keys in project config: {config_data.keys()}")

        return ProjectConfig(
            _workspace=workspace,
            prompt=prompt,
            files=files,
            context_cmd=context_cmd,
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
    workspace: Path = field(
        default_factory=Path.cwd
    )  # TODO: Is default value cwd ok for server?

    env: dict = field(default_factory=dict)
    mcp: MCPConfig | None = None

    @classmethod
    def from_dict(cls, config_data: dict) -> Self:
        """Create a ChatConfig instance from a dictionary. Warns about unknown keys."""
        _logdir = config_data.pop("_logdir", None)

        # Extract chat settings
        chat_data = config_data.pop("chat", {})

        # Convert workspace to Path if present
        if "workspace" in chat_data:
            chat_data["workspace"] = Path(chat_data["workspace"])
        # For old-style config, check if workspace is in the logdir
        elif _logdir and (_logdir / "workspace").exists():
            chat_data["workspace"] = (_logdir / "workspace").resolve()

        env = config_data.pop("env", {})
        mcp = (
            MCPConfig.from_dict(config_data.pop("mcp", {}))
            if "mcp" in config_data
            else None
        )

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
            if (path / "workspace").exists():
                workspace = (path / "workspace").resolve()
                return cls(_logdir=path, workspace=workspace)
            logger.warning(
                f"Neither chat config nor workspace found at {path}, using default config."
            )
            return cls(_logdir=path)
        try:
            with open(chat_config_path) as f:
                config_data = tomlkit.load(f).unwrap()
            config_data["_logdir"] = path
            return cls.from_dict(config_data)
        except (OSError, TOMLKitError) as e:
            logger.warning(f"Failed to load chat config from {chat_config_path}: {e}")
            return cls()

    def save(self) -> Self:
        """Save the chat config to the log directory."""
        if not self._logdir:
            raise ValueError("ChatConfig has no logdir set")
        self._logdir.mkdir(parents=True, exist_ok=True)
        chat_config_path = self._logdir / "config.toml"

        config_dict = self.to_dict()

        # TODO: load and update this properly as TOMLDocument to preserve formatting
        with open(chat_config_path, "w") as f:
            tomlkit.dump(config_dict, f)

        # Set the workspace symlink in the logdir
        workspace_path = self._logdir / "workspace"
        workspace_path.unlink(missing_ok=True)
        workspace_path.symlink_to(self.workspace)

        return self

    def to_dict(self) -> dict:
        """Convert ChatConfig to a dictionary. Returns a dict with non-'mcp' and non-'env' keys nested under a 'chat' key, and 'env' and 'mcp' as top-level keys."""

        # Custom function to handle Path objects during serialization
        def _dict_factory(items):
            result = {}
            for key, value in items:
                if isinstance(value, Path):
                    result[key] = str(value)
                else:
                    result[key] = value
            return result

        # Convert to dict and remove None values, using custom dict factory to handle Path objects
        config_dict = {
            k: v
            for k, v in asdict(self, dict_factory=_dict_factory).items()
            if v is not None and not k.startswith("_")
        }

        # Save chat-specific settings into [chat] table
        for k in list(config_dict):
            if k not in ["mcp", "env"]:
                if "chat" not in config_dict:
                    config_dict["chat"] = {}
                config_dict["chat"][k] = config_dict.pop(k)

        mcp = config_dict.pop("mcp", None)

        # sort in chat -> env -> mcp order
        config_dict = {
            "chat": config_dict.pop("chat", {}),
            "env": config_dict.pop("env", {}),
        }

        if mcp:
            config_dict["mcp"] = mcp

        return config_dict

    @classmethod
    def load_or_create(cls, logdir: Path, cli_config: Self) -> Self:
        """Load or create a chat config, applying CLI overrides."""
        # Load existing config if it exists
        config = cls.from_logdir(logdir)
        defaults = cls()

        # Apply CLI overrides (only if they differ from defaults)
        for field_name in cli_config.__dataclass_fields__:
            if field_name.startswith("_"):
                continue
            cli_value = getattr(cli_config, field_name)
            default_value = getattr(defaults, field_name)
            # TODO: note that this isn't a great check: CLI values equal to defaults won't override existing config values
            if cli_value != default_value:
                # logger.info(f"Overriding {field_name} with CLI value: {cli_value}")
                config = replace(config, **{field_name: cli_value})

        return config


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
        get_project_config.cache_clear()
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
        """Gets an environment variable, checks the config file if it's not set in the environment."""
        return (
            os.environ.get(key)
            or (self.chat and self.chat.env.get(key))
            or (self.project and self.project.env.get(key))
            or self.user.env.get(key)
            or default
        )

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


# Define the path to the config file
config_path = os.path.expanduser("~/.config/gptme/config.toml")


# Thread-local storage for config
# Each thread gets its own independent copy of the configuration
_thread_local = threading.local()

# Note: Configuration must be initialized in each thread that needs it.
# The first call to get_config() in a thread will create a new Config instance.
# Subsequent calls in the same thread will return the same instance.


def get_config() -> Config:
    """Get the current configuration."""
    if not hasattr(_thread_local, "config"):
        _thread_local.config = Config()
    return _thread_local.config


def set_config(config: Config):
    """Set the configuration."""
    _thread_local.config = config


def set_config_from_workspace(workspace: Path):
    """Set the configuration to use a specific workspace, possibly having a project config."""
    _thread_local.config = Config.from_workspace(workspace=workspace)


def reload_config() -> Config:
    """Reload the configuration files."""
    if not hasattr(_thread_local, "config"):
        _thread_local.config = Config()
    elif workspace := (
        _thread_local.config.project and _thread_local.config.project._workspace
    ):
        _thread_local.config = Config.from_workspace(workspace=workspace)
    else:
        _thread_local.config = Config()
    assert _thread_local.config
    return _thread_local.config


if __name__ == "__main__":
    config = get_config()
    print(config)
