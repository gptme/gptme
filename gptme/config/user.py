"""User configuration loading.

Handles loading, merging, and persisting user-level configuration
from ~/.config/gptme/config.toml and config.local.toml.
"""

import codecs
import copy
import locale
import logging
import os
from collections.abc import MutableMapping
from dataclasses import asdict, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import tomlkit
from tomlkit import TOMLDocument

if TYPE_CHECKING:
    from tomlkit.container import Container
    from tomlkit.items import AoT

from ..util import path_with_tilde
from .models import (
    HooksConfig,
    LessonsConfig,
    MCPConfig,
    ModelsConfig,
    PluginsConfig,
    ProviderConfig,
    ScriptHookConfig,
    SettingsConfig,
    UserConfig,
    UserIdentityConfig,
    UserPromptConfig,
)

logger = logging.getLogger(__name__)


def _legacy_codec_candidates() -> list[str]:
    """Codecs to try after UTF-8 when reading a config, most likely first.

    `locale.getpreferredencoding(False)` is not sufficient on its own. Under
    Python's UTF-8 mode (`-X utf8`, `PYTHONUTF8=1`, or a launcher that enables it)
    it returns "utf-8" whatever the platform code page is -- so on exactly the
    Windows install whose code page wrote the file, the codec needed to read it
    back is the one this function would hide. `locale.getencoding()` (3.11+)
    reports the real code page regardless of UTF-8 mode; on 3.10 the same value is
    only reachable through `getdefaultlocale`, which is not yet deprecated there.

    Names are deduplicated by their canonical codec name, and "utf-8" is dropped
    since the caller has already tried it.
    """
    candidates: list[str] = []
    getencoding = getattr(locale, "getencoding", None)
    if getencoding is not None:
        candidates.append(getencoding())
    else:  # Python 3.10
        try:
            candidates.append(locale.getdefaultlocale()[1] or "")
        except ValueError:
            pass
    candidates.append(locale.getpreferredencoding(False))

    result: list[str] = []
    seen = {"utf-8"}
    for name in candidates:
        if not name:
            continue
        try:
            canonical = codecs.lookup(name).name
        except LookupError:
            continue
        if canonical not in seen:
            seen.add(canonical)
            result.append(name)
    return result


# Configs whose text came out of a guessed codec rather than a declared one.
# Rewriting one as UTF-8 would persist whatever text that guess produced, and the
# original bytes would be gone -- so the bytes are backed up first, once.
_encoding_unverified: set[str] = set()


def _mark_encoding_unverified(path: str | Path) -> None:
    """Record that this file's text came from a codec that was guessed, not declared."""
    _encoding_unverified.add(str(Path(path).resolve()))


def _back_up_before_reencoding(path: str | Path) -> None:
    """Keep the original bytes of a config whose decoding was a guess.

    Called before overwriting such a file as UTF-8. A misread is reversible -- the
    bytes still say what they always said, they were just read through the wrong
    table -- but only while those bytes exist. One backup per file: a second save
    would otherwise overwrite the original with the already-garbled version.

    Raises `OSError` if the bytes cannot be preserved, which aborts the caller's
    write. That is the point: the only reason to rewrite the file is that the
    original is recoverable, so a backup that did not happen removes the
    justification for the write rather than being a detail to log and move past.
    Refusing to save is recoverable -- the user is told and can copy the file by
    hand -- while a rewrite without a backup is not.

    The copy goes through a temporary file and a rename so that a failure part
    way through leaves no `.orig` at all. A truncated one would be as destructive
    as none while reading as success to the next call.
    """
    resolved = str(Path(path).resolve())
    if resolved not in _encoding_unverified:
        return
    backup = Path(f"{path}.orig")
    if backup.exists():
        # Already preserved by an earlier save; this file is safe to rewrite.
        _encoding_unverified.discard(resolved)
        return
    staged = Path(f"{path}.orig.tmp")
    try:
        staged.write_bytes(Path(path).read_bytes())
        staged.replace(backup)
    except OSError as e:
        staged.unlink(missing_ok=True)
        # The marker is deliberately left in place: this file still holds
        # unpreserved original bytes, so a later save must try again rather than
        # treat it as already handled.
        raise OSError(
            f"Refusing to rewrite {path} as UTF-8: its previous encoding could not "
            f"be confirmed, and the original bytes could not be copied to {backup} "
            f"({e}). Saving would discard them. Back the file up by hand, or fix "
            "the write error, and try again."
        ) from e
    _encoding_unverified.discard(resolved)
    logger.warning(
        f"Rewriting {path} as UTF-8, but its previous encoding could not be "
        f"confirmed. The original bytes are saved at {backup} in case any "
        "non-ASCII values were misread."
    )


def _read_config_text(path: str | Path) -> str:
    """Read a TOML config file as text.

    TOML is defined to be UTF-8, so that is what we decode as. But gptme wrote
    these files without naming an encoding until recently, so one left behind by
    an older install may be in the platform's preferred encoding -- a legacy code
    page on Windows. Falling back to that codec keeps such a config readable
    instead of making gptme fail to start; the next write re-encodes it as UTF-8,
    since the writers are now explicit.

    A decode that succeeds is not evidence that this was the codec that wrote the
    file, and no cheap check makes it so. A single-byte code page accepts anything:
    cp936 bytes read as cp1252 "succeed" as mojibake. Multi-byte code pages are no
    safer in practice -- the same cp936 bytes also decode cleanly, and wrongly, as
    big5, cp949 and cp932. Mojibake is still valid TOML, so the parser will not
    catch it either. So every fallback here is treated as a guess: it keeps the file
    readable, but the bytes are backed up before anything rewrites them as UTF-8,
    since a misread is reversible only while the original bytes exist.

    Never raises `UnicodeDecodeError`: callers wrap this in a TOML parse and handle
    parse errors (`ChatConfig.from_logdir` degrades to defaults), so a file no
    candidate can decode is read with `errors="replace"`. That usually reaches the
    parser and is rejected, but not always -- undecodable bytes inside a quoted
    value leave the document well-formed -- so replacement is treated as a guess
    too, and the more damaging one: U+FFFD discards which byte it stood for, so
    only the backup can undo it.
    """
    data = Path(path).read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    for codec in _legacy_codec_candidates():
        try:
            text = data.decode(codec)
        except UnicodeDecodeError:
            continue
        logger.warning(
            f"Config file {path} is not valid UTF-8. Reading it as {codec}, the "
            "locale's encoding, which is what an older gptme would have written it "
            "in -- but a successful decode does not prove that, so non-ASCII values "
            "may be garbled if the file came from a machine with a different one. "
            "They are preserved as read; check them before saving, since saving "
            "rewrites the file as UTF-8."
        )
        _mark_encoding_unverified(path)
        return text

    logger.warning(
        f"Config file {path} is not valid UTF-8 and no legacy codec could decode "
        "it; reading it with replacement characters. Some values may be garbled, "
        "and the original bytes are saved before anything rewrites the file."
    )
    _mark_encoding_unverified(path)
    return data.decode("utf-8", errors="replace")


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
            del doc[key]
            changed = True
    if changed:
        try:
            # May raise if the file's decoding was a guess and its original bytes
            # cannot be preserved. Swallowing it is right *here* -- this write is
            # only a cosmetic cleanup, so not doing it costs a repeated warning
            # and nothing else -- but the reason is worth naming, because the same
            # exception aborting a real save is deliberate.
            _back_up_before_reencoding(path)
            with open(path, "w", encoding="utf-8") as f:
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
        local_doc = tomlkit.loads(_read_config_text(local_path))
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
        local_doc = tomlkit.loads(_read_config_text(local_path))
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
        local_config = tomlkit.loads(_read_config_text(local_path)).unwrap()
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

    settings_data = config.pop("settings", {})
    if not isinstance(settings_data, dict):
        logger.warning(
            f"[settings] should be a table, got {type(settings_data).__name__}"
        )
        settings_data = {}
    settings_known = {f.name for f in fields(SettingsConfig)}
    settings_unknown = set(settings_data) - settings_known
    if settings_unknown:
        logger.warning(
            f"Unknown keys in [settings] config: {sorted(settings_unknown)} (ignored)"
        )
    settings = SettingsConfig(
        **{k: v for k, v in settings_data.items() if k in settings_known}
    )
    if settings.gear is not None:
        from ..gears import parse_gear

        try:
            settings.gear = parse_gear(settings.gear)
        except ValueError:
            logger.warning("[settings].gear should be an integer from 0 to 4")
            settings.gear = None

    hooks_data = config.pop("hooks", {})
    if not isinstance(hooks_data, dict):
        raise ValueError("hooks must be an object")
    scripts_data = hooks_data.pop("scripts", [])
    if not isinstance(scripts_data, list):
        raise ValueError("hooks.scripts must be a list")
    if hooks_data:
        logger.warning(
            f"Unknown keys in [hooks] config: {sorted(hooks_data)} (ignored)"
        )
    if not all(isinstance(item, dict) for item in scripts_data):
        raise ValueError("each hooks.scripts entry must be an object")
    hooks = HooksConfig(
        scripts=[
            ScriptHookConfig(
                **_filter_known_fields(ScriptHookConfig, item, "hooks.scripts")
            )
            for item in scripts_data
        ]
    )

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
        settings=settings,
        hooks=hooks,
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
        with open(path, "w", encoding="utf-8") as config_file:
            config_file.write(toml)
        logger.info(f"Created config file at {path}")
        doc = tomlkit.loads(toml)
        return doc
    return tomlkit.loads(_read_config_text(path))


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
        OSError: If the file was decoded by a guessed codec and its original
            bytes could not be backed up. Nothing is written in that case, so
            the unrecoverable values stay on disk rather than being replaced by
            a possibly-garbled rewrite.
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
        d = cast("Container", d[k])
    d[keypath[-1]] = value

    # Write the config
    if local:
        os.makedirs(os.path.dirname(write_path), exist_ok=True)
    _back_up_before_reencoding(write_path)
    with open(write_path, "w", encoding="utf-8") as config_file:
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

    Raises:
        OSError: If the file was decoded by a guessed codec and its original
            bytes could not be backed up; nothing is written in that case.
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
        doc.add("providers", tomlkit.aot())

    providers = cast("AoT", doc["providers"])
    for idx, existing_provider in enumerate(providers):
        if existing_provider.get("name") == provider.name:
            providers[idx] = provider_table
            break
    else:
        providers.append(provider_table)

    if local:
        os.makedirs(os.path.dirname(write_path), exist_ok=True)
    _back_up_before_reencoding(write_path)
    with open(write_path, "w", encoding="utf-8") as config_file:
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
