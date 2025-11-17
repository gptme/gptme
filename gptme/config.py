import logging
import os
from contextvars import ContextVar
from dataclasses import (
    asdict,
    dataclass,
    field,
    replace,
)
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, cast

import tomlkit
from tomlkit import TOMLDocument
from tomlkit.container import Container
from tomlkit.exceptions import TOMLKitError
from typing_extensions import Self

from .util import console, path_with_tilde

if TYPE_CHECKING:
    from .tools.base import ToolFormat

logger = logging.getLogger(__name__)


@dataclass
class PluginsConfig:
    """Configuration for the plugin system."""

    # Plugin search paths (relative to config directory or absolute)
    paths: list[str] = field(default_factory=list)

    # Optional: plugin allowlist (empty = all discovered plugins enabled)
    enabled: list[str] = field(default_factory=list)


@dataclass
class MCPServerConfig:
    """Configuration for a MCP server."""

    name: str
    enabled: bool = True
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    url: str = ""
    headers: dict = field(default_factory=dict)

    @property
    def is_http(self) -> bool:
        """Check if this is an HTTP MCP server."""
        return bool(self.url and self.url.startswith(("http://", "https://")))


@dataclass
class ProviderConfig:
    """Configuration for a custom OpenAI-compatible provider."""

    name: str
    base_url: str
    api_key: str | None = None
    api_key_env: str | None = None
    default_model: str | None = None

    def get_api_key(self, config: "Config") -> str:
        """Get the API key from direct value or environment variable."""
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return config.get_env_required(self.api_key_env)
        # Default to provider name in uppercase
        return config.get_env(f"{self.name.upper()}_API_KEY") or "default-key"


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
    # Additional files to include in context. Supports:
    # - Absolute paths
    # - ~ expansion
    # - Relative paths (resolved against the config directory, e.g. ~/.config/gptme)
    files: list[str] = field(default_factory=list)


@dataclass
class UserConfig:
    """User-level configuration, such as user-specific prompts and environment variables."""

    prompt: UserPromptConfig = field(default_factory=UserPromptConfig)

    env: dict[str, str] = field(default_factory=dict)
    mcp: MCPConfig | None = None
    providers: list[ProviderConfig] = field(default_factory=list)


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
class AgentConfig:
    """Configuration for agent-specific settings."""

    name: str


@dataclass
class LessonsConfig:
    """Configuration for the lessons system."""

    dirs: list[str] = field(default_factory=list)


@dataclass
class ProjectConfig:
    """Project-level configuration, such as which files to include in the context by default.

    This is loaded from a gptme.toml :ref:`project-config` file in the project directory or .github directory.
    """

    _workspace: Path | None = None

    base_prompt: str | None = None
    prompt: str | None = None
    files: list[str] | None = None
    context_cmd: str | None = None
    rag: RagConfig = field(default_factory=RagConfig)
    agent: AgentConfig | None = None
    lessons: LessonsConfig = field(default_factory=LessonsConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)

    env: dict[str, str] = field(default_factory=dict)
    mcp: MCPConfig | None = None

    @classmethod
    def from_dict(cls, config_data: dict, workspace: Path | None = None) -> Self:
        """Create a ProjectConfig instance from a dictionary. Warns about unknown keys."""
        prompt = config_data.pop("prompt", None)
        files = config_data.pop("files", None)
        context_cmd = config_data.pop("context_cmd", None)
        rag = RagConfig(**config_data.pop("rag", {}))
        agent = (
            AgentConfig(**config_data.pop("agent")) if "agent" in config_data else None
        )
        lessons = LessonsConfig(dirs=config_data.pop("lessons", {}).get("dirs", []))
        plugins_data = config_data.pop("plugins", {})
        plugins = PluginsConfig(
            paths=plugins_data.get("paths", []),
            enabled=plugins_data.get("enabled", []),
        )
        env = config_data.pop("env", {})
        if mcp := config_data.pop("mcp", None):
            mcp = MCPConfig.from_dict(mcp)

        # Check for unknown keys
        if config_data:
            logger.warning(f"Unknown keys in project config: {config_data.keys()}")

        return cls(
            _workspace=workspace,
            prompt=prompt,
            files=files,
            context_cmd=context_cmd,
            rag=rag,
            agent=agent,
            lessons=lessons,
            plugins=plugins,
            env=env,
            mcp=mcp,
            **config_data,
        )

    def merge(self, other: Self) -> Self:
        """Merge another ProjectConfig into this one."""
        return replace(self, **{k: v for k, v in other.to_dict().items()})

    def to_dict(self) -> dict:
        """Convert ProjectConfig to a dictionary. Returns a dict with non-'mcp' and non-'env' keys nested under a 'project' key, and 'env' and 'mcp' as top-level keys."""

        # Custom function to handle Path objects during serialization
        def _dict_factory(items):
            result = {}
            for key, value in items:
                if isinstance(value, Path):
                    result[key] = str(path_with_tilde(value))
                else:
                    result[key] = value
            return result

        # Convert to dict and remove None values (including in nested dicts), using custom dict factory to handle Path objects
        def remove_none_values(d):
            if not isinstance(d, dict):
                return d
            return {k: remove_none_values(v) for k, v in d.items() if v is not None}

        config_dict = remove_none_values(
            {
                k: v
                for k, v in asdict(self, dict_factory=_dict_factory).items()
                if not k.startswith("_")
            }
        )

        return config_dict


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

    # Parse custom providers
    providers_config = config.pop("providers", [])
    providers = [ProviderConfig(**provider) for provider in providers_config]

    if config:
        logger.warning(f"Unknown keys in config: {config.keys()}")

    return UserConfig(prompt=prompt, env=env, mcp=mcp, providers=providers)


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


def _merge_config_data(main_config: dict, local_config: dict) -> dict:
    """
    Merge local configuration into main configuration.

    For MCP servers, merge by name - local server env vars are merged into main server config.
    For other sections, local config extends/overrides main config.
    """
    import copy

    merged = copy.deepcopy(main_config)

    for key, value in local_config.items():
        if key == "mcp" and isinstance(value, dict) and "servers" in value:
            # Special handling for MCP servers - merge by name
            if "mcp" not in merged:
                merged["mcp"] = {}
            if "servers" not in merged["mcp"]:
                merged["mcp"]["servers"] = []

            local_servers = value.get("servers", [])
            main_servers = merged["mcp"]["servers"]

            # Create a dict for quick lookup of main servers by name
            main_servers_by_name = {server["name"]: server for server in main_servers}

            for local_server in local_servers:
                server_name = local_server["name"]
                if server_name in main_servers_by_name:
                    # Merge env vars from local into main server
                    main_server = main_servers_by_name[server_name]
                    if "env" not in main_server:
                        main_server["env"] = {}
                    if "env" in local_server:
                        main_server["env"].update(local_server["env"])

                    # Merge other server properties (command, args, enabled)
                    for server_key, server_value in local_server.items():
                        if server_key not in ["name", "env"]:
                            main_server[server_key] = server_value
                else:
                    # Add new server from local config
                    main_servers.append(local_server)

            # Merge other MCP config properties (enabled, auto_start)
            for mcp_key, mcp_value in value.items():
                if mcp_key != "servers":
                    merged["mcp"][mcp_key] = mcp_value

        elif (
            isinstance(value, dict) and key in merged and isinstance(merged[key], dict)
        ):
            # Recursive merge for nested dictionaries
            merged[key] = _merge_config_data(merged[key], value)
        else:
            # Direct override for other keys
            merged[key] = value

    return merged


@lru_cache(maxsize=4)
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

        # Look for local config file in the same directory
        local_config_path = project_config_path.parent / "gptme.local.toml"
        if local_config_path.exists():
            console.log(
                f"Loading local configuration from {path_with_tilde(local_config_path)}"
            )
            with open(local_config_path) as f:
                local_config_data = tomlkit.load(f).unwrap()

            # Merge local config into main config
            config_data = _merge_config_data(config_data, local_config_data)

        return ProjectConfig.from_dict(config_data, workspace=workspace)
    return None


@dataclass
class ChatConfig:
    """Configuration for a chat session."""

    _logdir: Path | None = None

    # these are under a [chat] namespace in the toml
    name: str | None = None
    model: str | None = None
    tools: list[str] | None = None
    tool_format: "ToolFormat | None" = None
    stream: bool = True
    interactive: bool = True
    workspace: Path = field(
        default_factory=Path.cwd
    )  # TODO: Is default value cwd ok for server?
    agent: Path | None = None

    env: dict = field(default_factory=dict)
    mcp: MCPConfig | None = None

    @property
    def agent_config(self) -> AgentConfig | None:
        """Get the agent configuration if available."""
        if not self.agent:
            return None
        agent_project_config = get_project_config(self.agent)
        if agent_project_config and agent_project_config.agent:
            return agent_project_config.agent
        return None

    @classmethod
    def from_dict(cls, config_data: dict) -> Self:
        """Create a ChatConfig instance from a dictionary. Warns about unknown keys."""
        _logdir = config_data.pop("_logdir", None)

        # Extract chat settings
        chat_data = config_data.pop("chat", {})

        # Convert workspace to Path if present and resolve to absolute path
        if "workspace" in chat_data:
            workspace_value = chat_data["workspace"]
            # Handle magic "@log" value like CLI does
            if workspace_value == "@log":
                if not _logdir:
                    raise ValueError("Cannot use '@log' workspace without logdir")
                chat_data["workspace"] = (_logdir / "workspace").resolve()
                # Ensure the workspace directory exists
                chat_data["workspace"].mkdir(parents=True, exist_ok=True)
            else:
                chat_data["workspace"] = Path(workspace_value).expanduser().resolve()
        # For old-style config, check if workspace is in the logdir
        elif _logdir and (_logdir / "workspace").exists():
            chat_data["workspace"] = (_logdir / "workspace").resolve()

        # Extract agent
        agent_path = chat_data.pop("agent", None)
        agent = Path(agent_path).expanduser().resolve() if agent_path else None

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
            agent=agent,
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
            logger.debug(
                f"No existing config found at {path}, using default config for new conversation."
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

        # Only create symlink if workspace is different from the log workspace
        if self.workspace != workspace_path:
            if workspace_path.exists():
                if workspace_path.is_dir() and not workspace_path.is_symlink():
                    # It's a directory with potential user content, don't delete it
                    raise ValueError(
                        f"Workspace directory '{workspace_path}' already exists and contains data. "
                        "Cannot change workspace when directory is in use. "
                        "Please move or rename the existing directory first."
                    )
                else:
                    # It's a file or symlink, safe to remove
                    workspace_path.unlink()
            workspace_path.symlink_to(self.workspace)
        # If workspace IS the log workspace, no symlink needed - directory already exists

        return self

    def to_dict(self) -> dict:
        """Convert ChatConfig to a dictionary. Returns a dict with non-'mcp' and non-'env' keys nested under a 'chat' key, and 'env' and 'mcp' as top-level keys."""

        # Custom function to handle Path objects during serialization
        def _dict_factory(items):
            result = {}
            for key, value in items:
                if isinstance(value, Path):
                    result[key] = str(path_with_tilde(value))
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

        # Apply CLI overrides for explicitly provided values
        for field_name in cli_config.__dataclass_fields__:
            if field_name.startswith("_"):
                continue
            cli_value = getattr(cli_config, field_name)
            default_value = getattr(defaults, field_name)

            # For optional fields that default to None, check if explicitly provided
            if (
                field_name in ["model", "tool_format", "tools", "agent"]
                and cli_value is not None
            ):
                logger.debug(f"Overriding {field_name} with CLI value: {cli_value}")
                config = replace(config, **{field_name: cli_value})
            # For other fields, use the original logic (differs from defaults)
            elif (
                field_name not in ["model", "tool_format", "tools", "agent"]
                and cli_value != default_value
            ):
                logger.debug(f"Overriding {field_name} with CLI value: {cli_value}")
                config = replace(config, **{field_name: cli_value})

        # Auto-detect agent if not explicitly set
        if config.agent is None:
            project_config = get_project_config(config.workspace)
            if project_config and project_config.agent:
                config = replace(config, agent=config.workspace)
                logger.debug(f"Auto-detected agent workspace: {config.workspace}")

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


# Define the path to the config file
config_path = os.path.expanduser("~/.config/gptme/config.toml")


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
    from gptme.tools import clear_tools

    clear_tools()

    assert config
    return config


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
    from .tools import get_toolchain

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
        else:
            # Normal mode - CLI override replaces defaults
            resolved_tool_allowlist = [
                tool.strip() for tool in tool_allowlist.split(",")
            ]
    elif existing_chat_config and existing_chat_config.tools:
        # When resuming, use saved conversation tools unless CLI override provided
        resolved_tool_allowlist = existing_chat_config.tools
    elif tools_env := config.get_env("TOOLS"):
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

    # Handle tool_format with similar precedence
    if tool_format is not None:
        # CLI override always takes precedence
        resolved_tool_format = tool_format
    elif existing_chat_config and existing_chat_config.tool_format:
        # When resuming, use saved conversation tool_format unless CLI override provided
        resolved_tool_format = existing_chat_config.tool_format
    else:
        # Fall back to env/config for new conversations or when no saved tool_format
        resolved_tool_format = (
            cast("ToolFormat", config.get_env("TOOL_FORMAT")) or "markdown"
        )

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


if __name__ == "__main__":
    config = get_config()
    print(config)
