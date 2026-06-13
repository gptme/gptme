"""Chat session configuration.

ChatConfig manages per-conversation settings including model selection,
tool configuration, workspace paths, and agent settings.
"""

import logging
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
from tomlkit.exceptions import TOMLKitError
from typing_extensions import Self

if sys.version_info >= (3, 11):
    import tomllib

    _CHAT_CONFIG_LOAD_ERRORS = (OSError, tomllib.TOMLDecodeError)
else:
    tomllib = None

    _CHAT_CONFIG_LOAD_ERRORS = (OSError, TOMLKitError)

from ..util import path_with_tilde
from .models import AgentConfig, MCPConfig
from .project import get_project_config

if TYPE_CHECKING:
    from ..tools.base import ToolFormat

logger = logging.getLogger(__name__)


def _coerce_config_path(value: object, field_name: str) -> Path:
    """Convert a JSON-provided path value to a resolved Path."""
    if not isinstance(value, str | os.PathLike):
        raise ValueError(f"chat.{field_name} must be a string path")
    return Path(value).expanduser().resolve()


def ensure_workspace_dir(workspace: Path) -> None:
    """Create the workspace directory unless it already exists in some form.

    mkdir(parents=True, exist_ok=True) only tolerates a pre-existing
    *directory*; it still raises FileExistsError when the path is a symlink or
    file (e.g. a manually-linked workspace). Skip creation when the path
    already exists in any form. is_symlink() also catches broken symlinks,
    which exists() reports as absent.
    """
    if not (workspace.is_symlink() or workspace.exists()):
        workspace.mkdir(parents=True, exist_ok=True)


def require_workspace_exists(workspace: Path) -> None:
    """Raise an actionable error if a configured workspace is missing.

    Workspaces may be symlinks to external directories that later get moved or
    deleted. We surface a clear message instead of a bare FileNotFoundError
    (CLI) or a silently-dying step thread (server) when about to chdir into it.
    """
    if not workspace.exists():
        raise FileNotFoundError(
            f"Configured workspace does not exist: {workspace}\n"
            "It may have been moved or deleted. Restore the directory, or update "
            "the conversation's workspace (chat.workspace in its config.toml)."
        )


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
    # Max tokens for the model's response. None = provider/model default.
    max_tokens: int | None = None
    # Sampling temperature override. None = use TEMPERATURE constant (env default 0).
    temperature: float | None = None
    # Top-p nucleus sampling override. None = use TOP_P constant (env default 0.1).
    top_p: float | None = None
    # CLI sessions default to the current directory. Server sessions load
    # through from_logdir/load_or_create to get per-conversation workspaces.
    workspace: Path = field(default_factory=Path.cwd)
    agent: Path | None = None
    system_prompt: str | None = None

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
    def from_dict(cls, config_data: dict, *, create_workspace: bool = True) -> Self:
        """Create a ChatConfig instance from a dictionary. Warns about unknown keys."""
        _logdir = config_data.pop("_logdir", None)

        # Extract chat settings
        chat_data = config_data.pop("chat", {})
        if not isinstance(chat_data, dict):
            raise ValueError("chat must be an object")

        # Convert workspace to Path if present and resolve to absolute path
        if "workspace" in chat_data:
            workspace_value = chat_data["workspace"]
            # Handle magic "@log" value like CLI does
            if workspace_value == "@log":
                if not _logdir:
                    raise ValueError("Cannot use '@log' workspace without logdir")
                chat_data["workspace"] = (_logdir / "workspace").resolve()
                if create_workspace:
                    ensure_workspace_dir(chat_data["workspace"])
            else:
                chat_data["workspace"] = _coerce_config_path(
                    workspace_value, "workspace"
                )
        # For old-style config, check if workspace is in the logdir
        elif _logdir and (_logdir / "workspace").exists():
            chat_data["workspace"] = (_logdir / "workspace").resolve()

        # Extract agent
        _missing = object()
        agent_path = chat_data.pop("agent", _missing)
        if agent_path is _missing or agent_path is None or agent_path == "":
            agent = None
        else:
            agent = _coerce_config_path(agent_path, "agent")

        system_prompt = chat_data.get("system_prompt")
        if system_prompt is not None and not isinstance(system_prompt, str):
            raise ValueError("chat.system_prompt must be a string")

        env = config_data.pop("env", {})
        if not isinstance(env, dict):
            raise ValueError("env must be an object")
        mcp_data = config_data.pop("mcp", None)
        if mcp_data is not None and not isinstance(mcp_data, dict):
            raise ValueError("mcp must be an object")
        mcp = MCPConfig.from_dict(mcp_data) if mcp_data is not None else None

        # Type-validate numeric fields so wrong-type values raise ValueError here
        # (at the API boundary) instead of silently storing bad types that crash later.
        for field_name in ("temperature", "top_p"):
            val = chat_data.get(field_name)
            if val is not None and not isinstance(val, int | float):
                raise ValueError(
                    f"chat.{field_name} must be a number, got {type(val).__name__}"
                )
        max_tokens_val = chat_data.get("max_tokens")
        if max_tokens_val is not None and (
            not isinstance(max_tokens_val, int) or isinstance(max_tokens_val, bool)
        ):
            raise ValueError(
                f"chat.max_tokens must be an integer, got {type(max_tokens_val).__name__}"
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
            # For new conversations without a saved config, use a
            # per-conversation workspace directory under the logdir.
            # This ensures server sessions get isolated workspaces
            # instead of sharing the server process's cwd.
            workspace = path / "workspace"
            ensure_workspace_dir(workspace)
            return cls(_logdir=path, workspace=workspace.resolve())
        try:
            if tomllib is not None:
                with open(chat_config_path, "rb") as f:
                    config_data = tomllib.load(f)
            else:
                with open(chat_config_path) as f:
                    config_data = tomlkit.load(f).unwrap()
            config_data["_logdir"] = path
            return cls.from_dict(config_data)
        except _CHAT_CONFIG_LOAD_ERRORS as e:
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
            # Use is_symlink() too: exists() follows symlinks and returns False
            # for a dangling one (e.g. a tmp workspace that was cleaned up),
            # which would skip removal and make symlink_to raise FileExistsError.
            if workspace_path.is_symlink() or workspace_path.exists():
                if workspace_path.is_dir() and not workspace_path.is_symlink():
                    try:
                        # If it's an empty directory (e.g., auto-created by
                        # from_logdir for a new conversation), it's safe to
                        # remove and replace with a symlink.
                        workspace_path.rmdir()
                    except OSError:
                        raise ValueError(
                            f"Workspace directory '{workspace_path}' already exists and contains data. "
                            "Cannot change workspace when directory is in use. "
                            "Please move or rename the existing directory first."
                        ) from None
                else:
                    # It's a file or symlink, safe to remove
                    workspace_path.unlink()
            workspace_path.symlink_to(self.workspace)
        else:
            # Workspace IS the log workspace — ensure the directory exists
            # (from_logdir creates it for new conversations, but callers
            # constructing ChatConfig directly may not have)
            ensure_workspace_dir(self.workspace)

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
        # Check before from_logdir: it may create dirs but never writes config.toml.
        is_new_conversation = not (logdir / "config.toml").exists()

        # Load existing config if it exists
        config = cls.from_logdir(logdir)
        defaults = cls()

        # Apply CLI overrides for explicitly provided values
        for field_name in cli_config.__dataclass_fields__:
            if field_name.startswith("_"):
                continue
            cli_value = getattr(cli_config, field_name)
            default_value = getattr(defaults, field_name)

            if field_name == "workspace" and is_new_conversation:
                # For new conversations from_logdir creates logdir/workspace as a
                # server-safe default, but CLI callers want their own cwd.  Always
                # use the cli_config workspace for new conversations so the caller
                # controls where work lands.  Server sessions must pass an explicit
                # workspace (e.g. "@log" → logdir/workspace) in the request config.
                logger.debug(f"New conversation: using CLI workspace: {cli_value}")
                config = replace(config, workspace=cli_value)
            # For optional fields that default to None, check if explicitly provided
            elif (
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

        # API clients use empty-string as an explicit clear signal for optional
        # text fields that would otherwise be indistinguishable from "omitted".
        if config.system_prompt == "":
            config = replace(config, system_prompt=None)

        return config
