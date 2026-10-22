"""Installation entry points must use Python supported by project metadata."""

import re
import subprocess
from pathlib import Path

import pytest
import tomli as tomllib
from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parent.parent
SUPPORTED = SpecifierSet(
    tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["requires-python"]
)


@pytest.mark.parametrize("workflow", ["lint.yml", "test.yml"])
def test_ci_python_versions_are_supported(workflow: str) -> None:
    text = (ROOT / ".github" / "workflows" / workflow).read_text()
    versions = re.findall(r"python-version: ['\"](3\.\d+)['\"]", text)
    assert versions, f"No explicit Python versions found in {workflow}"
    for version in versions:
        assert version in SUPPORTED, f"{workflow} uses unsupported Python {version}"


def test_dev_docker_python_is_supported() -> None:
    text = (ROOT / "scripts" / "Dockerfile.dev").read_text()
    match = re.search(r"^FROM python:(3\.\d+)-slim", text)
    assert match
    assert match[1] in SUPPORTED


def test_computer_docker_uses_supported_ubuntu_python_in_venv() -> None:
    text = (ROOT / "scripts" / "Dockerfile.computer").read_text()
    # Ubuntu 24.04 supplies Python 3.12. Its system Python is externally managed,
    # so pip and Playwright must run inside the image's application venv.
    assert "FROM ubuntu:24.04" in text
    assert "3.12" in SUPPORTED
    assert "python3-venv" in text
    assert "python3 -m venv /opt/venv" in text
    assert 'ENV PATH="/opt/venv/bin:' in text
    assert "python3.10" not in text


def test_minimum_python_runs_tests_without_coverage() -> None:
    """Keep 3.11 behavior tests, without its SetAsyncExc/tracer hang."""
    workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text()
    minimal = workflow.split("  test-minimal:", 1)[1].split("  test-py315:", 1)[0]
    assert "make test NO_COVERAGE=1" in minimal
    assert "codecov/codecov-action" in minimal  # JUnit upload is retained.
    assert "flags: no-api,no-extras" not in minimal

    for no_coverage in (False, True):
        result = subprocess.run(
            ["make", "--dry-run", "test", *(["NO_COVERAGE=1"] if no_coverage else [])],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        assert ("--cov=gptme" in result.stdout) is not no_coverage
        assert "--junitxml=junit.xml" in result.stdout
        assert "--timeout 60" in result.stdout
        assert "not slow and not eval" in result.stdout


def test_readme_python_prerequisites_are_supported() -> None:
    versions = re.findall(
        r"Python (3\.\d+)(?: or newer|\+)", (ROOT / "README.md").read_text()
    )
    assert versions
    for version in versions:
        assert version in SUPPORTED
