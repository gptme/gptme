"""Execute the release's PowerShell step against a sparse Windows SDK fixture."""

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
