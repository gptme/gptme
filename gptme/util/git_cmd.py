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

# Repository-local execution sinks that fire during index refresh:
# core.fsmonitor (FS event hook), core.sshCommand (used by some transports
# during ls-files), diff.external (external diff tool), core.hooksPath
# (pre-commit / post-checkout hooks).  Setting them to empty / /dev/null
# suppresses execution without affecting the output of read-only commands.
_INSPECT_SAFE_FLAGS: list[str] = [
    "-c",
    "core.fsmonitor=",
    "-c",
    "core.sshCommand=cat",
    "-c",
    "diff.external=",
    "-c",
    "core.hooksPath=/dev/null",
]


def git_inspect_cmd() -> list[str]:
    """Return a git invocation prefix that strips repo-local execution sinks.

    Use this instead of ``[GIT_CMD]`` for read-only context-gathering calls
    (``git status``, ``git diff --name-only``, ``git ls-files``).  Write paths
    and trusted-workspace hooks (commit, push) should keep using ``GIT_CMD``
    directly so legitimate user hooks are not suppressed.

    Defends against the GitSpawn class of attack (Manifold Security, 2026-09-01):
    a repository delivered as a directory (zip, USB, shared folder) may carry a
    ``.git/config`` that sets ``core.fsmonitor`` or similar to an attacker
    payload; index-refresh triggered by ``git status`` executes that payload
    before any approval prompt.
    """
    return [GIT_CMD, *_INSPECT_SAFE_FLAGS]
