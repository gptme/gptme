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
    assert step["if"] == (
        "runner.os == 'Windows' && env.HAS_WINDOWS_SIGNING == 'true'"
        " && env.HAS_AZURE_SIGNING != 'true'"
    )
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
            'printf "%s\\n" "$@" >> "$ARG_LEDGER"\n'
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
            "ARG_LEDGER": str(tmp_path / "arguments"),
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


@pytest.mark.parametrize("failure", ["", "sign", "verify", "no-artifact"])
def test_azure_winsign_config(tmp_path: Path, failure: str) -> None:
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

    runner_temp = tmp_path / "runner temp"
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
    assert sign_cmd["cmd"] == "pwsh"
    assert "azure-sign.ps1" in sign_cmd["args"][2]

    # The wrapper script must embed the signtool path and dlib/metadata paths
    wrap_path = runner_temp / "azure-sign.ps1"
    assert wrap_path.exists()
    wrap_text = wrap_path.read_text()
    assert "10.0.26100.0/x64/signtool.exe" in wrap_text.replace("\\", "/")
    assert "/dlib" in wrap_text
    assert "/dmdf" in wrap_text

    # Match Tauri's argument substitution: the artifact is one argument even
    # when its path contains spaces. Execute the actual generated command.
    artifact = tmp_path / "release artifacts/gptme setup.exe"
    artifact.parent.mkdir()
    artifact.write_text("fixture")
    command = [sign_cmd["cmd"], *sign_cmd["args"]]
    assert command.count("%1") == 1
    command = [str(artifact) if arg == "%1" else arg for arg in command]
    command[0] = str(PWSH)
    if failure == "no-artifact":
        command.remove(str(artifact))
    ledger = tmp_path / "ledger"
    arg_ledger = tmp_path / "arguments"
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env={
            **os.environ,
            "LEDGER": str(ledger),
            "ARG_LEDGER": str(arg_ledger),
            "FAILURE": failure,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    if failure == "no-artifact":
        assert result.returncode != 0
        assert not ledger.exists()
        return
    assert result.returncode == (7 if failure else 0), result.stderr
    args = arg_ledger.read_text().splitlines()
    signing_args = [
        "sign",
        "/fd",
        "sha256",
        "/tr",
        "http://timestamp.acs.microsoft.com",
        "/td",
        "sha256",
        "/dlib",
        str(dlib),
        "/dmdf",
        str(metadata),
        str(artifact),
    ]
    assert args == signing_args + (
        [] if failure == "sign" else ["verify", "/pa", str(artifact)]
    )


def test_windows_signing_provider_selection() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/tauri.yml").read_text())
    steps = workflow["jobs"]["release"]["steps"]
    pfx_names = {
        "Import Windows code-signing certificate",
        "Sign Windows sidecar binary",
        "Write Windows signing --config override",
        "Build and publish release (signed Windows)",
    }
    pfx_steps = [step for step in steps if step.get("name") in pfx_names]
    assert len(pfx_steps) == len(pfx_names)
    for step in pfx_steps:
        assert "env.HAS_WINDOWS_SIGNING == 'true'" in step["if"]
        assert "env.HAS_AZURE_SIGNING != 'true'" in step["if"]
    azure_sidecar = next(
        step
        for step in steps
        if step.get("name") == "Sign Windows sidecar binary (Azure Trusted Signing)"
    )
    for key in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
        assert azure_sidecar["env"][key] == "${{ secrets." + key + " }}"
    assert not any(step.get("uses", "").startswith("azure/login@") for step in steps)
    install = next(
        step for step in steps if step.get("name") == "Install Azure Code Signing DLIB"
    )
    assert "-Version 1.0.95" in install["run"]
