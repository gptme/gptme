"""Project configuration loading.

Handles loading project-level configuration from gptme.toml files
in workspace directories, with caching and local config merging.
"""

import logging
from functools import lru_cache
from pathlib import Path

import tomlkit

from ..util import path_with_tilde
from .models import ProjectConfig
from .user import _merge_config_data

logger = logging.getLogger(__name__)


# Track which workspaces we've logged config loading for (to avoid duplicate logs)
_config_logged_workspaces: set[Path] = set()


def get_project_config(
    workspace: Path | None, *, quiet: bool = False
) -> ProjectConfig | None:
    """
    Get a cached copy of or load the project configuration from a gptme.toml file in the workspace or .github directory.

    Args:
        workspace: Path to the workspace directory
        quiet: If True, suppress log messages (useful for metadata lookups)

    Run :func:`reload_config` or :func:`Config.from_workspace` to reset cache and reload the project config.
    """
    if workspace is None:
        return None

    # Compute file mtimes for cache invalidation
    # This way, if the config file is modified, the cache is automatically busted
    config_candidates = (
        workspace / "gptme.toml",
        workspace / ".github" / "gptme.toml",
    )
    mtimes: list[float] = []
    for p in config_candidates:
        try:
            mtimes.append(p.stat().st_mtime)
        except OSError:
            mtimes.append(0)
    # Also check local config mtime
    for p in config_candidates:
        local = p.parent / "gptme.local.toml"
        try:
            mtimes.append(local.stat().st_mtime)
        except OSError:
            mtimes.append(0)

    # Get cached result (includes paths for logging)
    result = _get_project_config_cached(workspace, tuple(mtimes))
    if result is None:
        return None

    config, config_path, local_config_path = result

    # Log only on first non-quiet access per workspace
    if not quiet and workspace not in _config_logged_workspaces:
        _config_logged_workspaces.add(workspace)
        logger.info(f"Using project configuration at {path_with_tilde(config_path)}")
        if local_config_path:
            logger.info(
                f"Using local configuration from {path_with_tilde(local_config_path)}"
            )

    return config


@lru_cache(maxsize=4)
def _get_project_config_cached(
    workspace: Path,
    _mtimes: tuple[float, ...] = (),
) -> tuple[ProjectConfig, Path, Path | None] | None:
    """Internal cached implementation of get_project_config.

    Args:
        workspace: Path to the workspace directory
        _mtimes: File modification times used as cache key for invalidation

    Returns:
        Tuple of (config, config_path, local_config_path) or None if no config found.
        local_config_path is None if no local config exists.
    """
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
        # load project config
        with open(project_config_path) as f:
            config_data = tomlkit.load(f).unwrap()

        # Look for local config file in the same directory
        local_config_path = project_config_path.parent / "gptme.local.toml"
        used_local_config_path: Path | None = None
        if local_config_path.exists():
            used_local_config_path = local_config_path
            with open(local_config_path) as f:
                local_config_data = tomlkit.load(f).unwrap()

            # Merge local config into main config
            config_data = _merge_config_data(config_data, local_config_data)

        config = ProjectConfig.from_dict(config_data, workspace=workspace)
        return (config, project_config_path, used_local_config_path)
    return None
