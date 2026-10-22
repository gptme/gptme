"""Installation entry points must use Python supported by project metadata."""

import re
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


def test_readme_python_prerequisites_are_supported() -> None:
    versions = re.findall(
        r"Python (3\.\d+)(?: or newer|\+)", (ROOT / "README.md").read_text()
    )
    assert versions
    for version in versions:
        assert version in SUPPORTED
