"""Execute the release's PowerShell steps against sparse Windows SDK fixtures."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(
    not PWSH or os.name == "nt",
    reason="requires PowerShell and POSIX fixture executables",
)


@pytest.mark.parametrize("failure", ["", "missing-sdk", "sign", "verify"])
def test_windows_sidecar_signing(tmp_path: Path, failure: str) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/tauri.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["release"]["steps"]
        if step.get("name") == "Sign Windows sidecar binary"
    )
    assert step["if"] == "runner.os == 'Windows' && env.HAS_WINDOWS_SIGNING == 'true'"
    program_files = tmp_path / "Program Files (x86)"
    sdk = program_files / "Windows Kits/10/bin"
    ledger = tmp_path / "ledger"
    # Deliberately not on PATH; use numeric version order, not lexical order.
    for version in [] if failure == "missing-sdk" else ["10.0.9999.0", "10.0.26100.0"]:
        tool = sdk / version / "x64/signtool.exe"
        tool.parent.mkdir(parents=True)
        tool.write_text(
            "#!/bin/sh\n"
            'printf "%s %s\\n" "$0" "$*" >> "$LEDGER"\n'
            'if [ "$1" = "$FAILURE" ]; then exit 7; fi\n'
        )
        tool.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    rustc = bin_dir / "rustc"
    rustc.write_text("#!/bin/sh\nprintf 'host: x86_64-pc-windows-msvc\\n'\n")
    rustc.chmod(0o755)
    sidecar = tmp_path / "tauri/bins/gptme-server-x86_64-pc-windows-msvc.exe"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("fixture")
    script = tmp_path / "sign.ps1"
    script.write_text('$ErrorActionPreference = "Stop"\n' + step["run"])
    result = subprocess.run(
        [str(PWSH), "-NoProfile", "-File", str(script)],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "ProgramFiles(x86)": str(program_files),
            "WINDOWS_SIGNING_THUMBPRINT": "fixture-thumbprint",
            "LEDGER": str(ledger),
            "FAILURE": failure,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    events = ledger.read_text().splitlines() if ledger.exists() else []
    if failure:
        assert result.returncode != 0, result.stdout
        expected_error = {
            "missing-sdk": "signtool.exe",
            "sign": "SignTool signing failed",
            "verify": "SignTool verification failed",
        }[failure]
        assert expected_error in result.stderr
    else:
        assert result.returncode == 0, result.stderr
    expected_count = {"": 2, "missing-sdk": 0, "sign": 1, "verify": 2}[failure]
    assert len(events) == expected_count
    for event in events:
        assert "10.0.26100.0/x64/signtool.exe" in event
    if events:
        assert " sign /sha1 fixture-thumbprint /fd sha256 /tr " in events[0]
    if len(events) == 2:
        assert (
            " verify /pa tauri/bins/gptme-server-x86_64-pc-windows-msvc.exe"
            in events[1]
        )


def _make_sdk_fixture(tmp_path: Path, versions: list[str]) -> Path:
    """Create a sparse Windows SDK tree with mock signtool.exe binaries."""
    program_files = tmp_path / "Program Files (x86)"
    sdk = program_files / "Windows Kits/10/bin"
    for version in versions:
        tool = sdk / version / "x64/signtool.exe"
        tool.parent.mkdir(parents=True)
        tool.write_text(
            "#!/bin/sh\n"
            'printf "%s %s\\n" "$0" "$*" >> "$LEDGER"\n'
            'if [ "$1" = "$FAILURE" ]; then exit 7; fi\n'
        )
        tool.chmod(0o755)
    return program_files


@pytest.mark.parametrize("failure", ["", "missing-sdk", "sign", "verify"])
def test_azure_sidecar_signing(tmp_path: Path, failure: str) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/tauri.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["release"]["steps"]
        if step.get("name") == "Sign Windows sidecar binary (Azure Trusted Signing)"
    )
    assert step["if"] == "runner.os == 'Windows' && env.HAS_AZURE_SIGNING == 'true'"

    program_files = _make_sdk_fixture(
        tmp_path,
        versions=[] if failure == "missing-sdk" else ["10.0.9999.0", "10.0.26100.0"],
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    rustc = bin_dir / "rustc"
    rustc.write_text("#!/bin/sh\nprintf 'host: x86_64-pc-windows-msvc\\n'\n")
    rustc.chmod(0o755)
    sidecar = tmp_path / "tauri/bins/gptme-server-x86_64-pc-windows-msvc.exe"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("fixture")

    dlib = tmp_path / "Azure.CodeSigning.Dlib.dll"
    dlib.write_text("fixture-dlib")
    metadata = tmp_path / "azure-signing.json"
    metadata.write_text('{"Endpoint":"https://eus.codesigning.azure.net"}')

    ledger = tmp_path / "ledger"
    script = tmp_path / "azure-sign.ps1"
    script.write_text('$ErrorActionPreference = "Stop"\n' + step["run"])
    result = subprocess.run(
        [str(PWSH), "-NoProfile", "-File", str(script)],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "ProgramFiles(x86)": str(program_files),
            "AZURE_CODESIGNING_DLIB": str(dlib),
            "AZURE_CODESIGNING_METADATA": str(metadata),
            "LEDGER": str(ledger),
            "FAILURE": failure,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    events = ledger.read_text().splitlines() if ledger.exists() else []
    if failure:
        assert result.returncode != 0, result.stdout
        expected_error = {
            "missing-sdk": "signtool.exe",
            "sign": "Azure Trusted Signing failed",
            "verify": "Sidecar verification failed",
        }[failure]
        assert expected_error in result.stderr
    else:
        assert result.returncode == 0, result.stderr
    expected_count = {"": 2, "missing-sdk": 0, "sign": 1, "verify": 2}[failure]
    assert len(events) == expected_count
    for event in events:
        assert "10.0.26100.0/x64/signtool.exe" in event
    if events:
        assert " sign /fd sha256 /tr " in events[0]
        assert f"/dlib {dlib}" in events[0]
        assert f"/dmdf {metadata}" in events[0]
    if len(events) == 2:
        assert (
            " verify /pa tauri/bins/gptme-server-x86_64-pc-windows-msvc.exe"
            in events[1]
        )


def test_azure_winsign_config(tmp_path: Path) -> None:
    """Write Azure Trusted Signing Tauri config step emits a valid signCommand JSON."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/tauri.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["release"]["steps"]
        if step.get("name") == "Write Azure Trusted Signing Tauri config"
    )
    assert step["if"] == "runner.os == 'Windows' && env.HAS_AZURE_SIGNING == 'true'"
    assert step["id"] == "azwinsign"

    program_files = _make_sdk_fixture(tmp_path, versions=["10.0.26100.0"])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    rustc = bin_dir / "rustc"
    rustc.write_text("#!/bin/sh\nprintf 'host: x86_64-pc-windows-msvc\\n'\n")
    rustc.chmod(0o755)

    dlib = tmp_path / "Azure.CodeSigning.Dlib.dll"
    dlib.write_text("fixture-dlib")
    metadata = tmp_path / "azure-signing.json"
    metadata.write_text("{}")

    github_output = tmp_path / "github_output"
    github_output.write_text("")

    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir()

    script = tmp_path / "azwinsign.ps1"
    script.write_text('$ErrorActionPreference = "Stop"\n' + step["run"])
    result = subprocess.run(
        [str(PWSH), "-NoProfile", "-File", str(script)],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "ProgramFiles(x86)": str(program_files),
            "AZURE_CODESIGNING_DLIB": str(dlib),
            "AZURE_CODESIGNING_METADATA": str(metadata),
            "GITHUB_OUTPUT": str(github_output),
            "RUNNER_TEMP": str(runner_temp),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    # GITHUB_OUTPUT must have path=<cfg_path>
    output_lines = github_output.read_text().splitlines()
    path_lines = [ln for ln in output_lines if ln.startswith("path=")]
    assert len(path_lines) == 1
    cfg_path = Path(path_lines[0].removeprefix("path="))
    assert cfg_path.exists()

    config = json.loads(cfg_path.read_text())
    assert "bundle" in config
    assert "windows" in config["bundle"]
    sign_cmd = config["bundle"]["windows"]["signCommand"]
    assert "pwsh" in sign_cmd.lower()
    assert "azure-sign.ps1" in sign_cmd

    # The wrapper script must embed the signtool path and dlib/metadata paths
    wrap_path = runner_temp / "azure-sign.ps1"
    assert wrap_path.exists()
    wrap_text = wrap_path.read_text()
    assert "10.0.26100.0/x64/signtool.exe" in wrap_text.replace("\\", "/")
    assert "/dlib" in wrap_text
    assert "/dmdf" in wrap_text
