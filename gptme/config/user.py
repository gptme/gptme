"""User configuration loading.

Handles loading, merging, and persisting user-level configuration
from ~/.config/gptme/config.toml and config.local.toml.
"""

import copy
import logging
import os
from collections.abc import MutableMapping
from dataclasses import asdict, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tomlkit
from tomlkit import TOMLDocument

if TYPE_CHECKING:
    from tomlkit.container import Container
    from tomlkit.items import AoT

from ..util import path_with_tilde
from .models import (
    LessonsConfig,
    MCPConfig,
    ModelsConfig,
    PluginsConfig,
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


def _strip_unknown_config_keys(path: str, keys: set[str]) -> None:
    """Remove the given top-level keys from a config file on disk.

    Called after detecting unknown keys in load_user_config() so that the
    warning fires only once instead of on every future invocation.
    """
    if not os.path.exists(path):
        return
    doc = _load_config_doc(path)
    changed = False
    for key in keys:
        if key in doc:
            del doc[key]  # type: ignore[attr-defined]
            changed = True
    if changed:
        try:
            with open(path, "w") as f:
                tomlkit.dump(doc, f)
        except OSError as e:
            logger.warning(f"Could not strip unknown config keys from {path}: {e}")


ABOUT_ACTIVITYWATCH = """ActivityWatch is a free and open-source automated time-tracker that helps you track how you spend your time on your devices."""
ABOUT_GPTME = "gptme is a CLI to interact with large language models in a Chat-style interface, enabling the assistant to execute commands and code on the local machine, letting them assist in all kinds of development and terminal-based work."


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

USER_CONFIG_SOURCE_ENV = "env"
USER_CONFIG_SOURCE_LOCAL = "config.local.toml"
USER_CONFIG_SOURCE_MAIN = "config.toml"


def get_user_config_paths(path: str | None = None) -> tuple[Path, Path]:
    """Return the main and local user config paths."""
    config_file = Path(path or config_path)
    return config_file, config_file.parent / "config.local.toml"


def _get_nested_config_value(doc: TOMLDocument, *keys: str) -> Any | None:
    """Look up a nested TOML value by key path."""
    current: Any = doc.unwrap()
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def get_user_config_env_source(key: str, path: str | None = None) -> str | None:
    """Return where an env-backed user setting currently comes from.

    Precedence matches ``Config.get_env`` for the user-config/global portion:
    process environment first, then ``config.local.toml``, then ``config.toml``.
    """
    prefixed = f"GPTME_{key}" if not key.startswith("GPTME_") else key
    bare = key.removeprefix("GPTME_") if key.startswith("GPTME_") else key

    if prefixed in os.environ or bare in os.environ:
        return USER_CONFIG_SOURCE_ENV

    config_file, local_path = get_user_config_paths(path)
    if local_path.exists():
        with open(local_path) as f:
            local_doc = tomlkit.load(f)
        if _get_nested_config_value(local_doc, "env", bare) is not None:
            return USER_CONFIG_SOURCE_LOCAL

    main_doc = _load_config_doc(str(config_file))
    if _get_nested_config_value(main_doc, "env", bare) is not None:
        return USER_CONFIG_SOURCE_MAIN

    return None


def get_default_model_source(path: str | None = None) -> str | None:
    """Return where the default model comes from.

    Precedence mirrors model resolution: ``[models].default`` (local then main
    config) takes priority, then the ``MODEL`` env var / ``[env]`` source.
    """
    config_file, local_path = get_user_config_paths(path)
    if local_path.exists():
        with open(local_path) as f:
            local_doc = tomlkit.load(f)
        if _get_nested_config_value(local_doc, "models", "default") is not None:
            return USER_CONFIG_SOURCE_LOCAL
    main_doc = _load_config_doc(str(config_file))
    if _get_nested_config_value(main_doc, "models", "default") is not None:
        return USER_CONFIG_SOURCE_MAIN
    return get_user_config_env_source("MODEL", path)


def get_user_config_runtime_info(path: str | None = None) -> dict[str, str | bool]:
    """Return read/write path details for the user config UI."""
    config_file, local_path = get_user_config_paths(path)
    return {
        "config_path": str(path_with_tilde(config_file)),
        "local_config_path": str(path_with_tilde(local_path)),
        "local_config_exists": local_path.exists(),
        "write_target": str(path_with_tilde(config_file)),
        "local_overrides_main": True,
    }


def load_user_config(path: str | None = None) -> UserConfig:
    """Load the user configuration from the config file.

    Also loads config.local.toml from the same directory if it exists,
    merging it into the main config (local values override main values).
    This allows committing preferences to dotfiles while keeping secrets separate.
    """
    config_file_path = path or config_path
    config_file, local_path = get_user_config_paths(config_file_path)
    config = _load_config_doc(path).unwrap()

    # Look for local config file in the same directory
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

    # Parse [models] section (model-related preferences like favorites)
    models_data = config.pop("models", {})
    if not isinstance(models_data, dict):
        logger.warning(f"[models] should be a table, got {type(models_data).__name__}")
        models_data = {}
    favorites_data = models_data.get("favorites", [])
    if not isinstance(favorites_data, list):
        logger.warning(
            f"[models].favorites should be a list, got {type(favorites_data).__name__}"
        )
        favorites_data = []
    default_model = models_data.get("default")
    if default_model is not None and not isinstance(default_model, str):
        logger.warning("[models].default should be a string")
        default_model = None
    models_config = ModelsConfig(
        default=default_model,
        favorites=[str(m) for m in favorites_data if isinstance(m, str)],
    )

    # Parse lessons config
    lessons_data = config.pop("lessons", None)
    lessons = (
        LessonsConfig(dirs=lessons_data.get("dirs", []))
        if lessons_data and isinstance(lessons_data, dict)
        else None
    )

    # Parse [plugins] section (search paths + enabled allowlist)
    plugins_data = config.pop("plugins", {})
    if not isinstance(plugins_data, dict):
        raise ValueError("plugins must be an object")
    plugins = PluginsConfig(
        paths=plugins_data.get("paths", []),
        enabled=plugins_data.get("enabled", []),
    )

    # Extract plugin-prefixed keys (e.g., [plugin.retrieval] -> plugin["retrieval"])
    # This allows plugins to have their own config sections without triggering warnings
    plugin_config: dict[str, dict] = {}
    if plugin_data := config.pop("plugin", None):
        if isinstance(plugin_data, dict):
            plugin_config = plugin_data

    if config:
        unknown = set(config.keys())
        strip_targets = str(path_with_tilde(config_file))
        if has_local:
            strip_targets += f" and {path_with_tilde(local_path)}"
        logger.warning(
            f"Unknown keys in config: {sorted(unknown)} — stripping from"
            f" {strip_targets}"
        )
        _strip_unknown_config_keys(str(config_file), unknown)
        if has_local:
            _strip_unknown_config_keys(str(local_path), unknown)

    return UserConfig(
        prompt=prompt,
        user=user_identity,
        env=env,
        mcp=mcp,
        providers=providers,
        lessons=lessons,
        models=models_config,
        plugins=plugins,
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


def set_config_value(
    key: str, value: Any, reload: bool = True, local: bool = False
) -> None:
    """Set a value in the user config file.

    Args:
        key: Dot-separated key path (e.g. "env.ANTHROPIC_API_KEY").
        value: Value to set. Type is preserved in the TOML output.
        reload: Whether to reload the in-memory config after writing.
        local: If True, write to config.local.toml instead of config.toml.
               Use for secrets (API keys) that should not be in the shared config.

    Raises:
        ValueError: If an intermediate keypath segment already exists
            but is not a TOML table (e.g. traversing into a string value).
    """
    if local:
        _, local_path = get_user_config_paths()
        write_path = str(local_path)
        # Load existing local config or start empty (no defaults)
        doc: TOMLDocument | Container = (
            _load_config_doc(write_path) if local_path.exists() else tomlkit.document()
        )
    else:
        write_path = config_path
        doc = _load_config_doc()

    # Set the value
    keypath = key.split(".")
    d: TOMLDocument | Container = doc
    for k in keypath[:-1]:
        if k not in d:
            d[k] = tomlkit.table()
        else:
            existing = d[k]
            if not isinstance(existing, MutableMapping):
                raise ValueError(f"Cannot set '{key}': '{k}' exists but is not a table")
        d = d[k]  # type: ignore[assignment]
    d[keypath[-1]] = value

    # Write the config
    if local:
        os.makedirs(os.path.dirname(write_path), exist_ok=True)
    with open(write_path, "w") as config_file:
        tomlkit.dump(doc, config_file)

    if reload:
        from .core import reload_config

        reload_config()


def save_provider_config(
    provider: "ProviderConfig", reload: bool = True, local: bool = False
) -> None:
    """Append a [[providers]] entry to the user config file.

    Args:
        provider: ProviderConfig to save.
        reload: Whether to reload the in-memory config after writing.
        local: If True, write to config.local.toml instead of config.toml.
               Use for entries with inline api_key (secrets).
    """
    if local:
        _, local_path = get_user_config_paths()
        write_path = str(local_path)
        doc: TOMLDocument | Container = (
            _load_config_doc(write_path) if local_path.exists() else tomlkit.document()
        )
    else:
        write_path = config_path
        doc = _load_config_doc()

    provider_table = tomlkit.table()
    provider_table.add("name", provider.name)
    provider_table.add("base_url", provider.base_url)
    if provider.api_key:
        provider_table.add("api_key", provider.api_key)
    if provider.api_key_env:
        provider_table.add("api_key_env", provider.api_key_env)
    if provider.default_model:
        provider_table.add("default_model", provider.default_model)

    if "providers" not in doc:
        doc.add("providers", tomlkit.aot())  # type: ignore[attr-defined]

    providers: AoT = doc["providers"]  # type: ignore[assignment]
    for idx, existing_provider in enumerate(providers):
        if existing_provider.get("name") == provider.name:
            providers[idx] = provider_table
            break
    else:
        providers.append(provider_table)

    if local:
        os.makedirs(os.path.dirname(write_path), exist_ok=True)
    with open(write_path, "w") as config_file:
        tomlkit.dump(doc, config_file)

    if reload:
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

        elif key == "providers" and isinstance(value, list):
            # Merge providers by name - local provider entries update matching
            # main providers (e.g. adding api_key to an otherwise-identical entry),
            # and new providers are appended.
            if key not in merged:
                merged[key] = []
            main_providers = merged[key]
            main_by_name: dict = {}
            for p in main_providers:
                if isinstance(p, dict) and "name" in p:
                    main_by_name[p["name"]] = p
            for local_provider in value:
                if not isinstance(local_provider, dict):
                    main_providers.append(local_provider)
                    continue
                name = local_provider.get("name")
                if name and name in main_by_name:
                    main_by_name[name].update(local_provider)
                else:
                    main_providers.append(local_provider)
        elif (
            isinstance(value, dict) and key in merged and isinstance(merged[key], dict)
        ):
            # Recursive merge for nested dictionaries
            merged[key] = _merge_config_data(merged[key], value)
        else:
            # Direct override for other keys
            merged[key] = value

    return merged
