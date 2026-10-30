"""Build real distributions, including generated/ignored release payloads."""

import configparser
import email.parser
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
import tomlkit
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def test_dependency_manifest_is_unambiguous() -> None:
    manifest = tomlkit.parse((ROOT / "pyproject.toml").read_text())
    # Dependabot's uv parser routes any tool.poetry table to Poetry dependencies,
    # even when it contains only build configuration and dependencies are PEP 621.
    assert "poetry" not in manifest.get("tool", {})
    assert manifest["project"]["dependencies"]


def _wheel_files(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_build_distribution_roundtrip(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("distribution build requires uv")
    source = tmp_path / "source"
    source.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE", ".gitignore"):
        shutil.copyfile(ROOT / name, source / name)
    shutil.copytree(
        ROOT / "gptme",
        source / "gptme",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "webui-dist"),
    )
    (source / "media").mkdir()
    media = [
        path
        for path in (ROOT / "media").iterdir()
        if path.suffix in {".wav", ".svg", ".png"}
    ]
    assert {"bell.wav", "icon.svg", "icon-mono.svg", "logo.png"} <= {
        path.name for path in media
    }
    for path in media:
        shutil.copyfile(path, source / "media" / path.name)
    # Exercise generated, gitignored assets, including nested ignored file types.
    generated = {
        "gptme/server/webui-dist/index.html": b"<html>release fixture</html>",
        "gptme/server/webui-dist/assets/index-test.js": b"console.log('release')",
        "gptme/server/webui-dist/assets/nested/icon.png": b"release image fixture",
    }
    for name, data in generated.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (source / "media" / "not-a-release.txt").write_text("do not ship")

    def build(directory: Path, output: Path, *args: str) -> None:
        subprocess.run(
            [uv, "build", "--no-sources", "--out-dir", str(output), *args],
            cwd=directory,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )

    dist = tmp_path / "dist"
    build(source, dist, "--wheel")
    build(source, dist, "--sdist")
    wheel = _wheel_files(next(dist.glob("*.whl")))
    expected = {
        str(path.relative_to(source)): path.read_bytes()
        for path in (source / "gptme").rglob("*")
        if path.is_file()
    }
    expected.update({f"media/{path.name}": path.read_bytes() for path in media})
    expected.update(generated)
    for name, data in expected.items():
        assert wheel.get(name) == data, name
    assert "media/not-a-release.txt" not in wheel
    manifest = tomlkit.parse((source / "pyproject.toml").read_text())["project"]
    metadata_name = next(name for name in wheel if name.endswith(".dist-info/METADATA"))
    metadata = email.parser.BytesParser().parsebytes(wheel[metadata_name])
    assert metadata["Name"] == manifest["name"]
    assert metadata["Version"] == manifest["version"]
    assert set(metadata.get_all("Provides-Extra", [])) == set(
        manifest["optional-dependencies"]
    )

    def requirement_key(value: str) -> tuple[str, str, str | None]:
        requirement = Requirement(value)
        return (
            canonicalize_name(requirement.name),
            str(requirement.specifier),
            str(requirement.marker) if requirement.marker else None,
        )

    # Build the metadata requirements directly from PEP 621, including each
    # extra's conjunction with its original environment marker.
    requirements = list(manifest["dependencies"])
    for extra, dependencies in manifest["optional-dependencies"].items():
        for value in dependencies:
            requirement = Requirement(value)
            marker = f"extra == '{extra}'"
            if requirement.marker:
                marker = f"({requirement.marker}) and {marker}"
            requirement.marker = None
            requirements.append(f"{requirement}; {marker}")
    assert {
        requirement_key(value) for value in metadata.get_all("Requires-Dist", [])
    } == {requirement_key(value) for value in requirements}
    entry_points = configparser.ConfigParser()
    entry_points.read_string(
        wheel[
            next(name for name in wheel if name.endswith(".dist-info/entry_points.txt"))
        ].decode()
    )
    assert dict(entry_points["console_scripts"]) == dict(manifest["scripts"])
    for group, entries in manifest["entry-points"].items():
        assert dict(entry_points[group]) == dict(entries)

    unpacked = tmp_path / "unpacked"
    with tarfile.open(next(dist.glob("*.tar.gz"))) as archive:
        # Archives are built above from this test's own source, not user input.
        archive.extractall(unpacked, filter="data")
    extracted = next(unpacked.iterdir())
    for name, data in expected.items():
        assert (extracted / name).read_bytes() == data, name
    assert not (extracted / "media/not-a-release.txt").exists()
    rebuilt_dist = tmp_path / "rebuilt"
    build(extracted, rebuilt_dist, "--wheel")
    rebuilt = _wheel_files(next(rebuilt_dist.glob("*.whl")))
    assert wheel == rebuilt
