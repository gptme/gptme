import importlib.util
import io
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/validate_release_package.py"
_SPEC = importlib.util.spec_from_file_location("validate_release_package", _SCRIPT)
assert _SPEC and _SPEC.loader
validate_release_package = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(validate_release_package)
main: Callable[[], int] = validate_release_package.main
validate_package: Callable[[Path], None] = validate_release_package.validate_package
_RUNTIME_ASSETS: tuple[str, ...] = validate_release_package._RUNTIME_ASSETS


@pytest.mark.parametrize("archive_type", ["wheel", "sdist"])
def test_validate_package_accepts_bundled_webui(
    tmp_path: Path, archive_type: str
) -> None:
    archive = _write_archive(
        tmp_path,
        archive_type,
        {
            **dict.fromkeys(_RUNTIME_ASSETS, b"asset"),
            "gptme/server/webui-dist/index.html": b"<html>modern</html>",
            "gptme/server/webui-dist/assets/index-abc.js": b"console.log('ok')",
        },
    )

    validate_package(archive)


@pytest.mark.parametrize("archive_type", ["wheel", "sdist"])
@pytest.mark.parametrize(
    "missing", ["media/bell.wav", "media/icon.svg", "gptme/data/faq.yaml"]
)
def test_validate_package_rejects_missing_runtime_asset(
    tmp_path: Path, archive_type: str, missing: str
) -> None:
    """A complete web UI must not hide missing sounds, images or package data."""
    members = {
        **dict.fromkeys(_RUNTIME_ASSETS, b"asset"),
        "gptme/server/webui-dist/index.html": b"html",
        "gptme/server/webui-dist/assets/index-abc.js": b"js",
    }
    del members[missing]
    archive = _write_archive(tmp_path, archive_type, members)

    with pytest.raises(ValueError, match=missing):
        validate_package(archive)


@pytest.mark.parametrize(
    ("members", "missing"),
    [
        ({"gptme/server/webui-dist/assets/index-abc.js": b"js"}, "index.html"),
        ({"gptme/server/webui-dist/index.html": b"html"}, "assets/*"),
    ],
)
def test_validate_package_rejects_incomplete_webui(
    tmp_path: Path, members: dict[str, bytes], missing: str
) -> None:
    archive = _write_archive(tmp_path, "wheel", members)

    with pytest.raises(ValueError, match=missing.replace("*", r"\*")):
        validate_package(archive)


def test_validate_package_rejects_unsupported_archive(tmp_path: Path) -> None:
    archive = tmp_path / "gptme.zip"
    archive.write_bytes(b"")

    with pytest.raises(ValueError, match="unsupported package archive"):
        validate_package(archive)


def test_main_validates_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _write_archive(
        tmp_path,
        "wheel",
        {
            **dict.fromkeys(_RUNTIME_ASSETS, b"asset"),
            "gptme/server/webui-dist/index.html": b"html",
            "gptme/server/webui-dist/assets/index-abc.js": b"js",
        },
    )
    monkeypatch.setattr("sys.argv", ["validate-release-package", str(archive)])

    assert main() == 0
    assert capsys.readouterr().out == f"Validated runtime assets in {archive}\n"


def test_main_reports_invalid_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = tmp_path / "gptme.zip"
    archive.write_bytes(b"")
    monkeypatch.setattr("sys.argv", ["validate-release-package", str(archive)])

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 1
    assert capsys.readouterr().err == f"error: unsupported package archive: {archive}\n"


def _write_archive(
    tmp_path: Path, archive_type: str, members: dict[str, bytes]
) -> Path:
    if archive_type == "wheel":
        archive = tmp_path / "gptme-0.0.0-py3-none-any.whl"
        with zipfile.ZipFile(archive, "w") as wheel:
            for name, data in members.items():
                wheel.writestr(name, data)
        return archive

    archive = tmp_path / "gptme-0.0.0.tar.gz"
    with tarfile.open(archive, "w:gz") as sdist:
        for name, data in members.items():
            info = tarfile.TarInfo(f"gptme-0.0.0/{name}")
            info.size = len(data)
            sdist.addfile(info, io.BytesIO(data))
    return archive
