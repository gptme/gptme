"""User configuration loading.

Handles loading, merging, and persisting user-level configuration
from optional config.runtime.toml defaults, config.toml, and config.local.toml.
"""

import codecs
import copy
import dataclasses
import errno
import locale
import logging
import os
import re
import stat
import threading
import time
import uuid
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from dataclasses import asdict, fields
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, cast

import tomlkit
from tomlkit import TOMLDocument
from tomlkit.items import AoT

if TYPE_CHECKING:
    from tomlkit.container import Container

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]

from ..context.config import parse_context_budget as _parse_context_budget
from ..util import path_with_tilde
from .models import (
    ContextConfig,
    HooksConfig,
    LessonsConfig,
    MCPConfig,
    MCPServerConfig,
    ModelConfig,
    ModelsConfig,
    PluginsConfig,
    ProviderConfig,
    ScriptHookConfig,
    SettingsConfig,
    TuiConfig,
    UserConfig,
    UserIdentityConfig,
    UserPromptConfig,
    _stringify_env,
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


def _group_grants_more_than_other(mode: int) -> bool:
    """Whether preserving ``mode``'s group ownership protects any access.

    A 0644 config grants its group exactly the read access 'other' already has,
    so an ``fchown`` to a foreign gid buys nothing -- and fails for a user who
    is not in that group, which is the common case for a config restored with
    ``rsync -a`` or a bind mount with an unmapped gid. Only a group bit that
    'other' lacks justifies moving the group, and only then can failing to move
    it expose something.
    """
    return bool((mode & 0o070) & ~((mode & 0o007) << 3))


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
    if backup.is_file():
        # Already preserved by an earlier save; this file is safe to rewrite.
        _encoding_unverified.discard(resolved)
        return
    # Per-save staging name: overlapping saves of the same legacy file must not
    # delete each other's in-progress copy.
    staged = Path(f"{path}.orig.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    try:
        original = Path(path).read_bytes()
        # Keep backups owner-only, including after publication. Copying mode
        # bits onto a new inode can grant a different group or inherited ACL
        # access to [env] secrets. Backups need not retain shared readability.
        fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(original)
        try:
            # No-clobber publish: the first completed backup wins, since every
            # concurrent save copied the same original bytes.
            os.link(staged, backup)
        except FileExistsError:
            # A concurrent save published it first. Anything else at that path
            # (a broken symlink, a directory) holds no copy of the bytes.
            if not backup.is_file():
                raise
        except OSError:
            # Filesystem without hard links: fall back to rename unless a
            # concurrent save has already published the backup.
            if not backup.is_file():
                staged.replace(backup)
        staged.unlink(missing_ok=True)
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


def _default_config_path() -> str:
    """``$XDG_CONFIG_HOME/gptme/config.toml``, defaulting to ``~/.config``.

    Honors ``XDG_CONFIG_HOME`` (on every platform, so macOS keeps its
    documented ``~/.config/gptme`` location when it is unset), which lets a
    process run with its own config directory, e.g. a dev server that must not
    load the user's personal prompt files.
    """
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return os.path.join(os.path.expanduser(base), "gptme", "config.toml")


# Define the path to the config file
config_path = _default_config_path()


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
    if not os.path.exists(path) or _doc_without_keys(path, keys) is None:
        return
    try:
        with _config_write_lock(path):
            # Re-read under the lock so a concurrent save is not overwritten
            # with the stale document from the unlocked check above.
            doc = _doc_without_keys(path, keys)
            if doc is None:
                return
            # May raise if the file's decoding was a guess and its original
            # bytes cannot be preserved. Swallowing it is right *here* -- this
            # write is only a cosmetic cleanup, so not doing it costs a repeated
            # warning and nothing else -- but the reason is worth naming,
            # because the same exception aborting a real save is deliberate.
            _back_up_before_reencoding(path)
            _write_config_doc(path, doc)
    except OSError as e:
        logger.warning(f"Could not strip unknown config keys from {path}: {e}")


def _doc_without_keys(path: str, keys: set[str]) -> TOMLDocument | None:
    """Return the config at ``path`` minus ``keys``, or None if nothing to write."""
    doc = _load_config_doc(path)
    if str(Path(path).resolve()) in _encoding_unverified or getattr(
        doc, "_providers_recovered", False
    ):
        # Cosmetic startup cleanup must not persist a guessed decoding or an
        # in-memory provider repair. Only an explicit save may rewrite it.
        return None
    present = [key for key in keys if key in doc]
    if not present:
        return None
    for key in present:
        del doc[key]
    return doc


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
USER_CONFIG_SOURCE_RUNTIME = "config.runtime.toml"


def get_user_config_paths(path: str | None = None) -> tuple[Path, Path]:
    """Return the main and local user config paths."""
    config_file = Path(path or config_path)
    return config_file, config_file.parent / "config.local.toml"


def get_user_config_runtime_path(path: str | None = None) -> Path:
    """Return the optional operator-owned runtime defaults path."""
    config_file, _ = get_user_config_paths(path)
    return config_file.parent / "config.runtime.toml"


def _load_runtime_config_doc(path: str | None = None) -> TOMLDocument | None:
    """Read runtime defaults without creating, repairing, or rewriting the file."""
    runtime_path = get_user_config_runtime_path(path)
    try:
        text = runtime_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as exc:
        raise ValueError(
            f"Could not read runtime config {runtime_path}: {exc}"
        ) from exc
    try:
        doc = tomlkit.loads(text)
        prompt = doc.unwrap().get("prompt", {})
        if not isinstance(prompt, dict):
            raise ValueError("prompt must be a table")
        if "fragments" in prompt:
            UserPromptConfig(fragments=prompt["fragments"])
        return doc
    except (tomlkit.exceptions.TOMLKitError, ValueError) as exc:
        raise ValueError(f"Invalid runtime config {runtime_path}: {exc}") from exc


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
    process environment first, then ``config.local.toml``, ``config.toml``,
    and finally ``config.runtime.toml`` defaults.
    """
    prefixed = f"GPTME_{key}" if not key.startswith("GPTME_") else key
    bare = key.removeprefix("GPTME_") if key.startswith("GPTME_") else key

    # Empty values are skipped by Config.get_env, so they are not the source.
    if os.environ.get(prefixed) or os.environ.get(bare):
        return USER_CONFIG_SOURCE_ENV

    config_file, local_path = get_user_config_paths(path)
    if local_path.exists():
        local_doc = _load_config_doc(str(local_path))
        if _get_nested_config_value(local_doc, "env", bare) is not None:
            return USER_CONFIG_SOURCE_LOCAL

    main_doc = _load_config_doc(str(config_file))
    if _get_nested_config_value(main_doc, "env", bare) is not None:
        return USER_CONFIG_SOURCE_MAIN

    runtime_doc = _load_runtime_config_doc(path)
    if (
        runtime_doc is not None
        and _get_nested_config_value(runtime_doc, "env", bare) is not None
    ):
        return USER_CONFIG_SOURCE_RUNTIME

    return None


def get_default_model_source(path: str | None = None) -> str | None:
    """Return where the effective default model comes from.

    Precedence mirrors model resolution: ``[models].default`` (local, main,
    then runtime defaults) takes priority, then ``MODEL`` env var / ``[env]``.
    """
    config_file, local_path = get_user_config_paths(path)
    if local_path.exists():
        local_doc = _load_config_doc(str(local_path))
        if _get_nested_config_value(local_doc, "models", "default") is not None:
            return USER_CONFIG_SOURCE_LOCAL
    main_doc = _load_config_doc(str(config_file))
    if _get_nested_config_value(main_doc, "models", "default") is not None:
        return USER_CONFIG_SOURCE_MAIN
    runtime_doc = _load_runtime_config_doc(path)
    if (
        runtime_doc is not None
        and _get_nested_config_value(runtime_doc, "models", "default") is not None
    ):
        return USER_CONFIG_SOURCE_RUNTIME
    return get_user_config_env_source("MODEL", path)


def get_model_source_origin(source_kind: str, path: str | None = None) -> str | None:
    """Resolve a structural model source to its concrete user-config layer."""
    if source_kind == "MODEL":
        return get_user_config_env_source("MODEL", path)
    if source_kind == "models.default":
        source = get_default_model_source(path)
        return source if source != USER_CONFIG_SOURCE_ENV else None
    return None


def get_user_config_runtime_info(path: str | None = None) -> dict[str, str | bool]:
    """Return read/write path details for the user config UI."""
    config_file, local_path = get_user_config_paths(path)
    runtime_path = get_user_config_runtime_path(path)
    return {
        "config_path": str(path_with_tilde(config_file)),
        "local_config_path": str(path_with_tilde(local_path)),
        "local_config_exists": local_path.exists(),
        "runtime_config_path": str(path_with_tilde(runtime_path)),
        "runtime_config_exists": runtime_path.exists(),
        "runtime_is_defaults": True,
        "write_target": str(path_with_tilde(config_file)),
        "local_overrides_main": True,
    }


def load_user_config(path: str | None = None) -> UserConfig:
    """Load the user configuration from the config file.

    Loads optional read-only config.runtime.toml defaults, then config.toml,
    then config.local.toml from the same directory (later values override).
    With runtime config present, built-in defaults form the lowest in-memory
    layer. Without it, retain the existing first-run initialization and dataclass
    fallbacks for existing files.
    This allows committing preferences to dotfiles while keeping secrets separate.
    """
    runtime_doc = _load_runtime_config_doc(path)
    try:
        return _load_user_config(path, runtime_doc)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        if runtime_doc is None:
            raise
        raise ValueError(
            "Invalid merged user configuration with runtime defaults from "
            f"{get_user_config_runtime_path(path)}: {exc}"
        ) from exc


def _serialize_user_config(config: UserConfig) -> dict[str, Any]:
    """Serialize model overrides using their public TOML table shape."""
    data = asdict(config)
    if config.mcp is not None:
        data["mcp"] = config.mcp.to_dict()
    model_data = data["models"]
    overrides = model_data.pop("overrides", {})
    model_data.update(overrides)
    return _strip_none(data)


def _with_builtin_defaults(config: dict[str, Any]) -> dict[str, Any]:
    """Apply built-in defaults beneath the explicit runtime/main/local layers."""
    defaults = _serialize_user_config(default_config)
    prompt = config.get("prompt", {})
    if isinstance(prompt, dict):
        # Legacy prompt preferences must still beat built-in user defaults.
        # Explicit [user] fields retain their existing priority over these aliases.
        for key, legacy_key in (
            ("about", "about_user"),
            ("response_preference", "response_preference"),
        ):
            if prompt.get(legacy_key) is not None:
                defaults["user"].pop(key, None)
    return _merge_config_data(defaults, config)


def _mcp_server_entry_constructs(server: dict[str, Any]) -> bool:
    """Return True if ``server`` can construct an ``MCPServerConfig``."""
    try:
        MCPServerConfig(**server)
    except TypeError:
        return False
    return True


def _parse_mcp_config(mcp_data: Any, *, strict: bool) -> MCPConfig:
    """Parse the ``[mcp]`` section.

    ``strict=True`` is for admin-managed ``config.runtime.toml``: schema
    errors must fail loudly so ``load_user_config`` can attribute them to the
    runtime path. ``strict=False`` is for user/local config: a malformed
    entry must not crash every gptme invocation (including ``--help``) —
    skip it with a warning instead. The stricter ``MCPConfig.from_dict``
    (used for per-chat/project config validated over the HTTP API) always
    raises, so that path can turn client input into a clean 4xx.
    """
    if not isinstance(mcp_data, dict):
        msg = f"[mcp] should be a table, got {type(mcp_data).__name__}"
        if strict:
            raise ValueError(msg)
        logger.warning(msg)
        return MCPConfig()
    mcp_data = dict(mcp_data)
    enabled = mcp_data.pop("enabled", False)
    auto_start = mcp_data.pop("auto_start", False)
    raw_servers = mcp_data.pop("servers", [])
    if not isinstance(raw_servers, list):
        msg = f"mcp.servers should be a list, got {type(raw_servers).__name__}"
        if strict:
            raise ValueError(msg)
        logger.warning(msg)
        raw_servers = []
    servers: list[MCPServerConfig] = []
    for server in raw_servers:
        if not isinstance(server, dict):
            msg = f"mcp.servers entries must be objects, got {type(server).__name__}"
            if strict:
                raise ValueError(msg)
            logger.warning(f"{msg} (skipped)")
            continue
        try:
            servers.append(MCPServerConfig(**server))
        except TypeError as e:
            name = server.get("name", "<unnamed>")
            if strict:
                raise ValueError(f"mcp.servers entry invalid: {e}") from e
            logger.warning(f"Skipping invalid mcp.servers entry {name!r}: {e}")
    if mcp_data:
        logger.warning(f"Unknown keys in MCP config: {sorted(mcp_data.keys())}")
    return MCPConfig(enabled=enabled, auto_start=auto_start, servers=servers)


def _provider_entry_constructs(provider: dict[str, Any]) -> bool:
    """Return True if ``provider`` can construct a ``ProviderConfig``."""
    try:
        ProviderConfig(**provider)
    except TypeError:
        return False
    return True


def _parse_providers(providers_config: Any, *, strict: bool) -> list[ProviderConfig]:
    """Parse ``[[providers]]`` entries.

    ``strict=True`` is for admin-managed ``config.runtime.toml``: schema errors
    must fail loudly so ``load_user_config`` can attribute them to the runtime
    path. ``strict=False`` is for user/local config: a typo must not crash
    every gptme invocation (including ``--help``).
    """
    if not isinstance(providers_config, list):
        msg = f"[[providers]] should be a list, got {type(providers_config).__name__}"
        if strict:
            raise ValueError(msg)
        logger.warning(msg)
        return []
    providers: list[ProviderConfig] = []
    for provider in providers_config:
        if not isinstance(provider, dict):
            msg = f"providers entry should be a table, got {type(provider).__name__}"
            if strict:
                raise ValueError(msg)
            logger.warning(f"{msg} (skipped)")
            continue
        try:
            providers.append(ProviderConfig(**provider))
        except TypeError as e:
            name = provider.get("name", "<unnamed>")
            if strict:
                raise ValueError(f"Invalid provider config {name!r}: {e}") from e
            logger.warning(f"Skipping invalid provider config {name!r}: {e}")
    return providers


def _load_user_config(path: str | None, runtime_doc: TOMLDocument | None) -> UserConfig:
    config_file_path = path or config_path
    config_file, local_path = get_user_config_paths(config_file_path)
    main_config = _load_config_doc(path).unwrap()
    # Validate operator-managed providers before merging: a user/local overlay
    # must not repair or hide an invalid runtime default.
    if runtime_doc is not None:
        runtime_raw = runtime_doc.unwrap()
        if "providers" in runtime_raw:
            _parse_providers(runtime_raw["providers"], strict=True)
    writable_keys = set(main_config)
    config = (
        _merge_config_data(runtime_doc.unwrap(), main_config)
        if runtime_doc is not None
        else main_config
    )

    # Look for local config file in the same directory
    has_local = local_path.exists()
    if has_local:
        local_config = _load_config_doc(str(local_path)).unwrap()
        writable_keys.update(local_config)
        config = _merge_config_data(config, local_config)

    if runtime_doc is not None:
        config = _with_builtin_defaults(config)

    # Log config paths (only once per config file)
    # Use logger instead of console to avoid polluting stdout
    # (console.log writes to stdout, breaking JSON output in doctor --json, ACP, etc.)
    if config_file not in _user_config_logged:
        _user_config_logged.add(config_file)
        msg = f"Using user configuration from {path_with_tilde(config_file)}"
        if runtime_doc is not None:
            msg += " with runtime defaults"
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
        user_identity = dataclasses.replace(
            user_identity, about=about, response_preference=resp_pref
        )

    env = _stringify_env(config.pop("env", {}))

    # Parse [mcp] and [[providers]]. Runtime overlays are admin-managed: if
    # config.runtime.toml itself defines mcp/providers, those entries fail
    # loud (load_user_config wraps with the runtime path). User/local
    # malformed entries are always skipped — the mere presence of a runtime
    # overlay must not make user-originated mcp/provider errors fatal.
    if runtime_doc is not None:
        runtime_raw = runtime_doc.unwrap()
        if "mcp" in runtime_raw:
            _parse_mcp_config(runtime_raw["mcp"], strict=True)
    mcp = _parse_mcp_config(config.pop("mcp", {}), strict=False)
    providers = _parse_providers(config.pop("providers", []), strict=False)

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
    tui = _parse_tui_config(config.pop("tui", {}))
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
    favorites_data = models_data.pop("favorites", [])
    if not isinstance(favorites_data, list):
        logger.warning(
            f"[models].favorites should be a list, got {type(favorites_data).__name__}"
        )
        favorites_data = []
    default_model = models_data.pop("default", None)
    if default_model is not None and not isinstance(default_model, str):
        logger.warning("[models].default should be a string")
        default_model = None

    summary_model = models_data.pop("summary", None)
    if summary_model is not None and (
        not isinstance(summary_model, str)
        or "/" not in summary_model
        or not all(summary_model.split("/", 1))
    ):
        logger.warning(
            "[models].summary should be a fully qualified provider/model string"
        )
        summary_model = None

    overrides: dict[str, ModelConfig] = {}
    for model_id, model_data in models_data.items():
        if not isinstance(model_data, dict):
            logger.warning(
                f"[models.{model_id!r}] should be a table, got "
                f"{type(model_data).__name__}"
            )
            continue
        unknown = set(model_data) - {"context_budget"}
        if unknown:
            logger.warning(
                f"Unknown keys in [models.{model_id!r}]: {sorted(unknown)} (ignored)"
            )
        budget = model_data.get("context_budget")
        if budget is None:
            if not unknown:
                logger.warning(
                    f"[models.{model_id!r}] has no recognized overrides (ignored)"
                )
            continue
        overrides[model_id] = ModelConfig(
            context_budget=_parse_context_budget(
                budget, f'models."{model_id}".context_budget'
            )
        )

    models_config = ModelsConfig(
        default=default_model,
        summary=summary_model,
        favorites=[str(m) for m in favorites_data if isinstance(m, str)],
        overrides=overrides,
    )

    # Parse lessons config
    lessons_data = config.pop("lessons", None)
    lessons = (
        LessonsConfig(dirs=lessons_data.get("dirs", []))
        if lessons_data and isinstance(lessons_data, dict)
        else None
    )

    context_data = config.pop("context", {})
    if not isinstance(context_data, dict):
        raise ValueError("context must be an object")
    context = ContextConfig.from_dict(context_data)

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

    unknown = set(config)
    runtime_unknown = unknown - writable_keys
    if runtime_unknown:
        logger.warning(
            f"Unknown keys in runtime config {get_user_config_runtime_path(path)}:"
            f" {sorted(runtime_unknown)} (ignored; file is read-only)"
        )
    unknown &= writable_keys
    if unknown:
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
        context=context,
        models=models_config,
        plugins=plugins,
        settings=settings,
        hooks=hooks,
        tui=tui,
        plugin=plugin_config,
    )


def _parse_tui_config(data: object) -> TuiConfig:
    """Parse [tui]; invalid values warn and fall back to defaults."""
    if not isinstance(data, dict):
        logger.warning(f"[tui] should be a table, got {type(data).__name__}")
        return TuiConfig()
    known = {f.name for f in fields(TuiConfig)}
    if unknown := set(data) - known:
        logger.warning(f"Unknown keys in [tui] config: {sorted(unknown)} (ignored)")
    statusline = data.get("statusline")
    if statusline is not None and not (isinstance(statusline, str) and statusline):
        logger.warning("[tui].statusline should be a command string (ignored)")
        statusline = None
    return TuiConfig(statusline=statusline)


def _strip_none(d: dict) -> dict:
    """Recursively remove None values from a dict (tomlkit can't serialize None)."""
    return {
        k: _strip_none(v) if isinstance(v, dict) else v
        for k, v in d.items()
        if v is not None
    }


def _split_outside_quotes(text: str) -> list[str]:
    """Split provider entries at commas that fall outside quoted strings.

    A URL value like ``base_url = "http://host/v1?a=1,b=2"`` must not be
    split at the inner comma.  Walk the text, track double-quote depth
    (TOML only uses double quotes for basic strings here), and emit a split
    only when the comma is followed by ``word =`` outside any string or comment.
    """
    entries: list[str] = []
    current: list[str] = []
    in_string = False
    in_comment = False
    backslashes = 0
    i = 0
    while i < len(text):
        c = text[i]
        if in_comment:
            current.append(c)
            if c == "\n":
                in_comment = False
        elif in_string:
            current.append(c)
            # Only an odd run of backslashes escapes the following quote.
            if c == '"' and backslashes % 2 == 0:
                in_string = False
            backslashes = backslashes + 1 if c == "\\" else 0
        elif c == '"':
            in_string = True
            current.append(c)
        elif c == "#":
            in_comment = True
            current.append(c)
        elif c == ",":
            rest = text[i + 1 :].lstrip()
            if re.match(r"\w+\s*=", rest):
                entries.append("".join(current))
                current = []
            else:
                current.append(c)
        else:
            current.append(c)
        i += 1
    if any(ch.strip() for ch in current):
        entries.append("".join(current))
    return entries


def _repair_providers_corruption(raw: str, path: str) -> "tomlkit.TOMLDocument | None":
    """Detect and auto-repair the ``providers = [...]`` inline-table corruption.

    When gptme appended a tomlkit ``Table`` to a generated ``providers = []``
    ``Array``, the resulting TOML was invalid (table key=value syntax inside
    square brackets).  Detect that exact pattern and rewrite the providers block
    as proper ``[[providers]]`` array-of-tables so the file parses again.

    On success, returns the repaired ``TOMLDocument`` in memory only. Loading
    must not overwrite a concurrent save or risk credentials during startup.
    An explicit config save will persist the recovered document.
    Returns ``None`` when the pattern is not recognised so
    the caller can re-raise the original ``TOMLKitError``.
    """
    # Match the corrupted block: providers = [<multi-line table entries>]
    # Valid empty arrays ("providers = []") and simple-value arrays won't match
    # because they don't contain "key =" inside the brackets. Consume complete
    # basic strings (including escapes) before looking for the closing bracket:
    # a value such as "token]#suffix" is not an array boundary. Comments also
    # consume to end-of-line, so their quotes and brackets cannot be syntax.
    match = re.search(
        r'^(providers\s*=\s*\[)((?:"(?:[^"\\]|\\.)*"|#[^\r\n]*|[^"\]#])*)(\])[ \t]*(?=#|\r?\n|$)',
        raw,
        re.DOTALL | re.MULTILINE,
    )
    if not match:
        return None

    # Ask TOML to determine the assignment's scope. A nested setting named
    # providers is not the root-level provider array written by the old saver.
    try:
        prefix = tomlkit.loads(raw[: match.start()] + "providers = []\n")
    except tomlkit.exceptions.TOMLKitError:
        return None
    if prefix.get("providers") != []:
        return None

    content = match.group(2)

    # Require at least one key = "value" pair inside the brackets.
    if not re.search(r'\w+\s*=\s*"', content):
        return None

    # Split into individual provider entries, respecting quoted strings.
    # A base_url like "http://host/v1?a=1,b=2" must not split at the inner comma.
    provider_texts = _split_outside_quotes(content)

    aot = tomlkit.aot()
    for ptext in provider_texts:
        ptext = ptext.strip().strip(",").strip()
        if not ptext:
            continue
        try:
            parsed = tomlkit.loads(f"[__p__]\n{ptext}\n")
        except tomlkit.exceptions.TOMLKitError:
            return None  # Unrecognised structure — don't attempt a lossy repair
        t = tomlkit.table()
        for k, v in parsed["__p__"].items():
            t.add(k, v)
        aot.append(t)

    if not aot:
        return None

    # Rebuild: remove the corrupted block and append the AoT at the end.
    # Putting [[providers]] at the end avoids absorbing any root-level settings
    # that follow the providers block into the last provider table.
    aot_doc = tomlkit.document()
    aot_doc.add("providers", aot)
    replacement = tomlkit.dumps(aot_doc).strip()

    pre = raw[: match.start()]
    post = raw[match.end() :]
    repaired = (pre + post).rstrip("\n") + "\n" + replacement + "\n"

    try:
        doc = tomlkit.loads(repaired)
    except tomlkit.exceptions.TOMLKitError:
        return None

    # Keep this on the document, not a path registry: a later explicit save or
    # external repair must not leave a stale cleanup prohibition for that path.
    doc._providers_recovered = True  # type: ignore[attr-defined]
    logger.warning(
        "Using repaired providers in memory only for %s. "
        "This was caused by a bug in an older version of gptme. "
        "Save your config explicitly to persist the repair; "
        "the original file has not been changed.",
        path,
    )
    return doc


def _is_private_config(path: Path) -> bool:
    """``config.local.toml`` is where secrets (API keys) go, so keep it owner-only."""
    return path.name == USER_CONFIG_SOURCE_LOCAL


# Config locks the current thread already holds, so a nested acquisition (a
# first-run create inside set_config_value) does not deadlock on its own flock.
_held_config_locks = threading.local()


@contextmanager
def _config_write_lock(path: str | Path) -> Iterator[None]:
    """Serialize read-modify-write cycles on one config file across processes.

    The lock is a sidecar next to the resolved file, so every name for the same
    config (e.g. a dotfiles symlink) shares it. Re-entrant within one thread;
    other threads and processes wait.
    """
    target = Path(path).resolve()
    held: set[str] = _held_config_locks.__dict__.setdefault("paths", set())
    if str(target) in held:
        yield
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f".{target.name}.lock")
    lock_file: IO[bytes]
    read_only_fallback = False
    try:
        lock_file = lock_path.open("a+b")
    except PermissionError:
        # A lock created by another user follows their umask, so a group-writable
        # config can come with a lock we may only read. flock(2) needs no write
        # access to take LOCK_EX, so fall back to a read-only descriptor instead
        # of failing the save on the lock; msvcrt does need write access.
        if fcntl is None:
            raise
        try:
            lock_file = lock_path.open("rb")
        except FileNotFoundError:
            # The parent directory is not writable and there is no sidecar to
            # read, so no lock can exist. Save unlocked rather than surfacing a
            # FileNotFoundError that names the lock file, not the config.
            logger.warning(
                f"Cannot create lock file {lock_path}; saving {target.name} "
                "without cross-process serialization"
            )
            yield
            return
        read_only_fallback = True
    locked = False
    with lock_file:
        if fcntl is not None:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
                locked = True
            except OSError as e:
                # Linux emulates flock over NFS with fcntl byte-range locks,
                # where LOCK_EX needs a descriptor opened for writing. The
                # read-only fallback above then fails with EBADF on exactly the
                # shared NFS home it targets. Degrade to an unlocked save with
                # a warning instead of aborting every config write there.
                if not read_only_fallback:
                    raise
                logger.warning(
                    f"Could not take an exclusive lock on {lock_path} ({e}); "
                    f"saving {target.name} without it"
                )
        elif msvcrt is not None:
            lock_file.seek(0)
            # LK_LOCK gives up with an error after about ten seconds, unlike
            # flock; poll the non-blocking lock so writers really wait.
            while True:
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as e:
                    if e.errno not in (errno.EACCES, errno.EDEADLK):
                        raise
                time.sleep(0.05)
        held.add(str(target))
        try:
            yield
        finally:
            held.discard(str(target))
            if fcntl is not None:
                # Nothing to release when the fallback degraded to unlocked.
                if locked:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)
            elif msvcrt is not None:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


# A ``PermissionError`` from ``os.replace`` is transient only on Windows, where
# CPython opens files without ``FILE_SHARE_DELETE``. On POSIX ``rename`` returns
# EPERM/EACCES deterministically (a sticky-bit directory, an immutable file), so
# retrying there would only delay a real error and then relabel it as a lock.
_replace_retries_transient_errors = os.name == "nt"


def _replace_with_retry(
    src: str | Path, dst: str | Path, *, attempts: int = 5, delay: float = 0.05
) -> None:
    """Atomically replace ``dst`` with ``src``, retrying transient Windows locks.

    On Windows, ``os.replace`` (``MoveFileEx``) fails with ``PermissionError``
    while another process has the destination open -- a reader, antivirus, or
    the search indexer. The previous in-place write succeeded in that window.
    Retry a few times before surfacing a clear error. Elsewhere the error is
    deterministic and is re-raised unchanged, keeping its type and ``errno`` so
    callers that handle permission failures still recognise it.
    """
    for attempt in range(attempts if _replace_retries_transient_errors else 1):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            if not _replace_retries_transient_errors:
                raise
            if attempt == attempts - 1:
                raise PermissionError(
                    f"Could not replace {dst}: it remained locked by another "
                    f"process after {attempts} attempts"
                ) from exc
            # Back off a little so a short-lived reader can finish.
            time.sleep(delay * (2**attempt))


def _publish_over_placeholder(staged: Path, target: Path) -> None:
    """Exclusively create ``target`` from ``staged`` without hard links.

    Call with the target's write lock held. Raises `FileExistsError` if the
    target exists. If publishing fails, the empty placeholder is removed: the
    next launch would otherwise take it as an existing config and skip writing
    the defaults.
    """
    os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    placeholder = os.stat(target)
    try:
        _replace_with_retry(staged, target)
    except BaseException:
        try:
            current = os.stat(target)
            if (current.st_dev, current.st_ino) == (
                placeholder.st_dev,
                placeholder.st_ino,
            ):
                os.unlink(target)
        except FileNotFoundError:
            pass
        raise


def _write_config_text(path: str | Path, text: str, *, exclusive: bool = False) -> bool:
    """Replace a config file with ``text`` without ever leaving it truncated.

    The new contents are staged in a temporary file beside the resolved
    destination (symlinks keep pointing at the same file) and renamed over it,
    so a failed write leaves the previous file intact. Existing permission bits
    and POSIX group ownership are kept. ``config.local.toml`` holds secrets: it
    is created 0600 and an existing one loses its group/other bits.

    This replaces the destination inode: hard links to it are not updated and
    extended metadata (ACLs, xattrs) on it is not carried over. A single-file
    bind mount cannot be replaced; mount the config directory instead. There
    is no in-place fallback, which would reintroduce truncation on write failure.

    With ``exclusive``, the file is only created if absent; returns False (and
    writes nothing) when it already exists.
    """
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    # A symlinked config.local.toml is private whatever its target is called.
    private = _is_private_config(Path(path)) or _is_private_config(target)
    try:
        destination_stat = target.stat()
        mode: int | None = stat.S_IMODE(destination_stat.st_mode)
    except FileNotFoundError:
        destination_stat = None
        mode = None
    if exclusive and mode is not None:
        return False
    if mode is not None and (not mode & 0o222 or not os.access(target, os.W_OK)):
        # Rename is authorized by the directory on POSIX, but a deliberately
        # read-only config must still refuse saves. Check before staging so a
        # Windows read-only staging file cannot mask the error during cleanup.
        # The mode check alone is not enough: in a group-writable directory
        # another user could replace a config they cannot write (the rename
        # needs only directory permission), where the old in-place write
        # raised PermissionError. Keep the mode test too so root still refuses
        # an explicitly 0444 file, which os.access would allow.
        raise PermissionError(errno.EACCES, "Config file is read-only", str(target))
    if mode is not None and private:
        mode &= 0o700
    elif mode is None and private:
        mode = 0o600

    staged = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    # The contents never sit in a file more open than the destination. Another
    # user who opens the staging file keeps that descriptor through a later
    # chmod, so whenever the final mode is known the file is created 0600 and
    # only gets its final mode once fully written. A new non-private file gets
    # the umask-filtered 0666 default it ends up with anyway.
    fd = os.open(
        staged,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600 if private or mode is not None else 0o666,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            if mode is not None:
                # A replacement inherits the directory/process group, not the
                # destination's. Restore it before granting group access, or a
                # retained 0640 mode could expose secrets to a different group.
                # Failure must leave the old file intact, not widen access.
                # Only when the group has access 'other' does not: the move is
                # pointless for 0600/0700, and for 0644 the group has no more
                # than 'other' already has. An unprivileged user cannot chown
                # to a group they are not in, so doing it anyway would fail an
                # otherwise valid save (a config restored from another machine
                # or a bind mount with a foreign gid).
                if (
                    _group_grants_more_than_other(mode)
                    and destination_stat is not None
                    and hasattr(os, "fchown")
                    and os.fstat(f.fileno()).st_gid != destination_stat.st_gid
                ):
                    os.fchown(f.fileno(), -1, destination_stat.st_gid)
                if hasattr(os, "fchmod"):
                    os.fchmod(f.fileno(), mode)
                else:  # pragma: no cover - Windows before 3.13
                    os.chmod(staged, mode)
            os.fsync(f.fileno())
        if exclusive:
            # Publish only if still absent: never replace a file another
            # process created after our existence check.
            try:
                if hasattr(os, "link"):
                    try:
                        os.link(staged, target)
                        staged.unlink()
                    except FileExistsError:
                        raise
                    except OSError:
                        # No hard links here (exFAT, some network mounts).
                        # Claim the name with an empty placeholder, which fails
                        # if the file appeared meanwhile, then replace our own
                        # placeholder. Readers only ever see empty or complete
                        # TOML. The write lock keeps a concurrent save from
                        # landing between the cleanup check and the unlink.
                        with _config_write_lock(target):
                            _publish_over_placeholder(staged, target)
                else:  # pragma: no cover - rename refuses existing targets there
                    os.rename(staged, target)
            except FileExistsError:
                staged.unlink()
                return False
        else:
            _replace_with_retry(staged, target)
        return True
    except BaseException:
        try:
            staged.unlink()
        except FileNotFoundError:
            pass
        raise


def _write_config_doc(path: str | Path, doc: "TOMLDocument | Container") -> None:
    """Serialize ``doc`` and atomically replace ``path`` (see `_write_config_text`)."""
    _write_config_text(path, tomlkit.dumps(doc))


def _initial_config_data(path: str) -> dict[str, Any]:
    """The content a config layer starts with when it has no content yet.

    The main config gets the built-in defaults written out. Built-in defaults
    written into an operator-supplied runtime layer would become explicit user
    overrides of it (notably empty lists), so that layer starts sparse. The
    local override layer always starts empty: defaults written there would
    shadow the main config instead of being overridden by it.
    """
    if _is_private_config(Path(path)) or _is_private_config(Path(path).resolve()):
        return {}
    if get_user_config_runtime_path(path).exists():
        return {}
    return _serialize_user_config(default_config)


def _load_config_doc(path: str | None = None) -> tomlkit.TOMLDocument:
    if path is None:
        path = config_path
    # Check if the config file exists
    if not os.path.exists(path):
        # If not, create it and write some default settings
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # A pre-existing runtime layer makes the built-in defaults its business,
        # not this file's: writing them here would turn runtime defaults into
        # explicit user overrides (notably empty lists).
        toml = tomlkit.dumps(_initial_config_data(path))
        if _write_config_text(path, toml, exclusive=True):
            logger.info(f"Created config file at {path}")
            return tomlkit.loads(toml)
        # Another process created it after our check; read theirs instead.
    text = _read_config_text(path)
    try:
        return tomlkit.loads(text)
    except tomlkit.exceptions.TOMLKitError:
        repaired = _repair_providers_corruption(text, path)
        if repaired is not None:
            return repaired
        raise


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
    else:
        local_path = None
        write_path = config_path

    # Hold the lock across read-modify-write so concurrent writers cannot drop
    # each other's updates. Reload only after releasing it (not re-entrant).
    with _config_write_lock(write_path):
        if local_path is not None:
            # Load existing local config or start empty (no defaults)
            doc: TOMLDocument | Container = (
                _load_config_doc(write_path)
                if local_path.exists()
                else tomlkit.document()
            )
        else:
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
                    raise ValueError(
                        f"Cannot set '{key}': '{k}' exists but is not a table"
                    )
            d = cast("Container", d[k])
        d[keypath[-1]] = value

        _back_up_before_reencoding(write_path)
        _write_config_doc(write_path, doc)

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
    else:
        local_path = None
        write_path = config_path

    with _config_write_lock(write_path):
        if local_path is not None:
            doc: TOMLDocument | Container = (
                _load_config_doc(write_path)
                if local_path.exists()
                else tomlkit.document()
            )
        else:
            doc = _load_config_doc()
        _add_provider_to_doc(doc, provider)
        _back_up_before_reencoding(write_path)
        _write_config_doc(write_path, doc)

    if reload:
        from .core import reload_config

        reload_config()


def _add_provider_to_doc(
    doc: "TOMLDocument | Container", provider: "ProviderConfig"
) -> None:
    """Insert or replace the ``[[providers]]`` entry named ``provider.name``."""
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
    elif not isinstance(doc["providers"], AoT):
        # Defaults use providers = []; valid inline arrays need conversion too.
        # Appending a regular table to a TOML Array would write invalid TOML.
        normalized = tomlkit.aot()
        for entry in doc["providers"]:
            table = tomlkit.table()
            table.update(entry)
            normalized.append(table)
        doc["providers"] = normalized

    providers = cast("AoT", doc["providers"])
    for idx, existing_provider in enumerate(providers):
        if existing_provider.get("name") == provider.name:
            providers[idx] = provider_table
            break
    else:
        providers.append(provider_table)


def _merge_config_data(
    main_config: dict, local_config: dict, *, _root: bool = True
) -> dict:
    """
    Merge local configuration into main configuration.

    For MCP servers, merge by name - local server env vars are merged into main server config.
    For other sections, local config extends/overrides main config.
    """

    merged = copy.deepcopy(main_config)

    for key, value in local_config.items():
        if key == "mcp" and isinstance(value, dict) and "servers" in value:
            # Special handling for MCP servers - merge by name. Existing mcp /
            # servers values may be malformed (user typo); don't crash the
            # merge before the later non-strict parse can skip them.
            if not isinstance(merged.get("mcp"), dict):
                merged["mcp"] = {}
            if not isinstance(merged["mcp"].get("servers"), list):
                merged["mcp"]["servers"] = []

            local_servers = value.get("servers", [])
            if not isinstance(local_servers, list):
                local_servers = []
            main_servers = merged["mcp"]["servers"]

            # Create a dict for quick lookup of main servers by name. Entries
            # missing a name (malformed config) can't be merged by name; leave
            # them as-is so the later parse step can warn/skip them cleanly
            # instead of this merge crashing on a raw KeyError.
            main_servers_by_name: dict = {}
            for server in main_servers:
                if isinstance(server, dict) and "name" in server:
                    main_servers_by_name[server["name"]] = server

            for local_server in local_servers:
                if not isinstance(local_server, dict):
                    main_servers.append(local_server)
                    continue
                server_name = local_server.get("name")
                if server_name and server_name in main_servers_by_name:
                    # Merge env vars from local into main server. A malformed
                    # same-name override must not poison (and then drop) an
                    # otherwise valid existing server — e.g. an unexpected
                    # key in local config deleting a main entry.
                    main_server = main_servers_by_name[server_name]
                    candidate = dict(main_server)
                    local_env = local_server.get("env")
                    if isinstance(local_env, dict):
                        base_env = (
                            dict(main_server["env"])
                            if isinstance(main_server.get("env"), dict)
                            else {}
                        )
                        base_env.update(local_env)
                        candidate["env"] = base_env
                    candidate.update(
                        {
                            k: v
                            for k, v in local_server.items()
                            if k not in ["name", "env"]
                        }
                    )
                    if not _mcp_server_entry_constructs(candidate):
                        logger.warning(
                            f"Skipping malformed override for MCP server "
                            f"{server_name!r}; keeping the existing entry"
                        )
                        continue
                    main_server.clear()
                    main_server.update(candidate)
                else:
                    # Add new server from local config
                    main_servers.append(local_server)

            # Merge other MCP config properties (enabled, auto_start)
            for mcp_key, mcp_value in value.items():
                if mcp_key != "servers":
                    merged["mcp"][mcp_key] = mcp_value

        elif _root and key == "providers":
            # Only root providers use the provider schema; plugin settings
            # named providers retain ordinary recursive override semantics.
            # Skip malformed containers before merge-by-name, just as the
            # tolerant user parser does. Keep partial entries for name-based
            # overrides; they need not construct a ProviderConfig on their own.
            if not isinstance(value, list):
                _parse_providers(value, strict=False)
                continue
            if key in merged and not isinstance(merged[key], list):
                _parse_providers(merged[key], strict=False)
                merged[key] = []
            elif key not in merged:
                merged[key] = []
            # Local entries update matching main providers; new ones append.
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
                    # A malformed same-name override must not poison (and then
                    # drop) an otherwise valid existing provider — e.g. an
                    # unexpected key in user config deleting a runtime entry.
                    candidate = {**main_by_name[name], **local_provider}
                    if not _provider_entry_constructs(candidate):
                        logger.warning(
                            f"Skipping malformed override for provider {name!r}; "
                            "keeping the existing entry"
                        )
                        continue
                    main_by_name[name].update(local_provider)
                else:
                    main_providers.append(local_provider)
        elif (
            isinstance(value, dict) and key in merged and isinstance(merged[key], dict)
        ):
            # Recursive merge for nested dictionaries
            merged[key] = _merge_config_data(merged[key], value, _root=False)
        else:
            # Direct override for other keys
            merged[key] = value

    return merged
