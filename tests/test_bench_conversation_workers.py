"""Smoke tests for the #5060 process-per-conversation worker prototype.

Backs the claims in ``docs/design/conversation-workers.md``: native per-process cwd,
single-writer lease, SIGINT cancellation, and exit detection that survives an orphaned
grandchild holding the worker's stdout pipe.
"""

import importlib.util
import os
import signal
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="prototype uses flock/SIGINT (POSIX only)"
)

_SPEC = importlib.util.spec_from_file_location(
    "bench_conversation_workers",
    Path(__file__).parent.parent / "scripts" / "bench_conversation_workers.py",
)
assert _SPEC and _SPEC.loader
bench = importlib.util.module_from_spec(_SPEC)
sys.modules["bench_conversation_workers"] = (
    bench  # needed for dataclass-free but safe reload
)
_SPEC.loader.exec_module(bench)


def test_two_workers_have_independent_native_cwd(tmp_path):
    ws = {}
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "marker.txt").write_text(name)
        ws[name] = bench.Worker(str(tmp_path / name))
    try:
        for name, w in ws.items():
            r = w.turn(bench.ISOLATION_CODE)["result"]
            assert r["open_relative"] == name
            assert r["path_exists"] is True
            assert r["subprocess_no_cwd"] == name
            assert r["native_getcwd"] == name
    finally:
        for w in ws.values():
            w.close()


def test_lease_refuses_second_writer_and_releases_on_sigkill(tmp_path):
    lease = str(tmp_path / ".lock")
    w1 = bench.Worker(str(tmp_path), lease=lease)
    try:
        w2 = bench.Worker(str(tmp_path), lease=lease)
        assert w2.ready["t"] == "lease_denied"
        w2.p.wait(5)
    finally:
        w1.kill()
    w3 = bench.Worker(str(tmp_path), lease=lease)
    try:
        assert w3.ready["t"] == "ready"
    finally:
        w3.close()


def test_sigint_cancels_busy_loop_and_worker_survives(tmp_path):
    w = bench.Worker(str(tmp_path))
    try:
        w._id += 1
        w.send({"t": "turn", "id": w._id, "code": "while True: pass"})
        time.sleep(0.3)
        os.kill(w.p.pid, signal.SIGINT)
        assert w.recv()["t"] == "cancelled"
        assert w.turn("result = 2")["result"] == 2
    finally:
        w.close()


def test_exit_detected_despite_orphan_holding_stdout(tmp_path):
    w = bench.Worker(str(tmp_path))
    try:
        child = w.turn(
            "import subprocess; result = subprocess.Popen(['sleep','300']).pid"
        )["result"]
        w._id += 1
        w.send({"t": "turn", "id": w._id, "code": "import time; time.sleep(60)"})
        time.sleep(0.2)
        os.kill(w.p.pid, signal.SIGKILL)
        assert w.recv()["t"] == "exit"  # would hang on pipe EOF: orphan keeps it open
        w.kill()  # process-group kill reaps the orphan
        time.sleep(0.3)
        assert not Path(f"/proc/{child}").exists() or sys.platform != "linux"
    finally:
        w.kill()
