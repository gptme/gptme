"""Resolved Git executable path for internal subprocess calls.

Using an absolute path prevents CWD-based executable hijacking on native
Windows, where CreateProcess searches the current directory before PATH
when given a bare executable name.
"""

import os
import shutil
import sys


def _resolve_git_cmd() -> str:
    """Resolve git to a safe absolute path.

    On Windows, PATH entries of '' or '.' both map to the process CWD; if such
    an entry appears before a real git installation shutil.which() would return
    an absolute path that still points into the current directory.  The bare
    'git' fallback re-introduces CreateProcess CWD lookup.  Both are mitigated
    here by stripping CWD from the candidate directories before searching.
    """
    if sys.platform == "win32":
        cwd = os.path.normcase(os.path.abspath(os.getcwd()))
        safe_dirs = [
            d
            for d in os.get_exec_path()
            if os.path.normcase(os.path.abspath(d or ".")) != cwd
        ]
        resolved = shutil.which("git", path=os.pathsep.join(safe_dirs))
        if resolved is not None:
            return resolved
        # git not found in any safe PATH directory — callers that run on
        # Windows without a standard git installation accept the residual risk.
        return "git"
    return shutil.which("git") or "git"


GIT_CMD: str = _resolve_git_cmd()
