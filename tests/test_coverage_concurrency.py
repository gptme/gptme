"""The shipped coverage configuration must trace native threads and greenlets."""

import subprocess
import sys
from pathlib import Path

import coverage

ROOT = Path(__file__).resolve().parent.parent


def test_coverage_records_thread_and_greenlet_execution(tmp_path: Path) -> None:
    source = tmp_path / "concurrent_work.py"
    source.write_text(
        "import threading\n"
        "from greenlet import greenlet\n"
        "def threaded():\n"
        "    return 'thread marker'\n"
        "def cooperative():\n"
        "    return 'greenlet marker'\n"
        "worker = threading.Thread(target=threaded)\n"
        "worker.start()\n"
        "worker.join()\n"
        "greenlet(cooperative).switch()\n"
    )
    data_file = tmp_path / ".coverage"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            f"--rcfile={ROOT / 'pyproject.toml'}",
            f"--data-file={data_file}",
            str(source),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    data = coverage.CoverageData(basename=str(data_file))
    data.read()
    executed = set(data.lines(str(source)) or [])
    assert 4 in executed, "coverage missed the native thread body"
    assert 6 in executed, "coverage missed the greenlet body"
