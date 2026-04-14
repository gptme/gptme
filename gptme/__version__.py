import importlib.metadata
import os.path
import subprocess

_cached_version: str | None = None


def get_git_version(package_dir):
    """Get version information from git."""
    try:

        def git_cmd(cmd):
            return subprocess.check_output(
                cmd, cwd=package_dir, text=True, timeout=10
            ).strip()

        if (
            subprocess.call(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=package_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            == 0
        ):
            tags = git_cmd(["git", "tag", "--list", "v*", "--sort=-v:refname"])
            if tags:
                latest_tag = tags.split("\n")[0]
                version = latest_tag.lstrip("v")
                commit_hash = git_cmd(["git", "rev-parse", "--short", "HEAD"])
                is_dirty = bool(git_cmd(["git", "status", "--porcelain"]))
                version += f"+{commit_hash}"
                if is_dirty:
                    version += ".dirty"
                return version
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ):
        pass
    return None


def _compute_version() -> str:
    """Compute version string. Called lazily on first access of __version__."""
    try:
        version = importlib.metadata.version("gptme")
        git_hash = None

        # Method 1: Check direct_url.json (for pip installs from git)
        try:
            dist = importlib.metadata.distribution("gptme")
            if hasattr(dist, "read_text"):
                direct_url_json = dist.read_text("direct_url.json")
                if direct_url_json:
                    import json

                    direct_url_data = json.loads(direct_url_json)
                    if "vcs_info" in direct_url_data:
                        git_hash = direct_url_data["vcs_info"].get("commit_id", "")[:8]
        except (KeyError, AttributeError, TypeError, ValueError, FileNotFoundError):
            pass

        # Method 2: Try git command (for editable installs)
        if not git_hash:
            is_editable = isinstance(
                importlib.metadata.distribution("gptme"),
                importlib.metadata.PathDistribution,
            )
            if is_editable:
                package_dir = os.path.dirname(os.path.abspath(__file__))
                git_version = get_git_version(package_dir)
                if git_version:
                    return git_version
                return version + "+unknown"

        # Apply git hash if found via direct_url.json
        if git_hash:
            version = f"{version}+{git_hash}"
            package_dir = os.path.dirname(os.path.abspath(__file__))
            git_version = get_git_version(package_dir)
            if git_version and ".dirty" in git_version:
                version += ".dirty"

        return version

    except importlib.metadata.PackageNotFoundError:
        return "0.0.0 (unknown)"


def __getattr__(name: str):
    if name == "__version__":
        global _cached_version
        if _cached_version is None:
            _cached_version = _compute_version()
        globals()["__version__"] = _cached_version
        return _cached_version
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    print(_compute_version())
