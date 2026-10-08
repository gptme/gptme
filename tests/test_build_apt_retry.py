"""Exercise the Ubuntu build workflow's apt retry loop without network or sudo."""

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("bash") is None, reason="requires Bash")
@pytest.mark.parametrize(
    ("failures", "status", "warning", "expected_attempts", "expected_status"),
    [
        (0, 0, "", 1, 0),
        (1, 100, "", 2, 0),
        (1, 124, "", 2, 0),
        (1, 0, "W: Failed to fetch mirror", 2, 0),
        (1, 0, "W: Some index files failed to download", 2, 0),
        (1, 0, "W: Fehler beim Holen", 2, 0),
        (3, 100, "", 3, 1),
        (3, 124, "", 3, 1),
        (3, 0, "W: Failed to fetch mirror", 3, 1),
        (3, 0, "W: Fehler beim Holen", 3, 1),
    ],
)
def test_apt_retry(
    tmp_path: Path,
    failures: int,
    status: int,
    warning: str,
    expected_attempts: int,
    expected_status: int,
) -> None:
    workflow = (
        Path(__file__).resolve().parents[1] / ".github/workflows/build.yml"
    ).read_text()
    # Execute the actual loop, not a copy that can drift from the workflow.
    start = workflow.index("        for i in {1..3}; do")
    end = workflow.index("        sudo apt-get install", start)
    loop = textwrap.dedent(workflow[start:end])
    output = tmp_path / "apt-output"
    # Keep large/localized diagnostics to guard against parsing output again.
    output.write_text((warning + "\n" if warning else "") + "x" * 262144)
    attempts = tmp_path / "attempts"
    sleeps = tmp_path / "sleeps"
    script = """
    timeout() {
        if [[ "$*" != '300 sudo apt-get update -o APT::Update::Error-Mode=any' ]]; then
            echo "bad timeout args: $*" >&2
            exit 64
        fi
        local count=0

        if [[ -f "$ATTEMPTS" ]]; then count=$(wc -l < "$ATTEMPTS"); fi
        echo attempt >> "$ATTEMPTS"
        if (( count < FAILURES )); then
            cat "$OUTPUT"
            # With Error-Mode=any, partial fetch warnings produce exit 100,
            # independent of the language or wording of apt's diagnostics.
            if [[ -n "$FETCH_FAILURE" ]]; then
                return 100
            fi
            return "$STATUS"
        fi
        echo 'Fetched all indexes'
    }
    sleep() {
        if [[ "$1" != 5 ]]; then
            echo "bad sleep args: $*" >&2
            exit 65
        fi
        echo sleep >> "$SLEEPS"
    }
    """
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script + loop],
        env={
            "PATH": os.environ["PATH"],
            "ATTEMPTS": str(attempts),
            "SLEEPS": str(sleeps),
            "OUTPUT": str(output),
            "FAILURES": str(failures),
            "STATUS": str(status),
            "FETCH_FAILURE": warning,
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == expected_status, result.stderr
    assert len(attempts.read_text().splitlines()) == expected_attempts
    assert (len(sleeps.read_text().splitlines()) if sleeps.exists() else 0) == (
        expected_attempts - 1
    )
