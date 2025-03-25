import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import tomlkit
from tomlkit import TOMLDocument
from tomlkit.container import Container

from .util import console, path_with_tilde

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


@dataclass
class Config:
    prompt: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)
    mcp: MCPConfig = field(
        default_factory=lambda: MCPConfig(enabled=False, auto_start=False, servers=[])
    )

    def get_env(self, key: str, default: str | None = None) -> str | None:
        """Gets an environment variable, checks the config file if it's not set in the environment."""
        return os.environ.get(key) or self.env.get(key) or default

    def get_env_required(self, key: str) -> str:
        """Gets an environment variable, checks the config file if it's not set in the environment."""
        if val := os.environ.get(key) or self.env.get(key):
            return val
        raise KeyError(  # pragma: no cover
            f"Environment variable {key} not set in env or config, see README."
        )

    def dict(self) -> dict:
        return {
            "prompt": self.prompt,
            "env": self.env,
            "mcp": {
                "enabled": self.mcp.enabled,
                "auto_start": self.mcp.auto_start,
                "servers": [
                    {
                        "name": s.name,
                        "enabled": s.enabled,
                        "command": s.command,
                        "args": s.args,
                        "env": s.env,
                    }
                    for s in self.mcp.servers
                ],
            },
        }


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
    """Project-level configuration, such as which files to include in the context by default."""

    base_prompt: str | None = None
    prompt: str | None = None
    files: list[str] = field(default_factory=list)
    rag: RagConfig = field(default_factory=RagConfig)


ABOUT_ACTIVITYWATCH = """ActivityWatch is a free and open-source automated time-tracker that helps you track how you spend your time on your devices."""
ABOUT_GPTME = "gptme is a CLI to interact with large language models in a Chat-style interface, enabling the assistant to execute commands and code on the local machine, letting them assist in all kinds of development and terminal-based work."


default_config = Config(
    prompt={
        "about_user": "I am a curious human programmer.",
        "response_preference": "Basic concepts don't need to be explained.",
        "project": {
            "activitywatch": ABOUT_ACTIVITYWATCH,
            "gptme": ABOUT_GPTME,
        },
    },
    env={
        # toml doesn't support None
        # "OPENAI_API_KEY": None
    },
)

# Define the path to the config file
config_path = os.path.expanduser("~/.config/gptme/config.toml")

# Global variable to store the config
_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = _load_config()
    return _config


def _load_config() -> Config:
    """Load the configuration from the config file."""
    doc = _load_config_doc()
    doc.setdefault("prompt", {})
    doc.setdefault("env", {})

    # Parse MCP config if present
    mcp_config = MCPConfig(enabled=False, auto_start=False, servers=[])
    if "mcp" in doc:
        mcp_data = doc.get("mcp", {})
        servers = []
        for server_data in mcp_data.get("servers", []):
            servers.append(MCPServerConfig(**server_data))

        mcp_config = MCPConfig(
            enabled=mcp_data.get("enabled", False),
            auto_start=mcp_data.get("auto_start", False),
            servers=servers,
        )

    prompt = doc.get("prompt", {})
    env = doc.get("env", {})

    return Config(prompt=prompt, env=env, mcp=mcp_config)


def _load_config_doc() -> tomlkit.TOMLDocument:
    # Check if the config file exists
    if not os.path.exists(config_path):
        # If not, create it and write some default settings
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        toml = tomlkit.dumps(default_config.dict())
        with open(config_path, "w") as config_file:
            config_file.write(toml)
        console.log(f"Created config file at {config_path}")
        doc = tomlkit.loads(toml)
        return doc
    else:
        with open(config_path) as config_file:
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
    global _config
    _config = _load_config()


@lru_cache
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
            config_data = dict(tomlkit.load(f))

        # Handle RAG config conversion before creating ProjectConfig
        if "rag" in config_data:
            config_data["rag"] = RagConfig(**config_data["rag"])  # type: ignore

        return ProjectConfig(**config_data)  # type: ignore
    return None


if __name__ == "__main__":
    config = get_config()
    print(config)
