import os
import subprocess
import sys
from pathlib import Path

import pytest

from gptme.logmanager import LogManager


def _probe_other_process(logdir: Path) -> subprocess.CompletedProcess[str]:
    code = (
        "import sys; from pathlib import Path; "
        "from gptme.logmanager import LogManager; "
        "manager = LogManager(logdir=Path(sys.argv[1])); "
        "manager._release_lock()"
    )
    return subprocess.run(
        [sys.executable, "-c", code, str(logdir)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific msvcrt locking behavior")
def test_same_process_conversation_lock_lifetime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same-process users must keep the shared Windows lock until the last release."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    logdir = tmp_path / "conversation"

    first = LogManager(logdir=logdir)
    second = LogManager(logdir=logdir)
    try:
        first._release_lock()

        blocked = _probe_other_process(logdir)
        assert blocked.returncode != 0
        assert "Another gptme instance is using" in blocked.stderr
    finally:
        second._release_lock()
        first._release_lock()

    available = _probe_other_process(logdir)
    assert available.returncode == 0, available.stderr
