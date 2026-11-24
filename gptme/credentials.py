"""Persistent credential storage for provider API keys."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import tomlkit
from tomlkit.exceptions import TOMLKitError

STORED_CREDENTIALS_SOURCE = "credentials.toml"

logger = logging.getLogger(__name__)


def get_credentials_dir() -> Path:
    """Directory holding the credential store. Pure lookup: creates nothing."""
    # An empty, padded or relative XDG_CONFIG_HOME is invalid per the XDG spec;
    # using it as-is would put the API key file under the current directory.
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else None
    if base is None or not base.is_absolute():
        base = Path.home() / ".config"
    return base / "gptme"


def _get_credentials_path() -> Path:
    """Return the path to gptme's persisted credential store."""
    config_dir = get_credentials_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / STORED_CREDENTIALS_SOURCE


def _load_credentials_doc(path: Path | None = None, *, strict: bool = False):
    path = path or _get_credentials_path()
    if not path.exists():
        return tomlkit.document()
    with open(path, encoding="utf-8") as f:
        try:
            return tomlkit.load(f)
        except TOMLKitError as e:
            # Readers must not take down provider discovery (and doctor) over a
            # hand-edited file; writers must not silently replace it.
            if strict:
                raise ValueError(f"Cannot parse {path}: {e}") from e
            logger.warning(f"Ignoring unparseable credentials file {path}: {e}")
            return tomlkit.document()


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
    doc = _load_credentials_doc(path, strict=True)
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
