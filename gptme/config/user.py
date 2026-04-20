"""User configuration loading.

Handles loading, merging, and persisting user-level configuration
from ~/.config/gptme/config.toml and config.local.toml.
"""

import copy
import logging
import os
from dataclasses import asdict, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tomlkit
from tomlkit import TOMLDocument

if TYPE_CHECKING:
    from tomlkit.container import Container

from ..util import path_with_tilde
from .models import (
    LessonsConfig,
    MCPConfig,
    ProviderConfig,
    UserConfig,
    UserIdentityConfig,
    UserPromptConfig,
)

logger = logging.getLogger(__name__)


# Define the path to the config file
config_path = os.path.expanduser("~/.config/gptme/config.toml")


def _filter_known_fields(
    cls: type, data: dict[str, Any], section: str
) -> dict[str, Any]:
    """Filter a dict down to fields known to a dataclass, warning about unknown keys.

    This keeps older gptme versions forward-compatible with newer config schemas:
    unknown keys are dropped with a warning instead of raising TypeError.
    """
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        logger.warning(
            f"Unknown keys in [{section}] config: {sorted(unknown)} (ignored)"
        )
    return {k: v for k, v in data.items() if k in known}


ABOUT_ACTIVITYWATCH = """ActivityWatch is a free and open-source automated time-tracker that helps you track how you spend your time on your devices."""
ABOUT_GPTME = "gptme is a CLI to interact with large language models in a Chat-style interface, enabling the assistant to execute commands and code on the local machine, letting them assist in all kinds of development and terminal-based work."


# TODO: include this in docs
default_config = UserConfig(
    user=UserIdentityConfig(
        name="User",
        about="I am a curious human programmer.",
        response_preference="Basic concepts don't need to be explained.",
    ),
    prompt=UserPromptConfig(
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


# Track whether we've already logged the user config message
_user_config_logged: set[Path] = set()


def load_user_config(path: str | None = None) -> UserConfig:
    """Load the user configuration from the config file.

    Also loads config.local.toml from the same directory if it exists,
    merging it into the main config (local values override main values).
    This allows committing preferences to dotfiles while keeping secrets separate.
    """
    config_file_path = path or config_path
    config_file = Path(config_file_path)
    config = _load_config_doc(path).unwrap()

    # Look for local config file in the same directory
    local_path = config_file.parent / "config.local.toml"
    has_local = local_path.exists()
    if has_local:
        with open(local_path) as f:
            local_config = tomlkit.load(f).unwrap()
        config = _merge_config_data(config, local_config)

    # Log config paths (only once per config file)
    # Use logger instead of console to avoid polluting stdout
    # (console.log writes to stdout, breaking JSON output in doctor --json, ACP, etc.)
    if config_file not in _user_config_logged:
        _user_config_logged.add(config_file)
        msg = f"Using user configuration from {path_with_tilde(config_file)}"
        if has_local:
            msg += " with local overrides"
        logger.info(msg)

    # Note: prompt and env are optional - defaults are used if missing

    prompt_data = config.pop("prompt", {})
    prompt = UserPromptConfig(
        **_filter_known_fields(UserPromptConfig, prompt_data, "prompt")
    )

    # Parse [user] section (validate it's a dict in case of e.g. user = "Erik")
    user_data = config.pop("user", {})
    if not isinstance(user_data, dict):
        logger.warning(f"[user] should be a table, got {type(user_data).__name__}")
        user_data = {}
    user_identity = UserIdentityConfig(
        **_filter_known_fields(UserIdentityConfig, user_data, "user")
    )

    # Backward compat: if about/response_preference not set in [user],
    # fall back to [prompt].about_user / [prompt].response_preference
    about = user_identity.about
    if about is None and prompt.about_user is not None:
        about = prompt.about_user
    resp_pref = user_identity.response_preference
    if resp_pref is None and prompt.response_preference is not None:
        resp_pref = prompt.response_preference
    if about != user_identity.about or resp_pref != user_identity.response_preference:
        user_identity = UserIdentityConfig(
            name=user_identity.name,
            about=about,
            response_preference=resp_pref,
            avatar=user_identity.avatar,
        )

    env = config.pop("env", {})
    mcp = MCPConfig.from_dict(config.pop("mcp", {}))

    # Parse custom providers
    providers_config = config.pop("providers", [])
    providers = [ProviderConfig(**provider) for provider in providers_config]

    # Parse lessons config
    lessons_data = config.pop("lessons", None)
    lessons = (
        LessonsConfig(dirs=lessons_data.get("dirs", []))
        if lessons_data and isinstance(lessons_data, dict)
        else None
    )

    # Extract plugin-prefixed keys (e.g., [plugin.retrieval] -> plugin["retrieval"])
    # This allows plugins to have their own config sections without triggering warnings
    plugin_config: dict[str, dict] = {}
    if plugin_data := config.pop("plugin", None):
        if isinstance(plugin_data, dict):
            plugin_config = plugin_data

    if config:
        logger.warning(f"Unknown keys in config: {config.keys()}")

    return UserConfig(
        prompt=prompt,
        user=user_identity,
        env=env,
        mcp=mcp,
        providers=providers,
        lessons=lessons,
        plugin=plugin_config,
    )


def _strip_none(d: dict) -> dict:
    """Recursively remove None values from a dict (tomlkit can't serialize None)."""
    return {
        k: _strip_none(v) if isinstance(v, dict) else v
        for k, v in d.items()
        if v is not None
    }


def _load_config_doc(path: str | None = None) -> tomlkit.TOMLDocument:
    if path is None:
        path = config_path
    # Check if the config file exists
    if not os.path.exists(path):
        # If not, create it and write some default settings
        os.makedirs(os.path.dirname(path), exist_ok=True)
        toml = tomlkit.dumps(_strip_none(asdict(default_config)))
        with open(path, "w") as config_file:
            config_file.write(toml)
        logger.info(f"Created config file at {path}")
        doc = tomlkit.loads(toml)
        return doc
    with open(path) as config_file:
        doc = tomlkit.load(config_file)
    return doc


def set_config_value(key: str, value: str) -> None:  # pragma: no cover
    """Set a value in the user config file."""
    doc: TOMLDocument | Container = _load_config_doc()

    # Set the value
    keypath = key.split(".")
    d: TOMLDocument | Container = doc
    for key in keypath[:-1]:
        if key not in d:
            d[key] = tomlkit.table()
        d = d[key]  # type: ignore[assignment]
    d[keypath[-1]] = value

    # Write the config
    with open(config_path, "w") as config_file:
        tomlkit.dump(doc, config_file)

    # Reload config
    from .core import reload_config

    reload_config()


def _merge_config_data(main_config: dict, local_config: dict) -> dict:
    """
    Merge local configuration into main configuration.

    For MCP servers, merge by name - local server env vars are merged into main server config.
    For other sections, local config extends/overrides main config.
    """

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
