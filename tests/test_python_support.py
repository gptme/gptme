"""Installation entry points must use Python supported by project metadata."""

import os
import re
import subprocess
from pathlib import Path

import pytest
import tomli as tomllib
from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
SUPPORTED = SpecifierSet(
    tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["requires-python"]
)


def _supports_selector(selector: str) -> bool:
    """Minor selectors track patched releases; full versions name exact releases."""
    version = Version(selector)
    if len(version.release) == 2:
        # Consider the declared lower bounds as well as the .0 release, so a
        # moving 3.11 selector can satisfy >=3.11.5 without admitting 3.10.
        candidates = [version] + [
            Version(spec.version) for spec in SUPPORTED if spec.operator == ">="
        ]
        return any(
            candidate.release[:2] == version.release and candidate in SUPPORTED
            for candidate in candidates
        )
    return version in SUPPORTED


@pytest.mark.parametrize(
    ("selector", "supported"),
    [
        ("3.10", False),
        ("3.11", True),
        ("3.11.4", False),
        ("3.11.5", True),
        ("3.12", True),
        ("3.15", True),
        ("3.16", False),
    ],
)
def test_python_selector_support(selector: str, supported: bool) -> None:
    assert _supports_selector(selector) is supported


@pytest.mark.parametrize("workflow", ["lint.yml", "test.yml"])
def test_ci_python_versions_are_supported(workflow: str) -> None:
    text = (ROOT / ".github" / "workflows" / workflow).read_text()
    versions = re.findall(r"python-version: ['\"](3\.\d+(?:\.\d+)?)['\"]", text)
    assert versions, f"No explicit Python versions found in {workflow}"
    for version in versions:
        assert _supports_selector(version), (
            f"{workflow} uses unsupported Python {version}"
        )


def test_dev_docker_python_is_supported() -> None:
    text = (ROOT / "scripts" / "Dockerfile.dev").read_text()
    match = re.search(r"^FROM python:(3\.\d+(?:\.\d+)?)-slim", text)
    assert match
    assert _supports_selector(match[1])


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

    # A parent `make test NO_COVERAGE=1 SRCDIRS=...` exports its overrides.
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "MAKEFLAGS",
            "MFLAGS",
            "MAKEOVERRIDES",
            "NO_COVERAGE",
            "SRCDIRS",
            "SLOW",
            "EVAL",
            "PROFILE",
        }
    }
    for no_coverage in (False, True):
        result = subprocess.run(
            ["make", "--dry-run", "test", *(["NO_COVERAGE=1"] if no_coverage else [])],
            cwd=ROOT,
            env=env,
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
        r"Python (3\.\d+(?:\.\d+)?)(?: or newer|\+)", (ROOT / "README.md").read_text()
    )
    assert versions
    for version in versions:
        assert version in SUPPORTED
