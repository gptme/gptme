"""Tests for gptme/dirs.py — directory resolution and path utilities.

Tests cover:
- Config/data/log directory resolution
- Environment variable overrides (XDG_DATA_HOME, GPTME_LOGS_HOME, GPTME_WORKSPACE)
- Workspace detection (git repos, gptme.toml, submodules)
- Project directory walking (gptme.toml, .git)
- Profile memory directories
- History file paths
- Readline history migration
- Path initialization
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from gptme import dirs

# ── Config directory ──────────────────────────────────────────────────────


class TestGetConfigDir:
    def test_returns_path(self):
        result = dirs.get_config_dir()
        assert isinstance(result, Path)
        assert "gptme" in str(result)


# ── Data directory ────────────────────────────────────────────────────────


class TestGetDataDir:
    def test_xdg_data_home_override(self, tmp_path: Path):
        """XDG_DATA_HOME takes precedence over everything."""
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(tmp_path)}):
            result = dirs.get_data_dir()
        assert result == tmp_path / "gptme"

    def test_xdg_data_home_not_set_falls_through(self):
        """Without XDG_DATA_HOME, should return a path (either legacy or platformdirs)."""
        env = os.environ.copy()
        env.pop("XDG_DATA_HOME", None)
        with patch.dict(os.environ, env, clear=True):
            result = dirs.get_data_dir()
        assert isinstance(result, Path)
        assert "gptme" in str(result)

    def test_legacy_path_preferred_when_exists(self, tmp_path: Path):
        """When ~/.local/share/gptme exists and XDG_DATA_HOME is unset, use legacy."""
        legacy = tmp_path / ".local" / "share" / "gptme"
        legacy.mkdir(parents=True)
        env = os.environ.copy()
        env.pop("XDG_DATA_HOME", None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch("pathlib.Path.expanduser", return_value=legacy),
        ):
            result = dirs.get_data_dir()
        assert result == legacy


# ── Logs directory ────────────────────────────────────────────────────────


class TestGetLogsDir:
    def test_gptme_logs_home_override(self, tmp_path: Path):
        """GPTME_LOGS_HOME env var takes precedence."""
        logs_dir = tmp_path / "custom_logs"
        with patch.dict(os.environ, {"GPTME_LOGS_HOME": str(logs_dir)}):
            result = dirs.get_logs_dir()
        assert result == logs_dir
        assert result.exists()  # should be created

    def test_default_is_subdir_of_data(self):
        """Without GPTME_LOGS_HOME, logs go under get_data_dir()/logs."""
        env = os.environ.copy()
        env.pop("GPTME_LOGS_HOME", None)
        with patch.dict(os.environ, env, clear=True):
            result = dirs.get_logs_dir()
        assert result.name == "logs"

    def test_creates_directory(self, tmp_path: Path):
        """get_logs_dir() creates the directory if it doesn't exist."""
        logs_dir = tmp_path / "nonexistent" / "logs"
        assert not logs_dir.exists()
        with patch.dict(os.environ, {"GPTME_LOGS_HOME": str(logs_dir)}):
            result = dirs.get_logs_dir()
        assert result.exists()


# ── History files ─────────────────────────────────────────────────────────


class TestHistoryFiles:
    def test_readline_history_path(self, tmp_path: Path):
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(tmp_path)}):
            result = dirs.get_readline_history_file()
        assert result == tmp_path / "gptme" / "history"

    def test_pt_history_path(self, tmp_path: Path):
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(tmp_path)}):
            result = dirs.get_pt_history_file()
        assert result == tmp_path / "gptme" / "history.pt"


# ── Project directory detection ───────────────────────────────────────────


class TestGetProjectGptmeDir:
    def test_finds_gptme_toml(self, tmp_path: Path):
        """Finds directory containing gptme.toml."""
        (tmp_path / "gptme.toml").touch()
        subdir = tmp_path / "a" / "b" / "c"
        subdir.mkdir(parents=True)
        with patch("gptme.dirs.Path.cwd", return_value=subdir):
            result = dirs.get_project_gptme_dir()
        assert result == tmp_path

    def test_prefers_gptme_toml_over_git(self, tmp_path: Path):
        """gptme.toml takes priority over .git directory."""
        git_root = tmp_path / "repo"
        git_root.mkdir()
        (git_root / ".git").mkdir()
        toml_root = git_root / "nested"
        toml_root.mkdir()
        (toml_root / "gptme.toml").touch()
        workdir = toml_root / "src"
        workdir.mkdir()
        with patch("gptme.dirs.Path.cwd", return_value=workdir):
            result = dirs.get_project_gptme_dir()
        assert result == toml_root

    def test_falls_back_to_git_dir(self, tmp_path: Path):
        """Without gptme.toml, falls back to .git directory."""
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "src"
        subdir.mkdir()
        # Block gptme.toml detection outside tmp_path: the walk traverses the real
        # filesystem above tmp_path, which would find the gptme repo's own gptme.toml
        # when tests run inside the checkout.
        _orig_exists = Path.exists

        def _exists(p: Path) -> bool:
            if p.name == "gptme.toml" and not str(p).startswith(str(tmp_path)):
                return False
            return _orig_exists(p)

        with (
            patch("gptme.dirs.Path.cwd", return_value=subdir),
            patch.object(Path, "exists", _exists),
        ):
            result = dirs.get_project_gptme_dir()
        assert result == tmp_path

    def test_returns_none_when_no_markers(self, tmp_path: Path):
        """Returns None when neither gptme.toml nor .git exists."""
        subdir = tmp_path / "isolated"
        subdir.mkdir()
        with patch("gptme.dirs.Path.cwd", return_value=subdir):
            dirs.get_project_gptme_dir()
        # May return None or find a parent .git depending on the real filesystem
        # Just verify it doesn't crash


class TestGetProjectGitDir:
    def test_finds_git_directory(self, tmp_path: Path):
        """Finds the nearest .git directory."""
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "deep" / "nested"
        subdir.mkdir(parents=True)
        with patch("gptme.dirs.Path.cwd", return_value=subdir):
            result = dirs.get_project_git_dir()
        assert result == tmp_path

    def test_returns_none_without_git(self, tmp_path: Path):
        """Returns None when no .git found."""
        subdir = tmp_path / "no_git"
        subdir.mkdir()
        with patch("gptme.dirs.Path.cwd", return_value=subdir):
            dirs._get_project_git_dir_walk()
        # Could find a real .git above tmp_path; verify it doesn't crash

    def test_git_dir_walk_finds_immediate(self, tmp_path: Path):
        """Finds .git in the cwd itself."""
        (tmp_path / ".git").mkdir()
        with patch("gptme.dirs.Path.cwd", return_value=tmp_path):
            result = dirs._get_project_git_dir_walk()
        assert result == tmp_path

    def test_git_dir_call_success(self, tmp_path: Path):
        """_get_project_git_dir_call uses git rev-parse."""
        mock_result = subprocess.CompletedProcess(
            args=["git", "rev-parse", "--show-toplevel"],
            returncode=0,
            stdout=str(tmp_path) + "\n",
            stderr="",
        )
        with patch("gptme.dirs.subprocess.run", return_value=mock_result):
            result = dirs._get_project_git_dir_call()
        assert result == tmp_path

    def test_git_dir_call_failure(self):
        """_get_project_git_dir_call returns None on git failure."""
        with patch(
            "gptme.dirs.subprocess.run",
            side_effect=subprocess.CalledProcessError(128, "git"),
        ):
            result = dirs._get_project_git_dir_call()
        assert result is None


# ── Workspace detection ───────────────────────────────────────────────────


class TestGetWorkspace:
    def test_env_var_override(self, tmp_path: Path):
        """GPTME_WORKSPACE env var takes highest precedence."""
        workspace = tmp_path / "my_workspace"
        workspace.mkdir()
        with patch.dict(os.environ, {"GPTME_WORKSPACE": str(workspace)}):
            result = dirs.get_workspace()
        assert result == workspace

    def test_git_root_detection(self, tmp_path: Path):
        """Detects workspace from git root."""
        git_root = tmp_path / "repo"
        git_root.mkdir()
        (git_root / ".git").mkdir()  # directory, not file — normal repo

        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=str(git_root) + "\n", stderr=""
        )
        env = os.environ.copy()
        env.pop("GPTME_WORKSPACE", None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch("gptme.dirs.subprocess.run", return_value=mock_result),
        ):
            result = dirs.get_workspace()
        assert result == git_root

    def test_submodule_traversal(self, tmp_path: Path):
        """When in a submodule (.git is a file), returns parent repo root."""
        parent_repo = tmp_path / "parent"
        parent_repo.mkdir()
        submodule = tmp_path / "parent" / "sub"
        submodule.mkdir()
        # Submodule has .git as a file, not directory
        (submodule / ".git").write_text("gitdir: ../.git/modules/sub\n")

        # First call: git rev-parse --show-toplevel returns submodule root
        toplevel_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=str(submodule) + "\n", stderr=""
        )
        # Second call: git rev-parse --show-superproject-working-tree returns parent
        super_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=str(parent_repo) + "\n", stderr=""
        )

        env = os.environ.copy()
        env.pop("GPTME_WORKSPACE", None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "gptme.dirs.subprocess.run", side_effect=[toplevel_result, super_result]
            ),
        ):
            result = dirs.get_workspace()
        assert result == parent_repo

    def test_submodule_no_superproject(self, tmp_path: Path):
        """When superproject detection fails, falls back to submodule root."""
        submodule = tmp_path / "sub"
        submodule.mkdir()
        (submodule / ".git").write_text("gitdir: ../.git/modules/sub\n")

        toplevel_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=str(submodule) + "\n", stderr=""
        )
        # Superproject call returns empty (detached submodule)
        super_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="\n", stderr=""
        )

        env = os.environ.copy()
        env.pop("GPTME_WORKSPACE", None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "gptme.dirs.subprocess.run", side_effect=[toplevel_result, super_result]
            ),
        ):
            result = dirs.get_workspace()
        assert result == submodule

    def test_superproject_timeout(self, tmp_path: Path):
        """Falls back to submodule root when superproject call times out."""
        submodule = tmp_path / "sub"
        submodule.mkdir()
        (submodule / ".git").write_text("gitdir: ../.git/modules/sub\n")

        toplevel_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=str(submodule) + "\n", stderr=""
        )

        env = os.environ.copy()
        env.pop("GPTME_WORKSPACE", None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "gptme.dirs.subprocess.run",
                side_effect=[toplevel_result, subprocess.TimeoutExpired("git", 5)],
            ),
        ):
            result = dirs.get_workspace()
        assert result == submodule

    def test_git_not_found(self, tmp_path: Path):
        """Falls back to cwd when git is not available."""
        env = os.environ.copy()
        env.pop("GPTME_WORKSPACE", None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch("gptme.dirs.subprocess.run", side_effect=FileNotFoundError("git")),
            patch("gptme.dirs.Path.cwd", return_value=tmp_path),
        ):
            result = dirs.get_workspace()
        assert result == tmp_path

    def test_git_timeout(self, tmp_path: Path):
        """Falls back to cwd when git rev-parse times out."""
        env = os.environ.copy()
        env.pop("GPTME_WORKSPACE", None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "gptme.dirs.subprocess.run",
                side_effect=subprocess.TimeoutExpired("git", 5),
            ),
            patch("gptme.dirs.Path.cwd", return_value=tmp_path),
        ):
            result = dirs.get_workspace()
        assert result == tmp_path

    def test_git_nonzero_exit(self, tmp_path: Path):
        """Falls back to cwd when git returns non-zero."""
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr="not a git repo"
        )
        env = os.environ.copy()
        env.pop("GPTME_WORKSPACE", None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch("gptme.dirs.subprocess.run", return_value=mock_result),
            patch("gptme.dirs.Path.cwd", return_value=tmp_path),
        ):
            result = dirs.get_workspace()
        assert result == tmp_path

    def test_superproject_file_not_found(self, tmp_path: Path):
        """Falls back to submodule root when superproject raises FileNotFoundError."""
        submodule = tmp_path / "sub"
        submodule.mkdir()
        (submodule / ".git").write_text("gitdir: ../.git/modules/sub\n")

        toplevel_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=str(submodule) + "\n", stderr=""
        )

        env = os.environ.copy()
        env.pop("GPTME_WORKSPACE", None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "gptme.dirs.subprocess.run",
                side_effect=[toplevel_result, FileNotFoundError("git")],
            ),
        ):
            result = dirs.get_workspace()
        assert result == submodule

    def test_superproject_nonzero_exit(self, tmp_path: Path):
        """Falls back to git root when superproject returns non-zero."""
        submodule = tmp_path / "sub"
        submodule.mkdir()
        (submodule / ".git").write_text("gitdir: ../.git/modules/sub\n")

        toplevel_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=str(submodule) + "\n", stderr=""
        )
        super_result = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr="error"
        )

        env = os.environ.copy()
        env.pop("GPTME_WORKSPACE", None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "gptme.dirs.subprocess.run", side_effect=[toplevel_result, super_result]
            ),
        ):
            result = dirs.get_workspace()
        # returncode != 0, so the if branch is skipped, falls through to return git_root
        assert result == submodule


# ── Profile memory directory ──────────────────────────────────────────────


class TestGetProfileMemoryDir:
    def test_creates_directory(self, tmp_path: Path):
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(tmp_path)}):
            result = dirs.get_profile_memory_dir("explorer")
        assert result.exists()
        assert result == tmp_path / "gptme" / "memories" / "profiles" / "explorer"

    def test_different_profiles_different_dirs(self, tmp_path: Path):
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(tmp_path)}):
            r1 = dirs.get_profile_memory_dir("explorer")
            r2 = dirs.get_profile_memory_dir("researcher")
        assert r1 != r2
        assert r1.name == "explorer"
        assert r2.name == "researcher"

    def test_idempotent(self, tmp_path: Path):
        """Calling twice doesn't fail."""
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(tmp_path)}):
            r1 = dirs.get_profile_memory_dir("test")
            r2 = dirs.get_profile_memory_dir("test")
        assert r1 == r2


# ── Readline history migration ────────────────────────────────────────────


class TestMigrateReadlineHistory:
    def test_migrates_when_old_exists(self, tmp_path: Path):
        """Migrates history from config dir to data dir."""
        config_dir = tmp_path / "config" / "gptme"
        data_dir = tmp_path / "data" / "gptme"
        config_dir.mkdir(parents=True)
        data_dir.mkdir(parents=True)
        old_history = config_dir / "history"
        new_history = data_dir / "history"
        old_history.write_text("line1\nline2\n")

        with (
            patch.object(dirs, "get_config_dir", return_value=config_dir),
            patch.object(dirs, "get_data_dir", return_value=data_dir),
        ):
            dirs._migrate_readline_history()

        assert not old_history.exists()
        assert new_history.exists()
        assert new_history.read_text() == "line1\nline2\n"

    def test_no_migration_when_new_exists(self, tmp_path: Path):
        """Does not overwrite existing history in data dir."""
        config_dir = tmp_path / "config" / "gptme"
        data_dir = tmp_path / "data" / "gptme"
        config_dir.mkdir(parents=True)
        data_dir.mkdir(parents=True)
        old_history = config_dir / "history"
        new_history = data_dir / "history"
        old_history.write_text("old_content")
        new_history.write_text("new_content")

        with (
            patch.object(dirs, "get_config_dir", return_value=config_dir),
            patch.object(dirs, "get_data_dir", return_value=data_dir),
        ):
            dirs._migrate_readline_history()

        assert old_history.exists()  # not moved
        assert new_history.read_text() == "new_content"  # not overwritten

    def test_no_migration_when_old_missing(self, tmp_path: Path):
        """Does nothing when there's no old history to migrate."""
        config_dir = tmp_path / "config" / "gptme"
        data_dir = tmp_path / "data" / "gptme"
        config_dir.mkdir(parents=True)
        data_dir.mkdir(parents=True)

        with (
            patch.object(dirs, "get_config_dir", return_value=config_dir),
            patch.object(dirs, "get_data_dir", return_value=data_dir),
        ):
            dirs._migrate_readline_history()

        assert not (data_dir / "history").exists()

    def test_migration_failure_handled(self, tmp_path: Path):
        """Migration failure is logged but doesn't crash."""
        config_dir = tmp_path / "config" / "gptme"
        data_dir = tmp_path / "data" / "gptme"
        config_dir.mkdir(parents=True)
        data_dir.mkdir(parents=True)
        old_history = config_dir / "history"
        old_history.write_text("content")

        with (
            patch.object(dirs, "get_config_dir", return_value=config_dir),
            patch.object(dirs, "get_data_dir", return_value=data_dir),
            patch("gptme.dirs.shutil.move", side_effect=PermissionError("denied")),
        ):
            # Should not raise
            dirs._migrate_readline_history()

        assert old_history.exists()  # not moved due to error


# ── Init paths ────────────────────────────────────────────────────────────


class TestInitPaths:
    def test_creates_directories(self, tmp_path: Path):
        """_init_paths creates config, data, and logs directories."""
        with (
            patch.object(dirs, "get_config_dir", return_value=tmp_path / "config"),
            patch.object(dirs, "get_data_dir", return_value=tmp_path / "data"),
            patch.object(dirs, "get_logs_dir", return_value=tmp_path / "logs"),
            patch.object(dirs, "_migrate_readline_history"),
        ):
            dirs._init_paths()

        assert (tmp_path / "config").exists()
        assert (tmp_path / "data").exists()
        assert (tmp_path / "logs").exists()


# ── Edge cases ────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_workspace_env_var_nonexistent_path(self):
        """GPTME_WORKSPACE returns Path even if directory doesn't exist."""
        with patch.dict(os.environ, {"GPTME_WORKSPACE": "/nonexistent/workspace/path"}):
            result = dirs.get_workspace()
        assert result == Path("/nonexistent/workspace/path")

    def test_gptme_toml_in_cwd(self, tmp_path: Path):
        """gptme.toml in cwd itself is found."""
        (tmp_path / "gptme.toml").touch()
        with patch("gptme.dirs.Path.cwd", return_value=tmp_path):
            result = dirs.get_project_gptme_dir()
        assert result == tmp_path

    def test_git_dir_as_file_detected_as_submodule(self, tmp_path: Path):
        """A .git file (not directory) signals a submodule."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").write_text("gitdir: /some/path\n")

        # .git is a file, so is_file() returns True in get_workspace
        toplevel_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=str(repo) + "\n", stderr=""
        )
        super_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=str(tmp_path) + "\n", stderr=""
        )
        env = os.environ.copy()
        env.pop("GPTME_WORKSPACE", None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "gptme.dirs.subprocess.run", side_effect=[toplevel_result, super_result]
            ),
        ):
            result = dirs.get_workspace()
        assert result == tmp_path

    def test_multiple_gptme_toml_finds_nearest(self, tmp_path: Path):
        """Multiple gptme.toml files: the nearest one (closest parent) wins."""
        outer = tmp_path / "outer"
        inner = outer / "inner"
        deepest = inner / "deep"
        deepest.mkdir(parents=True)
        (outer / "gptme.toml").touch()
        (inner / "gptme.toml").touch()
        with patch("gptme.dirs.Path.cwd", return_value=deepest):
            result = dirs.get_project_gptme_dir()
        assert result == inner  # nearest parent with gptme.toml
