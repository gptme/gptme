"""Docker build inputs must follow the project's committed dependency lockfile."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "name",
    ["Dockerfile", "Dockerfile.computer", "Dockerfile.dev", "Dockerfile.selfhost"],
)
def test_docker_lockfile_inputs_exist(name: str) -> None:
    for line in (ROOT / "scripts" / name).read_text().splitlines():
        if line.startswith("COPY ") and "pyproject.toml" in line:
            for source in line.split()[1:-1]:
                assert (ROOT / source).exists(), f"{name} copies missing input {source}"


def test_docker_exports_exclude_local_project() -> None:
    """Requirements are installed outside the source tree alongside a built wheel."""
    dockerfile = (ROOT / "scripts" / "Dockerfile").read_text()
    for line in dockerfile.splitlines():
        if "uv export " in line:
            assert "--frozen" in line
            assert "--no-dev" in line
            assert "--no-emit-project" in line
    assert "-r /tmp/requirements.txt /tmp/gptme-*.whl" in dockerfile
    assert "requirements-browser.txt" in dockerfile
    assert (
        "requirements-browser.txt" in (ROOT / "scripts" / "Dockerfile.eval").read_text()
    )
