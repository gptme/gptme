import importlib.metadata
import os.path
import subprocess


def get_git_version(package_dir):
    """Get version information from git."""
    try:
        # Run git commands
        def git_cmd(cmd):
            return subprocess.check_output(cmd, cwd=package_dir, text=True).strip()

        # Check if we're in a git repo
        if (
            subprocess.call(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=package_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            == 0
        ):
            # List all version tags and get the latest one
            tags = git_cmd(["git", "tag", "--list", "v*", "--sort=-v:refname"])
            if tags:
                latest_tag = tags.split("\n")[0]  # Get first tag (latest due to sort)
                version = latest_tag.lstrip("v")  # Remove 'v' prefix

                # Get commit hash
                commit_hash = git_cmd(["git", "rev-parse", "--short", "HEAD"])

                # Check if working tree is dirty
                is_dirty = bool(git_cmd(["git", "status", "--porcelain"]))

                version += f"+{commit_hash}"
                if is_dirty:
                    version += ".dirty"
                return version
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return None


try:
    __version__ = importlib.metadata.version("gptme")

    # Try multiple methods to get git version info
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
    except Exception:
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
                __version__ = git_version
            else:
                __version__ += "+unknown"

    # Apply git hash if found
    if git_hash:
        __version__ = f"{__version__}+{git_hash}"
        # Add .dirty suffix if in development
        package_dir = os.path.dirname(os.path.abspath(__file__))
        if get_git_version(package_dir) and ".dirty" in get_git_version(package_dir):
            __version__ += ".dirty"

except importlib.metadata.PackageNotFoundError:
    __version__ = "0.0.0 (unknown)"

if __name__ == "__main__":
    print(__version__)
