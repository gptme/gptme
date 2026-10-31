"""Tests for the precommit tool — automatic pre-commit checks on saves and turns.

Tests cover:
- use_checks: config + filesystem-based enablement logic
- run_checks_per_file: per-file flag logic
- _get_modified_files: git status parsing
- run_precommit_checks: subprocess invocation and output parsing
- handle_precommit_command: /pre-commit command handler
- run_precommit_on_file: per-file save hook
- check_precommit_available: shutil.which detection
- run_full_precommit_checks: turn-post hook with StopPropagation
- tool spec: registration, hooks, commands
"""

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.hooks import StopPropagation
from gptme.message import Message
from gptme.tools.autocommit import tool as autocommit_tool
from gptme.tools.precommit import (
    _get_modified_files,
    _get_repo_root,
    check_precommit_available,
    get_precommit_runner,
    handle_precommit_command,
    run_checks_per_file,
    run_full_precommit_checks,
    run_precommit_checks,
    run_precommit_on_file,
    tool,
    use_checks,
)
from gptme.util.git_cmd import GIT_CMD

# ── Helpers ───────────────────────────────────────────────────────────────


def _mock_config(env: dict[str, str] | None = None):
    """Return a patched get_config that responds to get_env calls."""
    env = env or {}
    config = MagicMock()
    config.get_env.side_effect = lambda key, default="": env.get(key, default)
    config.get_env_bool.side_effect = lambda key, default=False: (
        env.get(key, str(default)).lower() in ("1", "true", "yes")
    )
    return patch("gptme.tools.precommit.get_config", return_value=config)


def _mock_subprocess_run(
    returncode: int = 0, stdout: str | bytes = "", stderr: str | bytes = ""
):
    """Return a mock subprocess.run result."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def _mock_which(path: str | None = os.path.abspath("/usr/bin/pre-commit")):
    """Return a patched shutil.which."""
    return patch("gptme.tools.precommit.shutil.which", return_value=path)


def _mock_which_available(*available: str):
    """Return a patched shutil.which that only resolves the named binaries."""
    return patch(
        "gptme.tools.precommit.shutil.which",
        side_effect=lambda name: (
            os.path.abspath(f"/usr/bin/{name}") if name in available else None
        ),
    )


@pytest.fixture
def mock_repo_root(tmp_path: Path) -> Iterator[MagicMock]:
    """Keep runner/git-query subprocess mocks separate from root discovery."""
    with patch("gptme.tools.precommit._get_repo_root", return_value=tmp_path) as root:
        yield root


# ── TestUseChecks ─────────────────────────────────────────────────────────


class TestUseChecks:
    """Tests for use_checks — decides whether pre-commit should run."""

    def test_explicitly_enabled_with_config(self, tmp_path: Path, monkeypatch):
        """Returns True when GPTME_CHECK=true and config file exists."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            assert use_checks() is True

    def test_explicitly_disabled(self, tmp_path: Path, monkeypatch):
        """Returns False when GPTME_CHECK=false, even with config file."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        with _mock_config({"GPTME_CHECK": "false"}):
            assert use_checks() is False

    def test_disabled_with_zero(self, tmp_path: Path, monkeypatch):
        """Returns False when GPTME_CHECK=0."""
        monkeypatch.chdir(tmp_path)
        with _mock_config({"GPTME_CHECK": "0"}):
            assert use_checks() is False

    def test_disabled_with_no(self, tmp_path: Path, monkeypatch):
        """Returns False when GPTME_CHECK=no."""
        monkeypatch.chdir(tmp_path)
        with _mock_config({"GPTME_CHECK": "no"}):
            assert use_checks() is False

    def test_auto_detect_config_file(self, tmp_path: Path, monkeypatch):
        """Auto-enables when .pre-commit-config.yaml exists in cwd."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        with _mock_config({"GPTME_CHECK": ""}), _mock_which():
            assert use_checks() is True

    def test_auto_detect_config_in_parent(self, tmp_path: Path, monkeypatch):
        """Auto-enables when .pre-commit-config.yaml exists in parent dir."""
        sub = tmp_path / "subdir"
        sub.mkdir()
        monkeypatch.chdir(sub)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        with _mock_config({"GPTME_CHECK": ""}), _mock_which():
            assert use_checks() is True

    def test_disabled_when_no_config(self, tmp_path: Path, monkeypatch):
        """Returns False when no config file and not explicitly enabled."""
        monkeypatch.chdir(tmp_path)
        with _mock_config({"GPTME_CHECK": ""}):
            assert use_checks() is False

    def test_disabled_when_precommit_not_installed(self, tmp_path: Path, monkeypatch):
        """Returns False when pre-commit binary isn't on PATH."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        with _mock_config({"GPTME_CHECK": ""}), _mock_which(None):
            assert use_checks() is False

    def test_enabled_with_yes(self, tmp_path: Path, monkeypatch):
        """Accepts 'yes' as truthy value."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        with _mock_config({"GPTME_CHECK": "yes"}), _mock_which():
            assert use_checks() is True

    def test_enabled_with_one(self, tmp_path: Path, monkeypatch):
        """Accepts '1' as truthy value."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        with _mock_config({"GPTME_CHECK": "1"}), _mock_which():
            assert use_checks() is True

    def test_warns_when_explicitly_enabled_without_config(
        self, tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
    ):
        """Warns when GPTME_CHECK=true but no config file exists."""
        monkeypatch.chdir(tmp_path)
        with (
            caplog.at_level("WARNING"),
            _mock_config({"GPTME_CHECK": "true"}),
            _mock_which(),
        ):
            assert use_checks() is True
        assert (
            "GPTME_CHECK is enabled but no .pre-commit-config.yaml found" in caplog.text
        )

    def test_enabled_with_prek_only(self, tmp_path: Path, monkeypatch):
        """prek satisfies the runner requirement when pre-commit is absent."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        with _mock_config({"GPTME_CHECK": ""}), _mock_which_available("prek"):
            assert use_checks() is True


# ── TestGetPrecommitRunner ────────────────────────────────────────────────


class TestGetPrecommitRunner:
    """Tests for get_precommit_runner — runner detection and preference."""

    def test_prefers_pre_commit(self):
        with _mock_which_available("pre-commit", "prek"):
            assert get_precommit_runner() == os.path.abspath("/usr/bin/pre-commit")

    def test_falls_back_to_prek(self):
        with _mock_which_available("prek"):
            assert get_precommit_runner() == os.path.abspath("/usr/bin/prek")

    def test_keeps_symlink_parent_components(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with _mock_which("runner-link/../bin/pre-commit"):
            assert get_precommit_runner() == os.path.join(
                str(tmp_path), "runner-link/../bin/pre-commit"
            )

    def test_returns_none_when_neither_installed(self):
        with _mock_which_available():
            assert get_precommit_runner() is None


# ── TestRunChecksPerFile ──────────────────────────────────────────────────


class TestRunChecksPerFile:
    """Tests for run_checks_per_file — per-file check enablement."""

    def test_default_is_disabled(self):
        """Per-file checks are disabled by default."""
        with _mock_config({}):
            assert run_checks_per_file() is False

    def test_enabled_with_true(self):
        with _mock_config({"GPTME_CHECK_PER_FILE": "true"}):
            assert run_checks_per_file() is True

    def test_enabled_with_one(self):
        with _mock_config({"GPTME_CHECK_PER_FILE": "1"}):
            assert run_checks_per_file() is True

    def test_enabled_with_yes(self):
        with _mock_config({"GPTME_CHECK_PER_FILE": "yes"}):
            assert run_checks_per_file() is True

    def test_disabled_with_empty(self):
        """Empty string falls back to 'false'."""
        with _mock_config({"GPTME_CHECK_PER_FILE": ""}):
            assert run_checks_per_file() is False


# ── TestGetModifiedFiles ──────────────────────────────────────────────────


class TestGetRepoRoot:
    """Root discovery decodes bytes and removes only Git's terminating LF."""

    @pytest.mark.parametrize("name", ["repo", " repo \t", "repo\n", "repo\n\n"])
    @patch("subprocess.run")
    def test_preserves_path_whitespace(
        self, mock_run: MagicMock, tmp_path: Path, name: str
    ) -> None:
        root = tmp_path / name
        mock_run.return_value = _mock_subprocess_run(stdout=os.fsencode(root) + b"\n")
        assert _get_repo_root() == root
        mock_run.assert_called_once_with(
            [GIT_CMD, "rev-parse", "--show-toplevel"],
            capture_output=True,
            check=False,
            timeout=10,
        )

    @pytest.mark.parametrize("returncode", [0, 128])
    @patch("subprocess.run")
    def test_no_root(self, mock_run: MagicMock, returncode: int) -> None:
        mock_run.return_value = _mock_subprocess_run(returncode=returncode, stdout=b"")
        assert _get_repo_root() is None

    @patch("subprocess.run")
    def test_ignores_output_on_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _mock_subprocess_run(
            returncode=128, stdout=b"/not/root\n"
        )
        assert _get_repo_root() is None

    @pytest.mark.parametrize(
        "error", [subprocess.TimeoutExpired("git", 10), FileNotFoundError("git")]
    )
    @patch("subprocess.run")
    def test_discovery_error(self, mock_run: MagicMock, error: Exception) -> None:
        mock_run.side_effect = error
        assert _get_repo_root() is None


@pytest.mark.usefixtures("mock_repo_root")
class TestGetModifiedFiles:
    """Tests for _get_modified_files — NUL-delimited git path parsing."""

    @patch("subprocess.run")
    def test_returns_modified_and_untracked(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Combines worktree, index and untracked files from root-relative queries."""
        for name in ("file1.py", "file2.py", "staged.py", "new_file.py"):
            (tmp_path / name).touch()
        mock_run.side_effect = [
            _mock_subprocess_run(stdout=b"file1.py\0file2.py\0"),
            _mock_subprocess_run(stdout=b"staged.py\0"),
            _mock_subprocess_run(stdout=b"new_file.py\0"),
        ]
        assert _get_modified_files() == [
            "file1.py",
            "file2.py",
            "new_file.py",
            "staged.py",
        ]
        assert [call.args[0] for call in mock_run.call_args_list] == [
            [GIT_CMD, "diff", "--name-only", "-z"],
            [GIT_CMD, "diff", "--cached", "--name-only", "-z"],
            [GIT_CMD, "ls-files", "--others", "--exclude-standard", "-z"],
        ]
        for call in mock_run.call_args_list:
            assert call.kwargs == {
                "cwd": tmp_path,
                "capture_output": True,
                "check": False,
                "timeout": 10,
            }

    @patch("subprocess.run")
    def test_deduplicates_files(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Same file in all three queries isn't duplicated."""
        (tmp_path / "shared.py").touch()
        mock_run.return_value = _mock_subprocess_run(stdout=b"shared.py\0")
        assert _get_modified_files() == ["shared.py"]
        assert mock_run.call_count == 3

    @patch("subprocess.run")
    def test_returns_sorted(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Results are sorted alphabetically."""
        for name in ("z.py", "a.py", "m.py"):
            (tmp_path / name).touch()
        mock_run.side_effect = [
            _mock_subprocess_run(stdout=b"z.py\0a.py\0"),
            _mock_subprocess_run(stdout=b""),
            _mock_subprocess_run(stdout=b"m.py\0"),
        ]
        assert _get_modified_files() == ["a.py", "m.py", "z.py"]

    @patch("subprocess.run")
    def test_empty_when_no_changes(self, mock_run: MagicMock) -> None:
        """Returns empty list when no modifications."""
        mock_run.return_value = _mock_subprocess_run(stdout=b"")
        assert _get_modified_files() == []
        assert mock_run.call_count == 3

    @patch("subprocess.run")
    def test_handles_git_failure(self, mock_run: MagicMock) -> None:
        """Returns empty list when git fails."""
        mock_run.return_value = _mock_subprocess_run(returncode=128, stdout=b"")
        assert _get_modified_files() == []
        assert mock_run.call_count == 3

    @patch("subprocess.run")
    def test_handles_timeout(self, mock_run: MagicMock) -> None:
        """Returns empty list on timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired("git", 10)
        assert _get_modified_files() == []
        assert mock_run.call_count == 3

    @patch("subprocess.run")
    def test_handles_git_not_found(self, mock_run: MagicMock) -> None:
        """Returns empty list when git not installed."""
        mock_run.side_effect = FileNotFoundError("git not found")
        assert _get_modified_files() == []
        assert mock_run.call_count == 3

    @patch("subprocess.run")
    def test_partial_failure(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Returns files from successful commands when one fails."""
        (tmp_path / "file.py").touch()
        (tmp_path / "new.py").touch()
        mock_run.side_effect = [
            _mock_subprocess_run(stdout=b"file.py\0"),
            _mock_subprocess_run(returncode=128, stdout=b"ignored.py\0"),
            _mock_subprocess_run(stdout=b"new.py\0"),
        ]
        assert _get_modified_files() == ["file.py", "new.py"]

    @patch("subprocess.run")
    def test_no_root_skips_queries(
        self, mock_run: MagicMock, mock_repo_root: MagicMock
    ) -> None:
        mock_repo_root.return_value = None
        assert _get_modified_files() == []
        mock_run.assert_not_called()


# ── TestRunPrecommitChecks ────────────────────────────────────────────────


@pytest.mark.usefixtures("mock_repo_root")
class TestRunPrecommitChecks:
    """Tests for run_precommit_checks — main check runner."""

    def test_disabled_returns_false_none(self, tmp_path: Path, monkeypatch):
        """Returns (False, None) when checks are disabled."""
        monkeypatch.chdir(tmp_path)
        with _mock_config({"GPTME_CHECK": "false"}):
            success, output = run_precommit_checks()
            assert success is False
            assert output is None

    @patch("subprocess.run")
    def test_all_files_mode(self, mock_run: MagicMock, tmp_path: Path, monkeypatch):
        """Runs with --all-files flag in all-files mode."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_run.return_value = _mock_subprocess_run()
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            run_precommit_checks(all_files=True)
        mock_run.assert_called_once_with(
            [os.path.abspath("/usr/bin/pre-commit"), "run", "--all-files"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )

    @patch("subprocess.run")
    def test_uses_prek_when_only_prek_installed(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch
    ):
        """Invokes prek with the same run arguments when pre-commit is absent."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_run.return_value = _mock_subprocess_run()
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which_available("prek"):
            run_precommit_checks(all_files=True)
        args = mock_run.call_args[0][0]
        assert args == [os.path.abspath("/usr/bin/prek"), "run", "--all-files"]
        assert mock_run.call_count == 1
        assert mock_run.call_args.kwargs["cwd"] == tmp_path

    @patch("gptme.tools.precommit._get_modified_files")
    @patch("subprocess.run")
    def test_modified_files_mode(
        self,
        mock_run: MagicMock,
        mock_modified: MagicMock,
        tmp_path: Path,
        monkeypatch,
    ):
        """Runs with --files flag for modified files."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_modified.return_value = ["a.py", "b.py"]
        mock_run.return_value = _mock_subprocess_run()
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            run_precommit_checks(all_files=False)
        mock_modified.assert_called_once_with(tmp_path)
        mock_run.assert_called_once_with(
            [os.path.abspath("/usr/bin/pre-commit"), "run", "--files", "a.py", "b.py"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )

    @patch("gptme.tools.precommit._get_modified_files")
    @patch("subprocess.run")
    def test_fallback_to_all_files_when_none_modified(
        self,
        mock_run: MagicMock,
        mock_modified: MagicMock,
        tmp_path: Path,
        monkeypatch,
    ):
        """Falls back to --all-files when no modified files found."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_modified.return_value = []
        mock_run.return_value = _mock_subprocess_run()
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            run_precommit_checks(all_files=False)
        args = mock_run.call_args[0][0]
        assert "--all-files" in args

    @pytest.mark.parametrize("all_files", [True, False])
    @patch("subprocess.run")
    def test_no_root_preserves_nonrepo_runner(
        self,
        mock_run: MagicMock,
        mock_repo_root: MagicMock,
        all_files: bool,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing root still runs checks in the current directory."""
        monkeypatch.chdir(tmp_path)
        mock_repo_root.return_value = None
        mock_run.return_value = _mock_subprocess_run()
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            assert run_precommit_checks(all_files=all_files) == (True, None)
        mock_run.assert_called_once_with(
            [os.path.abspath("/usr/bin/pre-commit"), "run", "--all-files"],
            cwd=None,
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )

    @patch("subprocess.run")
    def test_does_not_select_outer_repository_config(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An outer config must not be passed explicitly into a nested repo."""
        (tmp_path.parent / ".pre-commit-config.yaml").write_text("repos: []")
        nested = tmp_path / "service"
        nested.mkdir()
        monkeypatch.chdir(nested)
        mock_run.return_value = _mock_subprocess_run()
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            run_precommit_checks()
        assert "--config" not in mock_run.call_args.args[0]
        assert mock_run.call_args.kwargs["cwd"] == tmp_path

    @patch("subprocess.run")
    def test_runner_timeout(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Timeout applies to the runner, not the separately mocked root query."""
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = subprocess.TimeoutExpired("pre-commit", 300)
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            assert run_precommit_checks() == (
                False,
                "Pre-commit checks timed out after 300s",
            )
        mock_run.assert_called_once_with(
            [os.path.abspath("/usr/bin/pre-commit"), "run", "--all-files"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )

    @patch("subprocess.run")
    def test_success_returns_true_none(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch
    ):
        """Returns (True, None) when checks pass."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_run.return_value = _mock_subprocess_run()
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            success, output = run_precommit_checks()
        assert success is True
        assert output is None

    @patch("subprocess.run")
    def test_failure_returns_output(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch
    ):
        """Returns (False, output) when checks fail."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "pre-commit", output="ruff...Failed", stderr="lint errors"
        )
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            success, output = run_precommit_checks()
        assert success is False
        assert output is not None
        assert "Pre-commit checks failed" in output
        mock_run.assert_called_once_with(
            [os.path.abspath("/usr/bin/pre-commit"), "run", "--all-files"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )

    @patch("subprocess.run")
    def test_failure_includes_stdout(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch
    ):
        """Failure output includes stdout content."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "pre-commit", output="Hook failed: ruff", stderr=""
        )
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            _, output = run_precommit_checks()
        assert output is not None
        assert "Hook failed: ruff" in output

    @patch("subprocess.run")
    def test_failure_includes_stderr(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch
    ):
        """Failure output includes stderr content."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "pre-commit", output="", stderr="error: mypy failed"
        )
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            _, output = run_precommit_checks()
        assert output is not None
        assert "mypy failed" in output

    @patch("subprocess.run")
    def test_auto_fix_note(self, mock_run: MagicMock, tmp_path: Path, monkeypatch):
        """Shows auto-fix note when hooks modified files."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_run.side_effect = subprocess.CalledProcessError(
            1,
            "pre-commit",
            output="files were modified by this hook",
            stderr="",
        )
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            _, output = run_precommit_checks()
        assert output is not None
        assert "automatically fixed" in output

    @patch("subprocess.run")
    def test_manual_fix_note(self, mock_run: MagicMock, tmp_path: Path, monkeypatch):
        """Shows manual-fix note when hooks didn't auto-fix."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "pre-commit", output="ruff check failed", stderr=""
        )
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            _, output = run_precommit_checks()
        assert output is not None
        assert "manual fixes" in output

    @patch("subprocess.run")
    def test_keyboard_interrupt_propagated(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch
    ):
        """Exit code 130 raises KeyboardInterrupt."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_run.side_effect = subprocess.CalledProcessError(
            130, "pre-commit", output="", stderr=""
        )
        with (
            _mock_config({"GPTME_CHECK": "true"}),
            _mock_which(),
            pytest.raises(KeyboardInterrupt),
        ):
            run_precommit_checks()

    @patch("subprocess.run")
    def test_no_config_in_nested_repo(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch
    ):
        """Returns (False, None) when config not found in nested repo."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_run.side_effect = subprocess.CalledProcessError(
            1,
            "pre-commit",
            output=".pre-commit-config.yaml is not a file",
            stderr="",
        )
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            success, output = run_precommit_checks()
        assert success is False
        assert output is None

    @patch("subprocess.run")
    def test_nested_repo_prek_stderr_returns_false_none(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch
    ):
        """prek reports the nested-repo no-config case on stderr."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_run.side_effect = subprocess.CalledProcessError(
            1,
            "prek",
            output="",
            stderr=(
                "error: No `prek.toml` or `.pre-commit-config.yaml` found in the "
                "current directory or parent directories."
            ),
        )
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which_available("prek"):
            success, output = run_precommit_checks()
        assert success is False
        assert output is None


# ── TestHandlePrecommitCommand ────────────────────────────────────────────


class TestHandlePrecommitCommand:
    """Tests for handle_precommit_command — /pre-commit command."""

    @patch("gptme.tools.precommit.run_precommit_checks")
    def test_success_message(self, mock_checks: MagicMock):
        """Reports success when checks pass."""
        mock_checks.return_value = (True, None)
        ctx = MagicMock()
        msgs = list(handle_precommit_command(ctx))
        assert len(msgs) == 1
        assert "passed" in msgs[0].content

    @patch("gptme.tools.precommit.run_precommit_checks")
    def test_failure_message(self, mock_checks: MagicMock):
        """Reports failure output when checks fail."""
        mock_checks.return_value = (False, "Hook X failed")
        ctx = MagicMock()
        msgs = list(handle_precommit_command(ctx))
        assert len(msgs) == 1
        assert "Hook X failed" in msgs[0].content

    @patch("gptme.tools.precommit.run_precommit_checks")
    def test_not_enabled_message(self, mock_checks: MagicMock):
        """Reports not-enabled when checks return (False, None)."""
        mock_checks.return_value = (False, None)
        ctx = MagicMock()
        msgs = list(handle_precommit_command(ctx))
        assert len(msgs) == 1
        assert "not enabled" in msgs[0].content.lower()

    @patch("gptme.tools.precommit.run_precommit_checks")
    def test_undoes_command_message(self, mock_checks: MagicMock):
        """Undoes the /pre-commit command message from the log."""
        mock_checks.return_value = (True, None)
        ctx = MagicMock()
        list(handle_precommit_command(ctx))
        ctx.manager.undo.assert_called_once_with(1, quiet=True)

    @patch("gptme.tools.precommit.run_precommit_checks")
    def test_exception_handled(self, mock_checks: MagicMock):
        """Catches and reports unexpected exceptions."""
        mock_checks.side_effect = RuntimeError("unexpected")
        ctx = MagicMock()
        msgs = list(handle_precommit_command(ctx))
        assert len(msgs) == 1
        assert "failed" in msgs[0].content.lower()


# ── TestRunPrecommitOnFile ────────────────────────────────────────────────


class TestRunPrecommitOnFile:
    """Tests for run_precommit_on_file — per-file save hook."""

    def test_skips_when_checks_disabled(self, tmp_path: Path, monkeypatch):
        """Yields nothing when use_checks returns False."""
        monkeypatch.chdir(tmp_path)
        with _mock_config({"GPTME_CHECK": "false"}):
            msgs = list(
                run_precommit_on_file(None, tmp_path, tmp_path / "f.py", "content")
            )
        assert msgs == []

    def test_skips_when_per_file_disabled(self, tmp_path: Path, monkeypatch):
        """Yields nothing when per-file checks are disabled."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        with (
            _mock_config({"GPTME_CHECK": "true", "GPTME_CHECK_PER_FILE": "false"}),
            _mock_which(),
        ):
            msgs = list(
                run_precommit_on_file(None, tmp_path, tmp_path / "f.py", "content")
            )
        assert msgs == []

    @patch("subprocess.run")
    def test_check_passes(self, mock_run: MagicMock, tmp_path: Path, monkeypatch):
        """Yields hidden success message when file passes checks."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_run.return_value = _mock_subprocess_run()
        with (
            _mock_config({"GPTME_CHECK": "true", "GPTME_CHECK_PER_FILE": "true"}),
            _mock_which(),
        ):
            msgs = list(
                run_precommit_on_file(None, tmp_path, tmp_path / "f.py", "content")
            )
        assert len(msgs) == 1
        assert "passed" in msgs[0].content.lower()
        assert msgs[0].hide is True

    @patch("subprocess.run")
    def test_uses_prek_when_only_prek_installed(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch
    ):
        """Per-file hook invokes prek when pre-commit is absent."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_run.return_value = _mock_subprocess_run()
        with (
            _mock_config({"GPTME_CHECK": "true", "GPTME_CHECK_PER_FILE": "true"}),
            _mock_which_available("prek"),
        ):
            list(run_precommit_on_file(None, tmp_path, tmp_path / "f.py", "content"))
        calls = [call.args[0] for call in mock_run.call_args_list]
        assert calls[0] == [os.path.abspath("/usr/bin/prek"), "--version"]
        assert calls[1] == [
            os.path.abspath("/usr/bin/prek"),
            "run",
            "--files",
            str(tmp_path / "f.py"),
        ]

    @patch("subprocess.run")
    def test_check_fails(self, mock_run: MagicMock, tmp_path: Path, monkeypatch):
        """Yields visible failure message when file fails checks."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        # First call: version check passes; second call: actual check fails
        mock_run.side_effect = [
            _mock_subprocess_run(),  # --version
            _mock_subprocess_run(returncode=1, stdout="lint error found"),
        ]
        with (
            _mock_config({"GPTME_CHECK": "true", "GPTME_CHECK_PER_FILE": "true"}),
            _mock_which(),
        ):
            msgs = list(
                run_precommit_on_file(None, tmp_path, tmp_path / "f.py", "content")
            )
        assert len(msgs) == 1
        assert "failed" in msgs[0].content.lower()

    @patch("subprocess.run")
    def test_timeout_handled(self, mock_run: MagicMock, tmp_path: Path, monkeypatch):
        """Yields hidden timeout message when check times out."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_run.side_effect = [
            _mock_subprocess_run(),  # --version
            subprocess.TimeoutExpired("pre-commit", 30),
        ]
        with (
            _mock_config({"GPTME_CHECK": "true", "GPTME_CHECK_PER_FILE": "true"}),
            _mock_which(),
        ):
            msgs = list(
                run_precommit_on_file(None, tmp_path, tmp_path / "f.py", "content")
            )
        assert len(msgs) == 1
        assert "timed out" in msgs[0].content.lower()

    @patch("subprocess.run")
    def test_precommit_not_available(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch
    ):
        """Skips when pre-commit --version fails."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_run.return_value = _mock_subprocess_run(returncode=1)
        with (
            _mock_config({"GPTME_CHECK": "true", "GPTME_CHECK_PER_FILE": "true"}),
            _mock_which(),
        ):
            msgs = list(
                run_precommit_on_file(None, tmp_path, tmp_path / "f.py", "content")
            )
        assert msgs == []


# ── TestCheckPrecommitAvailable ───────────────────────────────────────────


class TestCheckPrecommitAvailable:
    """Tests for check_precommit_available — binary detection."""

    def test_available_when_found(self):
        with _mock_which():
            assert check_precommit_available() is True

    def test_available_with_prek_only(self):
        with _mock_which_available("prek"):
            assert check_precommit_available() is True

    def test_unavailable_when_not_found(self):
        with _mock_which(None):
            assert check_precommit_available() is False


# ── TestRunFullPrecommitChecks ────────────────────────────────────────────


class TestRunFullPrecommitChecks:
    """Tests for run_full_precommit_checks — turn-post hook."""

    def test_skips_when_checks_disabled(self, tmp_path: Path, monkeypatch):
        """Yields nothing when checks disabled."""
        monkeypatch.chdir(tmp_path)
        manager = MagicMock()
        with _mock_config({"GPTME_CHECK": "false"}):
            msgs = list(run_full_precommit_checks(manager))
        assert msgs == []

    @patch("gptme.tools.precommit.check_for_modifications")
    def test_skips_when_no_modifications(
        self, mock_mods: MagicMock, tmp_path: Path, monkeypatch
    ):
        """Yields nothing when no file modifications detected."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_mods.return_value = False
        manager = MagicMock()
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            msgs = list(run_full_precommit_checks(manager))
        assert msgs == []

    @patch("gptme.tools.precommit.run_precommit_checks")
    @patch("gptme.tools.precommit.check_for_modifications")
    def test_success_yields_hidden_message(
        self,
        mock_mods: MagicMock,
        mock_checks: MagicMock,
        tmp_path: Path,
        monkeypatch,
    ):
        """Yields hidden success message when checks pass."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_mods.return_value = True
        mock_checks.return_value = (True, None)
        manager = MagicMock()
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            msgs = list(run_full_precommit_checks(manager))
        assert len(msgs) == 1
        msg = msgs[0]
        assert isinstance(msg, Message)
        assert msg.hide is True
        assert "passed" in msg.content.lower()

    @patch("gptme.tools.precommit.run_precommit_checks")
    @patch("gptme.tools.precommit.check_for_modifications")
    def test_failure_yields_message_and_stop_propagation(
        self,
        mock_mods: MagicMock,
        mock_checks: MagicMock,
        tmp_path: Path,
        monkeypatch,
    ):
        """Yields failure message + StopPropagation to block autocommit."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_mods.return_value = True
        mock_checks.return_value = (False, "ruff check failed")
        manager = MagicMock()
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            results = list(run_full_precommit_checks(manager))
        msgs = [r for r in results if isinstance(r, Message)]
        stops = [r for r in results if isinstance(r, StopPropagation)]
        assert len(msgs) == 1
        assert "ruff check failed" in msgs[0].content
        assert len(stops) == 1

    @patch("gptme.tools.precommit.run_precommit_checks")
    @patch("gptme.tools.precommit.check_for_modifications")
    def test_uses_modified_files_mode(
        self,
        mock_mods: MagicMock,
        mock_checks: MagicMock,
        tmp_path: Path,
        monkeypatch,
    ):
        """Runs checks with all_files=False (only modified files)."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_mods.return_value = True
        mock_checks.return_value = (True, None)
        manager = MagicMock()
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            list(run_full_precommit_checks(manager))
        mock_checks.assert_called_once_with(all_files=False)

    @patch("gptme.tools.precommit.run_precommit_checks")
    @patch("gptme.tools.precommit.check_for_modifications")
    def test_exception_handled(
        self,
        mock_mods: MagicMock,
        mock_checks: MagicMock,
        tmp_path: Path,
        monkeypatch,
    ):
        """Catches unexpected exceptions."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []")
        mock_mods.return_value = True
        mock_checks.side_effect = RuntimeError("unexpected")
        manager = MagicMock()
        with _mock_config({"GPTME_CHECK": "true"}), _mock_which():
            msgs = list(run_full_precommit_checks(manager))
        assert len(msgs) == 1
        msg = msgs[0]
        assert isinstance(msg, Message)
        assert "failed" in msg.content.lower()


# ── TestToolSpec ──────────────────────────────────────────────────────────


class TestToolSpec:
    """Tests for precommit tool registration."""

    def test_tool_name(self):
        assert tool.name == "precommit"

    def test_tool_has_description(self):
        assert tool.desc is not None
        assert "pre-commit" in tool.desc.lower()

    def test_tool_has_hooks(self):
        assert tool.hooks is not None
        assert "precommit_file" in tool.hooks
        assert "precommit_full" in tool.hooks

    def test_file_hook_type(self):
        hook_name, hook_fn, priority = tool.hooks["precommit_file"]
        assert hook_name == "file.save.post"
        assert callable(hook_fn)
        assert priority == 5

    def test_full_hook_type(self):
        hook_name, hook_fn, priority = tool.hooks["precommit_full"]
        assert hook_name == "turn.post"
        assert callable(hook_fn)
        assert priority == 5

    def test_tool_has_command(self):
        assert tool.commands is not None
        assert "pre-commit" in tool.commands
        assert callable(tool.commands["pre-commit"])

    def test_full_hook_higher_priority_than_autocommit(self):
        """Pre-commit (5) runs before autocommit (1) since higher = first."""
        _, _, priority = tool.hooks["precommit_full"]
        _, _, autocommit_priority = autocommit_tool.hooks["autocommit"]
        assert priority == 5
        assert autocommit_priority == 1
        assert priority > autocommit_priority
