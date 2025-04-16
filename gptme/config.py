import logging
import os
from dataclasses import asdict, dataclass, field
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
    name: str
    enabled: bool = True
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)


@dataclass
class MCPConfig:
    enabled: bool = False
    auto_start: bool = False
    servers: list[MCPServerConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, doc: dict) -> Self:
        servers = [MCPServerConfig(**server) for server in doc.get("servers", [])]
        return cls(
            enabled=doc.get("enabled", False),
            auto_start=doc.get("auto_start", False),
            servers=servers,
        )


@dataclass
class UserPromptConfig:
    about_user: str | None = None
    response_preference: str | None = None
    project: dict = field(default_factory=dict)


@dataclass
class UserConfig:
    prompt: UserPromptConfig = field(default_factory=UserPromptConfig)

    env: dict = field(default_factory=dict)
    mcp: MCPConfig = field(default_factory=MCPConfig)


@dataclass
class RagConfig:
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

    This is loaded from a gptme.toml file in the project directory or .github directory."""

    _workspace: Path | None = None

    base_prompt: str | None = None
    prompt: str | None = None
    files: list[str] = field(default_factory=list)
    rag: RagConfig = field(default_factory=RagConfig)

    env: dict = field(default_factory=dict)
    mcp: MCPConfig | None = None


ABOUT_ACTIVITYWATCH = """ActivityWatch is a free and open-source automated time-tracker that helps you track how you spend your time on your devices."""
ABOUT_GPTME = "gptme is a CLI to interact with large language models in a Chat-style interface, enabling the assistant to execute commands and code on the local machine, letting them assist in all kinds of development and terminal-based work."


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

    # TODO: these lack a header in toml, we should maybe namespace them [chat]
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
        _logdir = config_data.pop("_logdir", None)

        model = config_data.pop("model", None)
        tools = config_data.pop("tools", None)
        tool_format = config_data.pop("tool_format", None)
        stream = config_data.pop("stream", True)
        interactive = config_data.pop("interactive", True)

        env = config_data.pop("env", {})
        mcp = MCPConfig.from_dict(config_data.pop("mcp", {}))

        # Check for unknown keys
        if config_data:
            logger.warning(f"Unknown keys in chat config: {config_data.keys()}")

        return cls(
            _logdir=_logdir,
            model=model,
            tools=tools,
            tool_format=tool_format,
            stream=stream,
            interactive=interactive,
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
        for field_name in cli_config.__dataclass_fields__:
            if field_name.startswith("_"):
                continue
            cli_value = getattr(cli_config, field_name)
            default_value = getattr(defaults, field_name)
            # TODO: note that this isn't a great check: CLI values equal to defaults won't override existing config values
            if cli_value != default_value:
                # logger.info(f"Overriding {field_name} with CLI value: {cli_value}")
                setattr(config, field_name, cli_value)

        # Save the config
        config.save()

        return config


@dataclass
class Config:
    user: UserConfig = field(default_factory=load_user_config)
    project: ProjectConfig | None = None

    @classmethod
    def from_workspace(cls, workspace: Path):
        get_project_config.cache_clear()

        config = cls()
        config.project = get_project_config(workspace)
        config.user = load_user_config()

        return config

    @property
    def mcp(self) -> MCPConfig:
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

# Global variable to store the config
_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        return Config()
    return _config


def set_config(workspace: Path):
    global _config
    _config = Config.from_workspace(workspace=workspace)


def reload_config():
    global _config
    if workspace := (_config and _config.project and _config.project._workspace):
        set_config(workspace)
    else:
        _config = Config()


if __name__ == "__main__":
    config = get_config()
    print(config)
