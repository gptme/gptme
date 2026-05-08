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
    # Use os.open with mode 0o600 at creation time to avoid a world-readable window.
    # open(path, "w") would create the file with umask permissions first, then chmod.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        tomlkit.dump(doc, f)
    return path


def mask_secret(secret: str) -> str:
    """Render a short preview of a secret without exposing the full value."""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"
