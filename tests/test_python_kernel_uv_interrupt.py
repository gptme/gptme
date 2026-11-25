"""Exercise cancellation through a real uv parent, not the direct-kernel path."""

import os
import queue
import shutil
import signal
from pathlib import Path
from unittest.mock import Mock

import pytest

pytest.importorskip("jupyter_client")
pytest.importorskip("ipykernel")

from gptme.tools.python_kernel import KernelBackend


@pytest.fixture
def uv_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    if os.name != "posix" or not shutil.which("uv"):
        pytest.skip("requires POSIX and uv")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(
        KernelBackend, "_find_project_python", staticmethod(lambda: None)
    )
    monkeypatch.setattr(KernelBackend, "_is_uv_project", staticmethod(lambda: True))
    backend = KernelBackend()
    try:
        assert backend.execute("42").result_text == "42"
        assert backend._km.kernel_spec.argv[0] == shutil.which("uv")
        yield backend
    finally:
        # Include any workers left by a failed assertion in test cleanup.
        from gptme.tools.shell_memory import kill_group

        if backend._km and backend._km.provisioner:
            kill_group(backend._km.provisioner.pgid)
        backend.shutdown()


@pytest.mark.parametrize("worker", ["popen", "system"])
def test_uv_cancellation_interrupts_workers(uv_backend: KernelBackend, worker: str):
    setup = uv_backend.execute(
        "import os, subprocess\nworker = subprocess.Popen(['sleep', '60'])\nworker.pid"
    )
    assert setup.error is None
    assert setup.result_text is not None
    pid = int(setup.result_text)
    try:
        code = "worker.wait()" if worker == "popen" else "os.system('sleep 60')"
        result = uv_backend.execute(code, timeout=1)
        assert result.error and result.error["ename"] == "ExecutionTimeout"
        followup = uv_backend.execute(
            "import time; time.sleep(0.5); worker.poll(), 6 * 7", timeout=3
        )
        assert followup.error is None, followup.error
        assert followup.result_text == f"({-signal.SIGINT}, 42)"
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_uv_timeout_does_not_interrupt_next_cell(uv_backend: KernelBackend):
    for _ in range(3):
        result = uv_backend.execute("import time; time.sleep(60)", timeout=0.3)
        assert result.error and result.error["ename"] == "ExecutionTimeout"
        result = uv_backend.execute("import time; time.sleep(0.2); 42", timeout=3)
        assert result.error is None, result.error
        assert result.result_text == "42"


def test_drain_retries_empty_polls(monkeypatch: pytest.MonkeyPatch):
    backend = KernelBackend()
    backend._kc = Mock()
    backend._active_execute_msg_id = "current"
    backend._kc.get_iopub_msg.side_effect = [
        {
            "msg_type": "status",
            "parent_header": {"msg_id": "previous"},
            "content": {"execution_state": "idle"},
        },
        queue.Empty,
        {
            "msg_type": "status",
            "parent_header": {"msg_id": "current"},
            "content": {"execution_state": "idle"},
        },
    ]
    try:
        backend._drain_until_idle(grace=1)
        assert backend._kc.get_iopub_msg.call_count == 3
        assert backend._active_execute_msg_id is None
    finally:
        backend._kc = None
        backend.shutdown()


def test_drain_without_active_request_does_not_poll():
    backend = KernelBackend()
    backend._kc = Mock()
    try:
        backend._drain_until_idle()
        backend._kc.get_iopub_msg.assert_not_called()
    finally:
        backend._kc = None
        backend.shutdown()


def test_setup_interrupt_drains_the_setup_request():
    backend = KernelBackend()
    backend._kc = Mock()
    backend._kc.execute.return_value = "setup"
    backend._kc.get_shell_msg.side_effect = KeyboardInterrupt
    backend._kc.get_iopub_msg.return_value = {
        "msg_type": "status",
        "parent_header": {"msg_id": "setup"},
        "content": {"execution_state": "idle"},
    }
    try:
        with pytest.raises(KeyboardInterrupt):
            backend._exec_silent("pass")
        assert backend._active_execute_msg_id == "setup"
        backend._drain_until_idle()
        assert backend._active_execute_msg_id is None
        assert backend._kc.get_iopub_msg.call_count == 1
    finally:
        backend._kc = None
        backend.shutdown()


def test_interrupt_without_kernel_spec():
    backend = KernelBackend()
    backend._km = Mock(kernel_spec=None)
    try:
        backend.interrupt()
        backend._km.interrupt_kernel.assert_called_once()
    finally:
        backend.shutdown()


def test_uv_workers_use_live_group_when_provisioner_has_no_pgid(monkeypatch):
    from types import SimpleNamespace

    backend = KernelBackend()
    backend._km = SimpleNamespace(provisioner=SimpleNamespace(pid=100, pgid=None))
    monkeypatch.setattr(os, "getpgid", lambda pid: 100)
    run = Mock(
        return_value=SimpleNamespace(stdout="100 1 100\n101 100 100\n102 101 100\n")
    )
    monkeypatch.setattr("gptme.tools.python_kernel.subprocess.run", run)
    kill = Mock()
    monkeypatch.setattr(os, "kill", kill)
    try:
        backend._interrupt_uv_workers()
        kill.assert_called_once_with(102, signal.SIGINT)
    finally:
        backend._km = None
        backend.shutdown()
