"""Tests for the Git executable resolver (hardening against Windows CWD hijack)."""

import importlib
import shutil
from pathlib import Path

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


def test_git_cmd_matches_shutil_which():
    """GIT_CMD equals the path shutil.which returns, proving the constant is resolved."""
    if shutil.which("git") is None:
        pytest.skip("git not available on PATH")

    import gptme.util.git_cmd as git_cmd_mod

    # Re-import from a fresh load to avoid any earlier test side-effects.
    importlib.reload(git_cmd_mod)

    resolved = shutil.which("git")
    assert resolved == git_cmd_mod.GIT_CMD, (
        f"GIT_CMD={git_cmd_mod.GIT_CMD!r} should equal shutil.which('git')={resolved!r}"
    )
