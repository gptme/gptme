"""Resolved Git executable path for internal subprocess calls.

Using an absolute path prevents CWD-based executable hijacking on native
Windows, where CreateProcess can search the current directory before PATH.
"""

import shutil

GIT_CMD: str = shutil.which("git") or "git"
