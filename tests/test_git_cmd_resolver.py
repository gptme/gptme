"""Tests for the Git executable resolver (hardening against Windows CWD hijack)."""

import importlib
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def test_git_cmd_is_string():
    """GIT_CMD is a non-empty string."""
    from gptme.util.git_cmd import GIT_CMD

    assert isinstance(GIT_CMD, str)
    assert GIT_CMD


def test_git_cmd_is_absolute_when_git_available():
    """When git is on PATH, GIT_CMD resolves to an absolute path.

    This is the core of the hardening: on native Windows CreateProcess searches
    CWD before PATH for bare executable names.  An absolute path bypasses that.
    """
    if shutil.which("git") is None:
        pytest.skip("git not available on PATH")

    from gptme.util.git_cmd import GIT_CMD

    # The resolved path must be absolute so CreateProcess on Windows cannot
    # select a CWD-local git.exe instead of the system git.
    assert Path(GIT_CMD).is_absolute(), (
        f"GIT_CMD={GIT_CMD!r} is not absolute — bare executable names are "
        "vulnerable to CWD-hijack on native Windows"
    )


def test_git_cmd_fallback_when_git_missing():
    """When git is not on PATH, GIT_CMD falls back to 'git' without crashing."""
    import gptme.util.git_cmd as git_cmd_mod

    # Temporarily override the module-level constant to simulate no-git env.
    original = git_cmd_mod.GIT_CMD
    git_cmd_mod.GIT_CMD = (
        shutil.which("missing-git-executable-that-does-not-exist") or "git"
    )
    try:
        assert git_cmd_mod.GIT_CMD == "git"
    finally:
        git_cmd_mod.GIT_CMD = original


def test_git_cmd_not_in_cwd_on_windows():
    """On Windows, _resolve_git_cmd never returns a path inside the CWD."""
    if sys.platform != "win32":
        pytest.skip("CWD-filter logic only applies on Windows")

    from gptme.util.git_cmd import _resolve_git_cmd

    cwd = os.path.normcase(os.path.abspath(os.getcwd()))
    result = _resolve_git_cmd()
    if Path(result).is_absolute():
        assert os.path.normcase(os.path.dirname(result)) != cwd, (
            f"GIT_CMD={result!r} resolves into CWD={cwd!r} — hijack protection failed"
        )


def test_git_cmd_cwd_filtered_from_path():
    """_resolve_git_cmd skips '' and '.' PATH entries that map to CWD."""
    if sys.platform != "win32":
        pytest.skip("CWD-filter logic only applies on Windows")

    from gptme.util import git_cmd as git_cmd_mod

    cwd = os.getcwd()
    # Inject a PATH that contains only the CWD (via empty-string entry) plus a
    # real system path so which() can still find git if it exists there.
    system_git_dir = (
        str(Path(shutil.which("git")).parent)
        if shutil.which("git")
        else r"C:\Windows\System32"
    )
    fake_path = os.pathsep.join(["", system_git_dir])

    with patch.dict(os.environ, {"PATH": fake_path}):
        importlib.reload(git_cmd_mod)
        result = git_cmd_mod.GIT_CMD

    # The result must not point into CWD.
    if Path(result).is_absolute():
        assert os.path.normcase(
            os.path.abspath(os.path.dirname(result))
        ) != os.path.normcase(os.path.abspath(cwd)), (
            f"GIT_CMD={result!r} still points into CWD after filtering"
        )
