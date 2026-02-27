"""Tests for gptme-agent doctor command."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.agent.cli import main
from gptme.agent.doctor import (
    DoctorReport,
    check_core_files,
    check_directories,
    check_git,
    check_gptme_toml,
    check_python_env,
    check_tools,
    run_doctor,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a minimal workspace for testing."""
    return tmp_path


@pytest.fixture
def full_workspace(tmp_path: Path) -> Path:
    """Create a fully configured workspace for testing."""
    ws = tmp_path

    # Core files
    (ws / "ABOUT.md").write_text("# About\n" + "Line\n" * 15)
    (ws / "gptme.toml").write_text(
        '[agent]\nname = "TestAgent"\n\n[prompt]\nfiles = ["ABOUT.md"]\ncontext_cmd = "echo hello"\n'
    )
    (ws / "ARCHITECTURE.md").write_text("# Architecture\n" + "Line\n" * 15)
    (ws / "AGENTS.md").write_text("# Agent Instructions\n" + "Line\n" * 15)

    # Directories
    for d in ["tasks", "journal", "knowledge", "lessons", "people", "scripts"]:
        (ws / d).mkdir()

    # Python env
    (ws / "pyproject.toml").write_text("[project]\nname = 'test'\n")
    (ws / ".venv").mkdir()
    (ws / "uv.lock").write_text("")

    # Git
    import subprocess

    subprocess.run(["git", "init"], cwd=ws, capture_output=True, check=False)
    subprocess.run(["git", "add", "."], cwd=ws, capture_output=True, check=False)
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=ws,
        capture_output=True,
        check=False,
        env={
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@test.com",
            "PATH": "/usr/bin:/bin",
        },
    )

    # Context script
    ctx_script = ws / "scripts" / "context.sh"
    ctx_script.write_text("#!/bin/bash\necho hello")
    ctx_script.chmod(0o755)

    # Run script
    run_dir = ws / "scripts" / "runs" / "autonomous"
    run_dir.mkdir(parents=True)
    run_script = run_dir / "autonomous-run.sh"
    run_script.write_text("#!/bin/bash\necho run")
    run_script.chmod(0o755)

    return ws


class TestDoctorReport:
    """Tests for DoctorReport aggregation."""

    def test_empty_report(self):
        report = DoctorReport()
        assert report.errors == 0
        assert report.warnings == 0
        assert report.passes == 0

    def test_mixed_results(self):
        report = DoctorReport()
        report.passed("a", "ok")
        report.passed("b", "ok")
        report.warn("c", "warn")
        report.fail("d", "fail")
        assert report.passes == 2
        assert report.warnings == 1
        assert report.errors == 1

    def test_add(self):
        report = DoctorReport()
        report.add("test", "pass", "message")
        assert len(report.results) == 1
        assert report.results[0].name == "test"
        assert report.results[0].emoji == "✓"


class TestCheckCoreFiles:
    """Tests for core file checks."""

    def test_missing_all(self, workspace: Path):
        report = DoctorReport()
        check_core_files(workspace, report)
        # Should fail for ABOUT.md, gptme.toml, ARCHITECTURE.md, and agent instructions
        assert report.errors == 4

    def test_empty_files(self, workspace: Path):
        (workspace / "ABOUT.md").write_text("# About\nShort")
        (workspace / "gptme.toml").write_text("[agent]\nname = 'x'\nfoo = 1\n")
        (workspace / "ARCHITECTURE.md").write_text("# Arch\nShort")
        report = DoctorReport()
        check_core_files(workspace, report)
        # ABOUT.md and ARCHITECTURE.md too short (< 10 lines), gptme.toml ok (3 lines)
        assert report.warnings == 2
        assert report.passes == 1

    def test_agents_md_present(self, workspace: Path):
        (workspace / "AGENTS.md").write_text("# Instructions")
        report = DoctorReport()
        check_core_files(workspace, report)
        assert any(
            r.status == "pass" and "AGENTS.md" in r.message for r in report.results
        )

    def test_claude_md_present(self, workspace: Path):
        (workspace / "CLAUDE.md").write_text("# Instructions")
        report = DoctorReport()
        check_core_files(workspace, report)
        assert any(
            r.status == "pass" and "CLAUDE.md" in r.message for r in report.results
        )


class TestCheckGptmeToml:
    """Tests for gptme.toml configuration checks."""

    def test_full_config(self, workspace: Path):
        (workspace / "gptme.toml").write_text(
            '[agent]\nname = "Bob"\n\n[prompt]\nfiles = []\ncontext_cmd = "echo"\n'
        )
        report = DoctorReport()
        check_gptme_toml(workspace, report)
        assert report.passes == 3
        assert report.warnings == 0

    def test_missing_sections(self, workspace: Path):
        (workspace / "gptme.toml").write_text("# empty config\n")
        report = DoctorReport()
        check_gptme_toml(workspace, report)
        assert report.warnings == 3  # name, prompt, context_cmd

    def test_no_file(self, workspace: Path):
        report = DoctorReport()
        check_gptme_toml(workspace, report)
        # Should silently skip (core_files check handles the missing file)
        assert len(report.results) == 0


class TestCheckDirectories:
    """Tests for directory structure checks."""

    def test_missing_required(self, workspace: Path):
        report = DoctorReport()
        check_directories(workspace, report)
        assert report.errors == 4  # tasks, journal, knowledge, lessons

    def test_all_present(self, workspace: Path):
        for d in ["tasks", "journal", "knowledge", "lessons"]:
            (workspace / d).mkdir()
        report = DoctorReport()
        check_directories(workspace, report)
        assert report.errors == 0

    def test_fix_creates_dirs(self, workspace: Path):
        report = DoctorReport()
        check_directories(workspace, report, fix=True)
        # Dirs should now exist
        for d in ["tasks", "journal", "knowledge", "lessons"]:
            assert (workspace / d).is_dir()

    def test_optional_missing(self, workspace: Path):
        for d in ["tasks", "journal", "knowledge", "lessons"]:
            (workspace / d).mkdir()
        report = DoctorReport()
        check_directories(workspace, report)
        # Optional dirs (people, skills, scripts) generate warnings
        assert report.warnings >= 2


class TestCheckGit:
    """Tests for git configuration checks."""

    def test_not_a_repo(self, workspace: Path):
        report = DoctorReport()
        check_git(workspace, report)
        assert report.errors == 1

    def test_valid_repo(self, workspace: Path):
        import subprocess

        subprocess.run(["git", "init"], cwd=workspace, capture_output=True, check=False)
        report = DoctorReport()
        check_git(workspace, report)
        assert any(
            r.status == "pass" and "initialized" in r.message for r in report.results
        )


class TestCheckTools:
    """Tests for tool availability checks."""

    def test_basic_tools(self):
        report = DoctorReport()
        check_tools(report)
        # git and python3 should always be available in test env
        assert any(r.status == "pass" and "git" in r.message for r in report.results)
        assert any(
            r.status == "pass" and "python3" in r.message for r in report.results
        )


class TestCheckPythonEnv:
    """Tests for Python environment checks."""

    def test_no_pyproject(self, workspace: Path):
        report = DoctorReport()
        check_python_env(workspace, report)
        assert report.warnings == 1

    def test_with_pyproject(self, workspace: Path):
        (workspace / "pyproject.toml").write_text("[project]\nname = 'test'\n")
        report = DoctorReport()
        check_python_env(workspace, report)
        assert any(
            r.status == "pass" and "present" in r.message for r in report.results
        )


class TestRunDoctor:
    """Integration tests for the full doctor run."""

    def test_empty_workspace(self, workspace: Path):
        report = run_doctor(workspace)
        assert report.errors > 0

    def test_full_workspace(self, full_workspace: Path):
        report = run_doctor(full_workspace)
        # Full workspace should have mostly passes
        assert report.passes > report.errors
        assert report.errors == 0


class TestDoctorCLI:
    """Tests for the CLI doctor command."""

    def test_doctor_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["doctor", "--help"])
        assert result.exit_code == 0
        assert "health" in result.output.lower() or "workspace" in result.output.lower()

    def test_doctor_empty_dir(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(main, ["doctor", str(tmp_path)])
        assert result.exit_code == 1  # Should fail with errors
        assert "error" in result.output.lower() or "✗" in result.output

    def test_doctor_full_workspace(self, full_workspace: Path):
        runner = CliRunner()
        result = runner.invoke(main, ["doctor", str(full_workspace)])
        assert "passed" in result.output

    def test_doctor_fix_flag(self, tmp_path: Path):
        runner = CliRunner()
        runner.invoke(main, ["doctor", str(tmp_path), "--fix"])
        # Should create missing directories
        assert (tmp_path / "tasks").is_dir()
        assert (tmp_path / "journal").is_dir()
