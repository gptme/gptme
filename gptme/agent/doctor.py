"""Agent workspace health checks (gptme-agent doctor).

Validates that an agent workspace is properly configured for autonomous operation.
Checks core files, configuration, directory structure, tools, and more.
"""

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Result of a single health check."""

    name: str
    status: str  # "pass", "warn", "fail"
    message: str

    @property
    def emoji(self) -> str:
        return {"pass": "✓", "warn": "!", "fail": "✗"}[self.status]


@dataclass
class DoctorReport:
    """Aggregated report from all health checks."""

    results: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, status: str, message: str) -> None:
        self.results.append(CheckResult(name=name, status=status, message=message))

    def passed(self, name: str, message: str) -> None:
        self.add(name, "pass", message)

    def warn(self, name: str, message: str) -> None:
        self.add(name, "warn", message)

    def fail(self, name: str, message: str) -> None:
        self.add(name, "fail", message)

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.status == "fail")

    @property
    def warnings(self) -> int:
        return sum(1 for r in self.results if r.status == "warn")

    @property
    def passes(self) -> int:
        return sum(1 for r in self.results if r.status == "pass")


def check_core_files(workspace: Path, report: DoctorReport) -> None:
    """Check that core identity files exist and have content."""
    core_files = [
        ("ABOUT.md", "Agent identity", 10),
        ("gptme.toml", "gptme configuration", 3),
        ("ARCHITECTURE.md", "Architecture docs", 10),
    ]

    for filename, desc, min_lines in core_files:
        path = workspace / filename
        if not path.exists():
            report.fail(desc, f"{filename} not found")
            continue

        lines = len(path.read_text().splitlines())
        if lines < min_lines:
            report.warn(
                desc,
                f"{filename} exists but only {lines} lines (expected >={min_lines})",
            )
        else:
            report.passed(desc, f"{filename} ({lines} lines)")

    # Check for AGENTS.md or CLAUDE.md (at least one)
    has_agents = (workspace / "AGENTS.md").exists()
    has_claude = (workspace / "CLAUDE.md").exists()
    if has_agents or has_claude:
        found = []
        if has_agents:
            found.append("AGENTS.md")
        if has_claude:
            found.append("CLAUDE.md")
        report.passed("Agent instructions", f"{' + '.join(found)} present")
    else:
        report.fail("Agent instructions", "neither AGENTS.md nor CLAUDE.md found")


def check_gptme_toml(workspace: Path, report: DoctorReport) -> None:
    """Check gptme.toml configuration."""
    toml_path = workspace / "gptme.toml"
    if not toml_path.exists():
        return  # Already reported in core_files

    content = toml_path.read_text()

    # Check for agent name
    if "[agent]" in content and "name" in content:
        # Extract name (simple parsing)
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("name") and "=" in line:
                name_val = line.split("=", 1)[1].strip().strip('"').strip("'")
                report.passed("Agent name", f"configured: {name_val}")
                break
    else:
        report.warn("Agent name", "no [agent] name configured in gptme.toml")

    # Check for prompt section
    if "[prompt]" in content:
        report.passed("Prompt config", "section present")
    else:
        report.warn("Prompt config", "no [prompt] section (auto-includes won't work)")

    # Check for context_cmd
    if "context_cmd" in content:
        report.passed("Context command", "configured")
    else:
        report.warn("Context command", "not configured (no dynamic context)")


def check_directories(workspace: Path, report: DoctorReport, fix: bool = False) -> None:
    """Check required directory structure."""
    required_dirs = [
        ("tasks", "Task directory"),
        ("journal", "Journal directory"),
        ("knowledge", "Knowledge base"),
        ("lessons", "Lessons directory"),
    ]
    optional_dirs = [
        ("people", "People directory"),
        ("skills", "Skills directory"),
        ("scripts", "Scripts directory"),
    ]

    for dirname, desc in required_dirs:
        path = workspace / dirname
        if path.is_dir():
            md_count = len(list(path.glob("*.md")))
            report.passed(desc, f"{dirname}/ ({md_count} .md files)")
        else:
            report.fail(desc, f"{dirname}/ not found")
            if fix:
                path.mkdir(parents=True, exist_ok=True)
                logger.info("Created %s/", dirname)

    for dirname, desc in optional_dirs:
        path = workspace / dirname
        if path.is_dir():
            report.passed(desc, f"{dirname}/ present")
        else:
            report.warn(desc, f"{dirname}/ not found (optional)")


def check_git(workspace: Path, report: DoctorReport) -> None:
    """Check git configuration."""
    git_dir = workspace / ".git"
    if not git_dir.exists():
        report.fail("Git repo", "not a git repository")
        return

    report.passed("Git repo", "initialized")

    # Check for remote
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            report.passed("Git remote", f"origin: {result.stdout.strip()}")
        else:
            report.warn("Git remote", "no 'origin' remote configured")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        report.warn("Git remote", "could not check (git not available)")

    # Check for pre-commit hooks
    hooks_dir = workspace / ".git" / "hooks"
    pre_commit = hooks_dir / "pre-commit"
    if pre_commit.exists() and pre_commit.stat().st_size > 0:
        report.passed("Pre-commit hooks", "installed")
    else:
        report.warn(
            "Pre-commit hooks",
            "not installed (run: prek install or pre-commit install)",
        )


def check_tools(report: DoctorReport) -> None:
    """Check required tools are available."""
    required_tools = [
        ("gptme", "gptme agent framework"),
        ("git", "version control"),
        ("python3", "Python runtime"),
    ]
    optional_tools = [
        ("uv", "Python package manager"),
        ("gh", "GitHub CLI"),
        ("prek", "fast pre-commit runner"),
        ("pre-commit", "pre-commit hooks"),
    ]

    for tool, desc in required_tools:
        if shutil.which(tool):
            report.passed(desc, f"{tool} available")
        else:
            report.fail(desc, f"{tool} not found in PATH")

    for tool, desc in optional_tools:
        if shutil.which(tool):
            report.passed(desc, f"{tool} available")
        else:
            report.warn(desc, f"{tool} not found (optional)")


def check_python_env(workspace: Path, report: DoctorReport) -> None:
    """Check Python environment setup."""
    pyproject = workspace / "pyproject.toml"
    if pyproject.exists():
        report.passed("pyproject.toml", "present")
    else:
        report.warn("pyproject.toml", "not found (no Python project configuration)")
        return

    venv = workspace / ".venv"
    if venv.is_dir():
        report.passed("Virtual environment", ".venv/ present")
    else:
        report.warn("Virtual environment", ".venv/ not found (run: uv sync)")

    lockfile = workspace / "uv.lock"
    if lockfile.exists():
        report.passed("Lock file", "uv.lock present")
    else:
        lockfile_poetry = workspace / "poetry.lock"
        if lockfile_poetry.exists():
            report.passed("Lock file", "poetry.lock present")
        else:
            report.warn("Lock file", "no lock file found")


def check_submodules(workspace: Path, report: DoctorReport, fix: bool = False) -> None:
    """Check git submodules are initialized."""
    gitmodules = workspace / ".gitmodules"
    if not gitmodules.exists():
        return  # No submodules to check

    try:
        result = subprocess.run(
            ["git", "submodule", "status"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            report.warn("Submodules", "could not check status")
            return

        uninitialized = []
        for line in result.stdout.strip().splitlines():
            if line.startswith("-"):
                # Uninitialized submodule
                parts = line.split()
                if len(parts) >= 2:
                    uninitialized.append(parts[1])

        if uninitialized:
            report.warn(
                "Submodules",
                f"{len(uninitialized)} uninitialized: {', '.join(uninitialized)}",
            )
            if fix:
                subprocess.run(
                    ["git", "submodule", "update", "--init", "--recursive"],
                    cwd=workspace,
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
                logger.info("Initialized submodules")
        else:
            report.passed("Submodules", "all initialized")

    except (subprocess.TimeoutExpired, FileNotFoundError):
        report.warn("Submodules", "could not check (git not available)")


def check_context_script(workspace: Path, report: DoctorReport) -> None:
    """Check context generation script exists and is executable."""
    # Check common locations
    candidates = [
        workspace / "scripts" / "context.sh",
        workspace / "scripts" / "context.py",
    ]

    for path in candidates:
        if path.exists():
            if path.stat().st_mode & 0o111:  # executable
                report.passed("Context script", f"{path.name} (executable)")
            else:
                report.warn("Context script", f"{path.name} exists but not executable")
            return

    report.warn("Context script", "no context script found in scripts/")


def check_autonomous_run(workspace: Path, report: DoctorReport) -> None:
    """Check autonomous run infrastructure."""
    candidates = [
        workspace / "scripts" / "runs" / "autonomous" / "autonomous-run.sh",
        workspace / "scripts" / "run.sh",
        workspace / "run.sh",
    ]

    for path in candidates:
        if path.exists():
            if path.stat().st_mode & 0o111:
                report.passed(
                    "Run script", f"{path.relative_to(workspace)} (executable)"
                )
            else:
                report.warn(
                    "Run script",
                    f"{path.relative_to(workspace)} exists but not executable",
                )
            return

    report.warn("Run script", "no autonomous run script found")


def run_doctor(workspace: Path, fix: bool = False) -> DoctorReport:
    """Run all health checks on the workspace.

    Args:
        workspace: Path to the agent workspace root.
        fix: If True, attempt to fix simple issues automatically.

    Returns:
        DoctorReport with all check results.
    """
    report = DoctorReport()

    check_core_files(workspace, report)
    check_gptme_toml(workspace, report)
    check_directories(workspace, report, fix=fix)
    check_git(workspace, report)
    check_tools(report)
    check_python_env(workspace, report)
    check_submodules(workspace, report, fix=fix)
    check_context_script(workspace, report)
    check_autonomous_run(workspace, report)

    return report
