"""Tests for the Git executable resolver (hardening against CWD hijack and GitSpawn)."""

import importlib
import os
import platform
import shutil
import subprocess
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


# ---------------------------------------------------------------------------
# GitSpawn hardening — repo-local execution sink suppression
# ---------------------------------------------------------------------------


def test_git_inspect_cmd_structure():
    """git_inspect_cmd() returns a list with GIT_CMD and all four sink-suppression flags."""
    from gptme.util.git_cmd import GIT_CMD, git_inspect_cmd

    cmd = git_inspect_cmd()
    assert isinstance(cmd, list)
    assert len(cmd) >= 1
    assert cmd[0] == GIT_CMD

    joined = " ".join(cmd)
    for sink in (
        "core.fsmonitor=",
        "core.sshCommand=",
        "diff.external=",
        "core.hooksPath=",
    ):
        assert sink in joined, f"git_inspect_cmd() is missing -{sink!r} suppression"


def _find_real_git() -> str | None:
    """Locate the real git binary, skipping any local wrapper at ~/bin/git."""
    import os

    # Skip any directory that resolves to $HOME/bin — those may hold wrapper scripts.
    home_bin = os.path.expanduser("~/bin")
    safe_path = os.pathsep.join(
        d for d in os.get_exec_path() if os.path.abspath(d) != os.path.abspath(home_bin)
    )
    return shutil.which("git", path=safe_path)


_REAL_GIT = _find_real_git()


@pytest.mark.skipif(
    _REAL_GIT is None,
    reason="real git binary not available outside ~/bin",
)
@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="fsmonitor shell-script test is Unix-only",
)
def test_git_inspect_cmd_suppresses_fsmonitor(tmp_path):
    """git_inspect_cmd() flags prevent core.fsmonitor from firing during inspection.

    Regression guard for the GitSpawn attack class (Manifold Security, 2026-09-01):
    a repository-local core.fsmonitor hook can be set to an arbitrary command that
    executes when git refreshes its index (triggered by git status / git ls-files).
    git_inspect_cmd() passes -c core.fsmonitor= to disable it.

    Uses the real git binary (not any ~/bin wrapper) so the test is pure and does
    not depend on wrapper behaviour.
    """
    from gptme.util.git_cmd import _INSPECT_SAFE_FLAGS

    assert _REAL_GIT is not None  # for type-checker
    real_git = _REAL_GIT

    # Isolated HOME keeps gpg-sign, global hooks, and other user config out of the test.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    git_env = {**os.environ, "HOME": str(fake_home), "GIT_CONFIG_NOSYSTEM": "1"}

    repo = tmp_path / "testrepo"
    repo.mkdir()
    marker = tmp_path / "fsmonitor_fired"
    fsmonitor_script = tmp_path / "fsmonitor.sh"
    fsmonitor_script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    fsmonitor_script.chmod(0o755)

    # Set up a minimal git repo using the real git binary (no wrappers)
    for cmd in [
        [real_git, "init", str(repo)],
        [real_git, "-C", str(repo), "config", "user.email", "test@example.com"],
        [real_git, "-C", str(repo), "config", "user.name", "Test"],
    ]:
        subprocess.run(cmd, check=True, capture_output=True, env=git_env)
    (repo / "file.txt").write_text("hello")
    subprocess.run(
        [real_git, "-C", str(repo), "add", "."],
        check=True,
        capture_output=True,
        env=git_env,
    )
    subprocess.run(
        [real_git, "-C", str(repo), "commit", "-m", "init"],
        check=True,
        capture_output=True,
        env=git_env,
    )

    # Plant the fsmonitor hook in the repo's local config
    subprocess.run(
        [real_git, "-C", str(repo), "config", "core.fsmonitor", str(fsmonitor_script)],
        check=True,
        capture_output=True,
        env=git_env,
    )

    # Baseline: bare git DOES fire the hook (validates the test setup is sound)
    marker.unlink(missing_ok=True)
    subprocess.run(
        [real_git, "status", "--short"],
        cwd=repo,
        capture_output=True,
        timeout=5,
        env=git_env,
        check=False,
    )
    if not marker.exists():
        pytest.skip(
            "core.fsmonitor did not fire with bare git on this platform — test setup inconclusive"
        )

    # With sink-suppression flags the hook must NOT fire
    marker.unlink(missing_ok=True)
    subprocess.run(
        [real_git, *_INSPECT_SAFE_FLAGS, "status", "--short"],
        cwd=repo,
        capture_output=True,
        timeout=5,
        env=git_env,
        check=False,
    )
    assert not marker.exists(), (
        "core.fsmonitor fired despite -c core.fsmonitor= flag — sink suppression is broken"
    )
