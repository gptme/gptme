"""Loading of the user's own ``.env`` file."""

import sys
from pathlib import Path

from dotenv import load_dotenv


def _candidates() -> list[Path]:
    home_env = Path.home() / ".env"
    config_env = Path.home() / ".config" / "gptme" / ".env"
    if getattr(sys, "frozen", False):
        return [home_env, config_env]
    # The directory holding the gptme package: the checkout, for a source install.
    package_root = Path(__file__).resolve().parents[2]
    candidates = [package_root / ".env"]
    # Always fall back to the user's files. /usr/local and Homebrew installs
    # live outside $HOME, so a home-relative check would skip them.
    for extra in (home_env, config_env):
        if extra not in candidates:
            candidates.append(extra)
    return candidates


def load_user_dotenv() -> None:
    """Load the user's ``.env`` file, never one belonging to a workspace.

    Consider the gptme source checkout, then ``~/.env``, then
    ``~/.config/gptme/.env``. Do not replace this with a bare ``load_dotenv()``:
    that searches upwards from the working directory in frozen builds, REPLs and
    debuggers, and from a project virtualenv it reaches the project's own
    ``.env``. A repository must not set credentials, endpoints or trust settings
    that way.
    """
    for path in _candidates():
        if path.is_file() or path.is_fifo():
            load_dotenv(path)
            return
