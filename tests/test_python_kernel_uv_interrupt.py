"""Exercise cancellation through a real uv parent, not the direct-kernel path."""

import os
import queue
import shutil
import signal
import sys
from pathlib import Path
from typing import Any
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
    # Keep the real uv parent/child topology without resolving packages or
    # depending on an existing uv cache. ipykernel is in the test interpreter.
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setattr(
        KernelBackend,
        "_build_uv_kernel_cmd",
        staticmethod(
            lambda: [
                shutil.which("uv"),
                "run",
                "--no-project",
                "--no-sync",
                sys.executable,
                "-m",
                "ipykernel_launcher",
                "-f",
                "{connection_file}",
            ]
        ),
    )
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
        followup = uv_backend.execute("worker.wait(timeout=5), 6 * 7", timeout=7)
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


@pytest.mark.parametrize("reply_status", ["ok", "error", "missing"])
def test_interrupt_waits_for_its_control_reply(reply_status, monkeypatch):
    """Old execute-idle cannot acknowledge a delayed control interrupt."""
    backend = KernelBackend()
    backend._km = Mock()
    backend._km.kernel_spec.interrupt_mode = "message"
    backend._kc = Mock()
    backend._kc.session.msg.return_value = {"header": {"msg_id": "interrupt"}}
    backend._active_execute_msg_id = "old-cell"
    monkeypatch.setattr(backend, "_interrupt_uv_workers", Mock())
    shutdown = Mock()
    monkeypatch.setattr(backend, "shutdown", shutdown)
    backend._kc.get_control_msg.side_effect = (
        [
            queue.Empty,
            {
                "msg_type": "interrupt_reply",
                "parent_header": {"msg_id": "older"},
                "content": {"status": "ok"},
            },
            {
                "msg_type": "interrupt_reply",
                "parent_header": {"msg_id": "interrupt"},
                "content": {"status": reply_status},
            },
        ]
        if reply_status != "missing"
        else RuntimeError("control channel closed")
    )
    try:
        backend.interrupt()
        backend._kc.control_channel.send.assert_called_once_with(
            {"header": {"msg_id": "interrupt"}}
        )
        backend._km.interrupt_kernel.assert_not_called()
        if reply_status == "ok":
            assert backend._kc.get_control_msg.call_count == 3
            shutdown.assert_not_called()
        else:
            shutdown.assert_called_once()
    finally:
        backend._kc = None
        backend._km = None
        backend._bridge.stop()


def test_silent_setup_timeout_drains_before_reuse(monkeypatch):
    backend = KernelBackend()
    backend._kc = Mock()
    backend._kc.execute.return_value = "setup"
    backend._kc.get_iopub_msg.return_value = {
        "msg_type": "status",
        "parent_header": {"msg_id": "setup"},
        "content": {"execution_state": "idle"},
    }
    interrupt = Mock()
    monkeypatch.setattr(backend, "interrupt", interrupt)
    try:
        with pytest.raises(RuntimeError, match="setup timed out"):
            backend._exec_silent("pass", timeout=0)
        interrupt.assert_called_once()
        backend._kc.get_iopub_msg.assert_called_once()
        assert backend._active_execute_msg_id is None
    finally:
        backend._kc = None
        backend.shutdown()


def test_uv_worker_idle_before_control_interrupt(uv_backend, monkeypatch):
    """Force worker cancellation to finish the cell before sending control."""
    assert (
        uv_backend.execute(
            "import subprocess; worker = subprocess.Popen(['sleep', '60'])"
        ).error
        is None
    )
    client = uv_backend._kc
    manager = uv_backend._km
    send = client.control_channel.send
    receive = client.get_control_msg
    requests: list[dict[str, Any]] = []
    saw_idle = []
    monkeypatch.setattr(client.control_channel, "send", requests.append)

    def delayed_reply(timeout):
        if requests:
            # _interrupt_uv_workers has stopped worker.wait(), but the actual
            # control interrupt is still unsent. Observe that old execute-idle
            # really can arrive first, then release the delayed request.
            while True:
                msg = client.get_iopub_msg(timeout=5)
                if (
                    msg.get("msg_type") == "status"
                    and msg.get("parent_header", {}).get("msg_id")
                    == uv_backend._active_execute_msg_id
                    and msg.get("content", {}).get("execution_state") == "idle"
                ):
                    saw_idle.append(msg)
                    break
            send(requests.pop())
        return receive(timeout=timeout)

    monkeypatch.setattr(client, "get_control_msg", delayed_reply)
    result = uv_backend.execute("worker.wait()", timeout=0.3)
    assert result.error and result.error["ename"] == "ExecutionTimeout"
    assert len(saw_idle) == 1
    assert uv_backend._km is manager  # acknowledgment preserves kernel state
    followup = uv_backend.execute("worker.returncode, 6 * 7", timeout=3)
    assert followup.error is None, followup.error
    assert followup.result_text == f"({-signal.SIGINT}, 42)"


def test_control_interrupt_deadline_replaces_kernel(monkeypatch: pytest.MonkeyPatch):
    backend = KernelBackend()
    backend._kc = Mock()
    shutdown = Mock()
    monkeypatch.setattr(backend, "shutdown", shutdown)
    clock = iter([0.0, 0.0, 6.0])
    monkeypatch.setattr("gptme.tools.python_kernel._monotonic", lambda: next(clock))
    backend._kc.get_control_msg.side_effect = queue.Empty
    try:
        backend._interrupt_via_control()
        shutdown.assert_called_once()
        assert backend._kc.get_control_msg.call_count == 1
    finally:
        backend._kc = None
        backend._bridge.stop()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
@pytest.mark.parametrize("case", ["no_pid", "gone", "own_group", "bad_ps", "denied"])
def test_uv_worker_enumeration_failures_are_safe(
    monkeypatch: pytest.MonkeyPatch, case: str
):
    from types import SimpleNamespace

    backend = KernelBackend()
    backend._km = SimpleNamespace(
        provisioner=SimpleNamespace(pid=None if case == "no_pid" else 100, pgid=None)
    )
    getpgid = Mock(return_value=os.getpgrp() if case == "own_group" else 100)
    if case == "gone":
        getpgid.side_effect = ProcessLookupError
    monkeypatch.setattr(os, "getpgid", getpgid)
    run = Mock(
        return_value=SimpleNamespace(
            stdout="invalid"
            if case == "bad_ps"
            else "100 1 100\n101 100 100\n102 101 100\n"
        )
    )
    monkeypatch.setattr("gptme.tools.python_kernel.subprocess.run", run)
    kill = Mock(side_effect=PermissionError)
    monkeypatch.setattr(os, "kill", kill)
    try:
        backend._interrupt_uv_workers()
        if case == "denied":
            kill.assert_called_once_with(102, signal.SIGINT)
        else:
            kill.assert_not_called()
    finally:
        backend._km = None
        backend.shutdown()
