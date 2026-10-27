"""Final AppImage must be patched and signed before tauri-action uploads it."""

import base64
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tauri/scripts/release-appimage.sh"


def bundled_signature(trusted_comment: str) -> str:
    """Base64 minisign box, as `tauri build` writes next to each bundle."""
    box = (
        "untrusted comment: signature from tauri secret key\nSIG\n"
        f"trusted comment: {trusted_comment}\nGLOBALSIG\n"
    )
    return base64.b64encode(box.encode()).decode()


@pytest.mark.parametrize(
    "failure",
    ["", "unversioned", "build", "patch", "sign", "missing-signature", "missing-key"],
)
def test_release_appimage_order(
    tmp_path: Path, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An inherited value must not replace the per-case bundler signature.
    monkeypatch.setenv("BUNDLED_SIG", "")
    scripts = tmp_path / "tauri/scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / SCRIPT.name
    # Execute the real wrapper in a sparse fixture, not a second implementation.
    wrapper.write_text(SCRIPT.read_text())
    patcher = scripts / "build-appimage.sh"
    patcher.write_text(
        "#!/bin/bash\nset -eu\n"
        'echo patch >> "$LEDGER"\n'
        '[[ "$*" == "--patch-only" ]]\n'
        '[[ "$FAILURE" != patch ]]\n'
        "printf patched > src-tauri/target/release/bundle/appimage/test.AppImage\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npm = bin_dir / "npm"
    npm.write_text(
        "#!/bin/bash\nset -eu\n"
        'if [[ "$1 $2 $3" == "run tauri --" && "$4" == build ]]; then\n'
        ' echo build >> "$LEDGER"\n'
        ' [[ "$FAILURE" != build ]]\n'
        ' [[ "${*:5}" == "--config test-config --verbose" ]]\n'
        " mkdir -p src-tauri/target/release/bundle/appimage\n"
        " printf unpatched > src-tauri/target/release/bundle/appimage/test.AppImage\n"
        ' printf %s "$BUNDLED_SIG" > src-tauri/target/release/bundle/appimage/test.AppImage.sig\n'
        'elif [[ "$1 $2 $3 $4 $5" == "run tauri -- signer sign" ]]; then\n'
        ' file="${@: -1}"\n'
        ' echo sign "${@:6:$#-6}" >> "$LEDGER"\n'
        ' [[ "$FAILURE" != sign ]]\n'
        ' [[ "$(cat "$file")" == patched ]]\n'
        ' [[ ! -e "$file.sig" ]]\n'
        ' if [[ "$FAILURE" != missing-signature ]]; then printf final-signature > "$file.sig"; fi\n'
        "else exit 99; fi\n"
    )
    npm.chmod(0o755)
    ledger = tmp_path / "ledger"
    stamp = "timestamp:1792688400\tfile:test.AppImage"
    if failure != "unversioned":
        stamp += "\tversion:0.35.2-dev.20261026"
    env = {
        **os.environ,
        "BUNDLED_SIG": bundled_signature(stamp),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "LEDGER": str(ledger),
        "FAILURE": failure,
        "TAURI_SIGNING_PRIVATE_KEY": "" if failure == "missing-key" else "fixture-key",
    }
    result = subprocess.run(
        ["bash", str(wrapper), "build", "--config", "test-config", "--verbose"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    events = ledger.read_text().splitlines() if ledger.exists() else []
    # The final signature keeps the version binding the bundler gave it.
    sign = "sign --app-version 0.35.2-dev.20261026"
    if failure in ("", "unversioned"):
        assert result.returncode == 0, result.stderr
        assert events == ["build", "patch", "sign" if failure else sign]
        assert (
            scripts.parent
            / "src-tauri/target/release/bundle/appimage/test.AppImage.sig"
        ).read_text() == "final-signature"
    else:
        assert result.returncode != 0
        expected = {
            "build": ["build"],
            "patch": ["build", "patch"],
            "sign": ["build", "patch", sign],
            "missing-signature": ["build", "patch", sign],
            "missing-key": [],
        }
        assert events == expected[failure]


def test_workflow_uploads_only_final_appimage() -> None:
    workflow = (ROOT / ".github/workflows/tauri.yml").read_text()
    release = workflow.split("  release:\n", 1)[1].split("\n  build-android:", 1)[0]
    assert "tauriScript:" in release
    assert "bash scripts/release-appimage.sh" in release
    assert "Patch AppImage libgpg-error dependency" not in release
    assert "gh api -X DELETE" not in release
    assert "gh release upload" not in release
