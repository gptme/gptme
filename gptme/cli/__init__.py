"""
CLI module - isolates interface/UX from core logic.

This module contains all CLI commands and related utilities:
- main: The main `gptme` command
- doctor: The `gptme-doctor` diagnostic command
- onboard: The `gptme-onboard` setup wizard
- setup: Setup utilities for shell completions, project config, etc.
- util: The `gptme-util` utility command with subcommands

Import functions from submodules directly to avoid circular imports:
    from gptme.cli.main import main
    from gptme.cli.doctor import main as doctor_main
"""


def __getattr__(name: str):
    """Lazy import to avoid circular dependencies."""
    if name == "main":
        from .main import main as _main

        return _main
    elif name == "doctor_main":
        from .doctor import main as doctor_main

        return doctor_main
    elif name == "onboard_main":
        from .onboard import main as onboard_main

        return onboard_main
    elif name == "util_main":
        from .util import main as util_main

        return util_main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["main", "doctor_main", "onboard_main", "util_main"]
