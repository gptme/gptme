"""Chat session configuration.

ChatConfig manages per-conversation settings including model selection,
tool configuration, workspace paths, and agent settings.
"""

import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
from tomlkit.exceptions import TOMLKitError
from typing_extensions import Self

from ..util import path_with_tilde
from .models import AgentConfig, MCPConfig
from .project import get_project_config

if TYPE_CHECKING:
    from ..tools.base import ToolFormat

logger = logging.getLogger(__name__)


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

        # Load existing config as TOMLDocument to preserve formatting/comments
        if chat_config_path.exists():
            try:
                with open(chat_config_path) as f:
                    doc = tomlkit.load(f)
                # Update document in-place, preserving formatting
                for key, value in config_dict.items():
                    if isinstance(value, dict) and key in doc:
                        # Update nested tables in-place
                        table = doc[key]
                        assert isinstance(table, dict)
                        for k, v in value.items():
                            table[k] = v
                        # Remove keys no longer present
                        for k in list(table):
                            if k not in value:
                                del table[k]
                    elif isinstance(value, dict):
                        # New section not yet in doc: use tomlkit.table() so it
                        # serializes as a proper [section] header, not an inline table
                        t = tomlkit.table()
                        t.update(value)
                        doc[key] = t
                # Remove top-level keys no longer present
                for key in list(doc):
                    if key not in config_dict:
                        del doc[key]
            except (OSError, TOMLKitError, AssertionError):
                # If loading fails, fall back to fresh document
                doc = tomlkit.parse(tomlkit.dumps(config_dict))
        else:
            doc = tomlkit.parse(tomlkit.dumps(config_dict))

        # Use atomic write: write to temp file, then rename
        # This prevents corruption if process is interrupted during write
        # (e.g., daemon thread killed on exit while saving)
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=self._logdir,
            suffix=".toml.tmp",
            delete=False,
        ) as f:
            temp_path = Path(f.name)
            tomlkit.dump(doc, f)
            # Ensure data is flushed to disk before rename
            f.flush()
            os.fsync(f.fileno())

        # Atomic rename (POSIX guarantees atomicity, Windows may not)
        temp_path.replace(chat_config_path)

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
