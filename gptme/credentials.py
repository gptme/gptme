"""Persistent credential storage for provider API keys."""

from __future__ import annotations

import os
from pathlib import Path

import tomlkit

STORED_CREDENTIALS_SOURCE = "credentials.toml"


def _get_credentials_path() -> Path:
    """Return the path to gptme's persisted credential store."""
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    config_dir = config_dir / "gptme"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / STORED_CREDENTIALS_SOURCE


def _load_credentials_doc(path: Path | None = None):
    path = path or _get_credentials_path()
    if not path.exists():
        return tomlkit.document()
    with open(path) as f:
        return tomlkit.load(f)


def get_stored_api_key(provider: str) -> str | None:
    """Return the stored API key for a provider, if present."""
    doc = _load_credentials_doc()
    providers = doc.get("providers")
    if providers is None or not hasattr(providers, "get"):
        return None
    api_key = providers.get(provider)
    return api_key if isinstance(api_key, str) and api_key else None


def list_stored_credentials() -> list[tuple[str, str]]:
    """List stored provider credentials as ``(provider, api_key)`` tuples."""
    doc = _load_credentials_doc()
    providers = doc.get("providers")
    if providers is None or not hasattr(providers, "items"):
        return []
    return sorted(
        (str(provider), api_key)
        for provider, api_key in providers.items()
        if isinstance(api_key, str) and api_key
    )


def set_stored_api_key(provider: str, api_key: str) -> Path:
    """Persist an API key for ``provider`` and return the storage path."""
    path = _get_credentials_path()
    doc = _load_credentials_doc(path)
    providers = doc.get("providers")
    if providers is None or not hasattr(providers, "__setitem__"):
        providers = tomlkit.table()
        doc["providers"] = providers
    providers[provider] = api_key
    # Create with 0o600, then tighten existing files before writing new secrets.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    if hasattr(os, "fchmod"):
        os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        tomlkit.dump(doc, f)
    if not hasattr(os, "fchmod"):
        path.chmod(0o600)
    return path


def mask_secret(secret: str) -> str:
    """Render a short preview of a secret without exposing the full value."""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"


# This opt-in selector is read only from the launch environment, never config.
SCOPED_OPENROUTER_FILE = "GPTME_OPENROUTER_API_KEY_FILE"


def scoped_inference_enabled() -> bool:
    """Whether the launcher selected explicit OpenRouter-only delivery."""
    return SCOPED_OPENROUTER_FILE in os.environ


def require_scoped_provider(provider: str) -> None:
    """Do not silently switch to another credential source in scoped mode."""
    if scoped_inference_enabled() and provider != "openrouter":
        raise ValueError("Scoped inference requires openrouter")


def read_scoped_openrouter_key() -> str:
    """Read one protected credential into the inference client, not os.environ.

    This reduces accidental inheritance, not same-user filesystem authority.
    The launcher owns file provisioning (e.g. a service credential); gptme never
    retrieves a vault or falls back to ambient/config/stored keys here.
    """
    import stat

    try:
        path = os.environ[SCOPED_OPENROUTER_FILE]
        # Nonblocking open avoids hanging on a mistakenly provisioned FIFO.
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError
        key = raw.decode("utf-8").strip()
        if not key or any(ch.isspace() or ord(ch) < 32 for ch in key):
            raise ValueError
        return key
    except (OSError, KeyError, ValueError):
        # No path, bytes or nested decoder exception in user-visible errors.
        raise ValueError("Scoped OpenRouter credential unavailable") from None
