"""Smoke tests for the #5060 process-per-conversation worker prototype.

Backs the claims in ``docs/design/conversation-workers.md``: native per-process cwd,
single-writer lease, SIGINT cancellation, and exit detection that survives an orphaned
grandchild holding the worker's stdout pipe.
"""

import importlib.util
import io
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

if sys.platform == "win32":
    pytest.skip("prototype uses flock/SIGINT (POSIX only)", allow_module_level=True)

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


@pytest.mark.timeout(10)
def test_isolation_benchmark_runs_and_restores_host_cwd():
    cwd = Path.cwd()
    try:
        result = bench.bench_isolation("")
        assert result["all_four_probes_independent"] is True
        assert 0 <= result["shared_process_threads_wrong_reads_of_4000"] <= 4000
        assert Path.cwd() == cwd
    finally:
        os.chdir(cwd)


@pytest.mark.timeout(10)
def test_printed_output_does_not_break_worker_protocol(tmp_path, monkeypatch):
    (tmp_path / "noisy_preload.py").write_text("print('preload output')\n")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    w = bench.Worker(str(tmp_path), preload="noisy_preload")
    try:
        assert w.turn("print('hello'); result = 42")["result"] == 42
        assert w.turn("import os; os.write(1, b'native output\\n')")["t"] == "done"
        assert (
            w.turn("import subprocess; subprocess.run(['echo', 'child output'])")["t"]
            == "done"
        )
        assert w.turn("result = 43")["result"] == 43
    finally:
        w.kill()


def test_windows_skips_before_importing_posix_benchmark():
    code = """
import builtins, runpy, sys
import pytest
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'fcntl':
        raise AssertionError('POSIX benchmark imported before Windows skip')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
sys.platform = 'win32'
try:
    runpy.run_path(sys.argv[1])
except pytest.skip.Exception:
    pass
else:
    raise AssertionError('Windows module was not skipped')
"""
    subprocess.run([sys.executable, "-c", code, __file__], check=True, timeout=10)


@pytest.mark.parametrize("output", ["not JSON\n", "[]\n", "{}\n"])
def test_malformed_worker_output_reports_protocol_error(output):
    w = bench.Worker.__new__(bench.Worker)
    w._q = bench.queue.Queue()
    w.p = type("Process", (), {"stdout": io.StringIO(output)})()
    w._pump()
    msg = w._q.get(timeout=1)
    assert msg["t"] == "protocol_error"
    assert "error" in msg
    w._q.put(msg)
    with pytest.raises(RuntimeError, match="worker protocol error"):
        w.recv()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc states")
@pytest.mark.parametrize(
    ("state", "expected"), [("S", True), ("Z", False), (None, False)]
)
def test_process_running_distinguishes_zombies(monkeypatch, state, expected):
    def read_stat(path):
        if state is None:
            raise FileNotFoundError
        return f"123 (command with ) parentheses) {state} 1 2 3"

    monkeypatch.setattr(Path, "read_text", read_stat)
    assert bench.process_running(123) is expected


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


def test_non_linux_cleanup_skips_without_psutil(tmp_path):
    code = """
import builtins, runpy, sys
from pathlib import Path
import pytest
namespace = runpy.run_path(sys.argv[1])
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'psutil':
        raise ImportError('psutil deliberately unavailable')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
sys.platform = 'darwin'
def unexpected_worker(*args, **kwargs):
    raise AssertionError('cleanup probe spawned a worker before dependency check')
namespace['bench'].Worker = unexpected_worker
try:
    namespace['test_exit_detected_despite_orphan_holding_stdout'](Path(sys.argv[2]))
except pytest.skip.Exception:
    pass
else:
    raise AssertionError('cleanup probe did not skip without psutil')
"""
    subprocess.run(
        [sys.executable, "-c", code, __file__, str(tmp_path)], check=True, timeout=10
    )


def test_exit_detected_despite_orphan_holding_stdout(tmp_path):
    if sys.platform != "linux":
        pytest.importorskip(
            "psutil",
            reason="non-Linux process-state probes require psutil",
            exc_type=ImportError,
        )
    w = bench.Worker(str(tmp_path))
    try:
        child = w.turn(bench.ORPHAN_CODE)["result"]
        w._id += 1
        w.send({"t": "turn", "id": w._id, "code": "import time; time.sleep(60)"})
        time.sleep(0.2)
        os.kill(w.p.pid, signal.SIGKILL)
        assert w.recv()["t"] == "exit"  # would hang on pipe EOF: orphan keeps it open
        assert bench.process_running(child)
        w.kill()  # kills the group; only the orphan's new parent can reap it
        deadline = time.monotonic() + 5
        while bench.process_running(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not bench.process_running(child)
    finally:
        w.kill()
