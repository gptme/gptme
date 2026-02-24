import logging
import os
import shutil
import subprocess
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

logger = logging.getLogger(__name__)


def get_config_dir() -> Path:
    return Path(user_config_dir("gptme"))


def get_readline_history_file() -> Path:
    return get_data_dir() / "history"


def get_pt_history_file() -> Path:
    return get_data_dir() / "history.pt"


def get_data_dir() -> Path:
    # used in testing, so must take precedence
    if "XDG_DATA_HOME" in os.environ:
        return Path(os.environ["XDG_DATA_HOME"]) / "gptme"

    # just a workaround for me personally
    old = Path("~/.local/share/gptme").expanduser()
    if old.exists():
        return old

    return Path(user_data_dir("gptme"))


def get_logs_dir() -> Path:
    """Get the path for **conversation logs** (not to be confused with the logger file)"""
    if "GPTME_LOGS_HOME" in os.environ:
        path = Path(os.environ["GPTME_LOGS_HOME"])
    else:
        path = get_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_project_gptme_dir() -> Path | None:
    """
    Walks up the directory tree from the working dir to find the project root,
    which is a directory containing a `gptme.toml` file.
    Or if none exists, the first parent directory with a git repo.

    Meant to be used in scripts/tools to detect a suitable location to store agent data/logs.
    """
    path = Path.cwd()
    while path != Path("/"):
        if (path / "gptme.toml").exists():
            return path
        path = path.parent

    # if no gptme.toml file was found, look for a git repo
    return _get_project_git_dir_walk()


def get_project_git_dir() -> Path | None:
    return _get_project_git_dir_walk()


def _get_project_git_dir_walk() -> Path | None:
    # if no gptme.toml file was found, look for a git repo
    path = Path.cwd()
    while path != Path("/"):
        if (path / ".git").exists():
            return path
        path = path.parent
    return None


def _get_project_git_dir_call() -> Path | None:
    try:
        projectdir = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return Path(projectdir)
    except subprocess.CalledProcessError:
        return None


def get_workspace() -> Path:
    """Get the agent workspace directory.

    Detection order:
    1. GPTME_WORKSPACE environment variable
    2. Git root, traversing to parent repo if in a submodule
    3. Current working directory

    Handles git submodules: if `.git` is a file (not a directory),
    we're in a submodule and the parent repo root is returned instead.
    """
    if workspace := os.environ.get("GPTME_WORKSPACE"):
        return Path(workspace)

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            git_root = Path(result.stdout.strip())
            # If .git is a file, we're in a submodule — find the parent repo
            if (git_root / ".git").is_file():
                try:
                    super_result = subprocess.run(
                        ["git", "rev-parse", "--show-superproject-working-tree"],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if super_result.returncode == 0 and super_result.stdout.strip():
                        return Path(super_result.stdout.strip())
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass
            return git_root
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return Path.cwd()


def get_profile_memory_dir(profile_name: str) -> Path:
    """Get the persistent memory directory for an agent profile.

    Each profile gets its own memory directory where the subagent can store
    learnings that persist across invocations. The primary file is MEMORY.md.

    Args:
        profile_name: Name of the agent profile (e.g. 'explorer', 'researcher')

    Returns:
        Path to the memory directory (created if it doesn't exist)
    """
    path = get_data_dir() / "profile-memories" / profile_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _migrate_readline_history():
    """Migrate readline history from config dir to data dir."""
    old_path = get_config_dir() / "history"
    new_path = get_data_dir() / "history"
    if old_path.exists() and not new_path.exists():
        try:
            logger.info(f"Migrating readline history: {old_path} -> {new_path}")
            shutil.move(str(old_path), str(new_path))
        except Exception as e:
            logger.warning(f"Failed to migrate readline history: {e}")


def _init_paths():
    # create all paths
    for path in [get_config_dir(), get_data_dir(), get_logs_dir()]:
        path.mkdir(parents=True, exist_ok=True)

    _migrate_readline_history()


# run once on init
_init_paths()
