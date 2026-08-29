import logging
import os
import shutil
import struct
import subprocess
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir, user_state_dir

from .util.git_cmd import GIT_CMD

logger = logging.getLogger(__name__)


def _get_env_path(var: str) -> str | None:
    """Return the env-var value, or None if unset or empty/whitespace.

    Treats an empty or whitespace-only value the same as "not set" —
    this is intentional: empty XDG_* vars (common in Docker or misconfigured
    environments) must not produce relative paths like ``Path("") / "gptme"``.

    Surrounding whitespace is stripped. ``Path(" /tmp/foo")`` is relative
    (it does not start with ``/``), so a leading space on ``XDG_DATA_HOME``
    would otherwise write data under CWD. Accidental padding is the
    misconfiguration this helper exists to tolerate.
    """
    stripped = os.environ.get(var, "").strip()
    return stripped or None


def get_config_dir() -> Path:
    return Path(user_config_dir("gptme"))


def get_readline_history_file() -> Path:
    return get_data_dir() / "history"


def get_pt_history_file() -> Path:
    return get_data_dir() / "history.pt"


def get_data_dir() -> Path:
    # used in testing, so must take precedence
    if xdg := _get_env_path("XDG_DATA_HOME"):
        return Path(xdg) / "gptme"

    # just a workaround for me personally
    old = Path("~/.local/share/gptme").expanduser()
    if old.exists():
        return old

    return Path(user_data_dir("gptme"))


def get_state_dir() -> Path:
    """Get the path for **transient state** (XDG_STATE_HOME).

    Used for recovery artifacts and other state that should persist between
    invocations but is not important enough to back up — checkpoints, etc.
    """
    if xdg := _get_env_path("XDG_STATE_HOME"):
        return Path(xdg) / "gptme"
    return Path(user_state_dir("gptme"))


def get_logs_dir() -> Path:
    """Get the path for **conversation logs** (not to be confused with the logger file)"""
    if logs_home := _get_env_path("GPTME_LOGS_HOME"):
        path = Path(logs_home)
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
    while True:
        if (path / "gptme.toml").exists():
            return path
        if path == path.parent:  # reached the filesystem root (cross-platform)
            break
        path = path.parent

    # if no gptme.toml file was found, look for a git repo
    return _get_project_git_dir_walk()


def get_project_git_dir() -> Path | None:
    return _get_project_git_dir_walk()


def _get_project_git_dir_walk() -> Path | None:
    # if no gptme.toml file was found, look for a git repo
    path = Path.cwd()
    while True:
        if (path / ".git").exists():
            return path
        if path == path.parent:  # reached the filesystem root (cross-platform)
            return None
        path = path.parent


def _get_project_git_dir_call() -> Path | None:
    try:
        projectdir = subprocess.run(
            [GIT_CMD, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
        return Path(projectdir)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def get_workspace() -> Path:
    """Get the agent workspace directory.

    Detection order:
    1. GPTME_WORKSPACE environment variable (ignored if empty)
    2. Git root, traversing to parent repo if in a submodule
    3. Current working directory

    Handles git submodules: if `.git` is a file (not a directory),
    we're in a submodule and the parent repo root is returned instead.
    """
    if workspace := _get_env_path("GPTME_WORKSPACE"):
        return Path(workspace)

    try:
        result = subprocess.run(
            [GIT_CMD, "rev-parse", "--show-toplevel"],
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
                        [GIT_CMD, "rev-parse", "--show-superproject-working-tree"],
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
        profile_name: Name of the agent profile (e.g. 'explorer', 'researcher').
            Must be a non-empty path component other than ``.`` or ``..``.

    Returns:
        Path to the memory directory (created if it doesn't exist)

    Raises:
        ValueError: If ``profile_name`` is not a safe single path component.
    """
    if (
        not profile_name
        or profile_name in {".", ".."}
        or "/" in profile_name
        or "\\" in profile_name
        or "\n" in profile_name
        or "\r" in profile_name
        or "\0" in profile_name
        or Path(profile_name).name != profile_name
    ):
        raise ValueError(
            f"Invalid profile name {profile_name!r}: must be a safe path component."
        )
    path = get_data_dir() / "memories" / "profiles" / profile_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _claude_project_dirname(path: str) -> str:
    """Replicate Claude Code's cwd → project-directory-name encoding.

    Mirrors CC's own implementation (bundle functions ``wv`` / ``pL`` / ``y9t``
    in ``cli.js`` — also in the VS Code extension's ``extension.js`` — verified
    against v2.1.239)::

        sanitize(e) = e.replace(/[^a-zA-Z0-9]/g, "-")
        dirname(e)  = sanitize(e).length <= 200
                          ? sanitize(e)
                          : sanitize(e).slice(0, 200) + "-" + hash(e)
        hash(e)     = Math.abs(e.split("").reduce(
                          (t, c) => (t * 31 + c.charCodeAt(0)) | 0, 0)).toString(36)

    Every non-alphanumeric character (``/``, ``\\``, ``:``, ``_``, ``.``,
    spaces, non-ASCII, ...) becomes a dash, with no collapsing of runs — so
    ``C:\\`` → ``C--``. Overlong names are truncated and disambiguated with a
    hash of the *original* path.

    CC runs on JS, where strings, ``.length``, ``.charCodeAt()`` and the regex
    all operate on UTF-16 code units. We iterate the same units so that paths
    with astral characters (emoji, ...) — two units each, ``"--"`` when
    sanitized — encode identically.
    """
    # 16-bit code units of the string, matching JS string/charCodeAt semantics.
    # An empty path yields no units (struct.unpack("<0H", b"") -> ()) and an
    # empty dirname, same as JS.
    utf16 = path.encode("utf-16-le", errors="surrogatepass")
    units = struct.unpack(f"<{len(utf16) // 2}H", utf16)

    def _is_alnum_unit(u: int) -> bool:
        return 48 <= u <= 57 or 65 <= u <= 90 or 97 <= u <= 122

    sanitized = "".join(chr(u) if _is_alnum_unit(u) else "-" for u in units)
    if len(sanitized) <= 200:
        return sanitized

    # CC's y9t(): t = (t * 31 + codeUnit), coerced to a signed int32 after each
    # step (JS `| 0`), then Math.abs(...).toString(36). Not canonical djb2
    # (which multiplies by 33) — this is the 31-multiplier variant CC ships.
    h = 0
    for u in units:
        h = (h * 31 + u) & 0xFFFFFFFF
        if h >= 0x80000000:
            h -= 0x100000000
    n = abs(h)
    if n == 0:
        suffix = "0"
    else:
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        chars = []
        while n:
            n, r = divmod(n, 36)
            chars.append(digits[r])
        suffix = "".join(reversed(chars))
    return sanitized[:200] + "-" + suffix


def get_cc_memory_dir(workspace: Path) -> Path:
    """Get the Claude Code memory directory for a given workspace.

    Claude Code stores per-project memories at:
        ~/.claude/projects/<workspace-hash>/memory/

    where ``<workspace-hash>`` is the absolute workspace path encoded via
    :func:`_claude_project_dirname` (every non-alphanumeric character replaced
    by a dash), e.g. ``/home/user/my_project`` → ``-home-user-my-project`` and
    ``C:\\Users\\user\\project`` → ``C--Users-user-project``.

    This allows gptme sessions to read memories written by CC sessions (and vice
    versa when the memory tool writes to this location).

    Args:
        workspace: Absolute path to the workspace root

    Returns:
        Path to the CC memory directory (may not exist)

    Note:
        The encoding is non-injective: paths that differ only by a dash versus
        any other non-alphanumeric character (e.g. ``/a/b``, ``/a-b`` and
        ``/a_b``) map to the same identifier. This is CC's own encoding scheme;
        gptme replicates it faithfully so it reads the correct memory directory.
        The collision risk is inherited from CC's design and cannot be resolved
        without diverging from CC's path formula.
    """
    workspace_hash = _claude_project_dirname(str(workspace.resolve()))
    return Path.home() / ".claude" / "projects" / workspace_hash / "memory"


def get_cc_memory_file(workspace: Path) -> Path:
    """Get the Claude Code MEMORY.md file path for a given workspace.

    Args:
        workspace: Absolute path to the workspace root

    Returns:
        Path to MEMORY.md inside the CC memory directory (may not exist)
    """
    return get_cc_memory_dir(workspace) / "MEMORY.md"


def _migrate_readline_history():
    """Migrate readline history from config dir to data dir."""
    old_path = get_config_dir() / "history"
    new_path = get_data_dir() / "history"
    if old_path.exists() and not new_path.exists():
        try:
            logger.info(f"Migrating readline history: {old_path} -> {new_path}")
            shutil.move(str(old_path), str(new_path))
        except OSError as e:
            logger.warning(f"Failed to migrate readline history: {e}")


def _init_paths():
    # create all paths
    for path in [get_config_dir(), get_data_dir(), get_logs_dir()]:
        path.mkdir(parents=True, exist_ok=True)

    _migrate_readline_history()


# run once on init
_init_paths()
