"""Utilities for detecting installation environment and generating install commands."""

import os
import sys


def detect_install_environment() -> str:
    """
    Detect how gptme is installed.

    Returns:
        Environment type: 'pipx', 'uvx', 'venv', or 'system'
    """
    # Check for pipx
    if os.environ.get("PIPX_HOME") or "pipx/venvs" in sys.prefix:
        return "pipx"

    # Check for uvx (comprehensive cross-platform detection to avoid false positives)
    if (
        "/.uv/" in sys.prefix
        or "\\.uv\\" in sys.prefix
        or "/uv/" in sys.prefix
        or "\\uv\\" in sys.prefix
        or sys.prefix.endswith("/.uv")
        or sys.prefix.endswith("\\.uv")
        or sys.prefix.endswith("/uv")
        or sys.prefix.endswith("\\uv")
        or os.environ.get("UV_HOME")
    ):
        return "uvx"

    # Check for virtualenv
    if sys.prefix != sys.base_prefix:
        return "venv"

    return "system"


def get_package_install_command(package: str, env_type: str | None = None) -> str:
    """
    Get the install command for a package based on the current environment.

    Args:
        package: Package name (e.g., 'questionary' or 'gptme[browser]')
        env_type: Environment type ('pipx', 'uvx', 'venv', 'system').
                  If None, auto-detects using detect_install_environment()

    Returns:
        Installation command string appropriate for the environment

    Examples:
        >>> get_package_install_command('questionary')  # in pipx env
        'pipx inject gptme questionary'
        >>> get_package_install_command('questionary')  # in venv
        'pip install questionary'
    """
    if env_type is None:
        env_type = detect_install_environment()

    if env_type == "pipx":
        return f"pipx inject gptme {package}"
    elif env_type == "uvx":
        # uvx runs in ephemeral environments, use uv pip with --system
        return f"uv pip install {package}"
    elif env_type == "venv":
        return f"pip install {package}"
    else:  # system
        return f"pip install --user {package}"
