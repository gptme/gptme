import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/validate_release_package.py"
_SPEC = importlib.util.spec_from_file_location("validate_release_package", _SCRIPT)
assert _SPEC and _SPEC.loader
validate_release_package = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(validate_release_package)
validate_package = validate_release_package.validate_package


@pytest.mark.parametrize("archive_type", ["wheel", "sdist"])
def test_validate_package_accepts_bundled_webui(
    tmp_path: Path, archive_type: str
) -> None:
    archive = _write_archive(
        tmp_path,
        archive_type,
        {
            "gptme/server/webui-dist/index.html": b"<html>modern</html>",
            "gptme/server/webui-dist/assets/index-abc.js": b"console.log('ok')",
        },
    )

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
