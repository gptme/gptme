"""Unattended kernel requests must reject stdin rather than wait for an answer."""

import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("jupyter_client")
pytest.importorskip("ipykernel")

from gptme.hooks import ConfirmAction
from gptme.tools import python as python_tool
from gptme.tools import python_kernel
from gptme.tools.python_kernel import KernelBackend


def test_execute_request_disables_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(python_kernel.BridgeServer, "start", lambda self: None)
    backend = KernelBackend()
    client = Mock()
    client.execute.return_value = "cell-id"
    client.get_iopub_msg.return_value = {
        "parent_header": {"msg_id": "cell-id"},
        "msg_type": "status",
        "content": {"execution_state": "idle"},
    }
    monkeypatch.setattr(backend, "_kc", client)
    monkeypatch.setattr(backend, "_ensure_started", lambda: None)
    monkeypatch.setattr(backend, "_exec_silent", lambda *args, **kwargs: None)
    result = backend.execute("input()", timeout=None)
    assert result.error is None
    client.execute.assert_called_once_with(
        "input()", silent=False, store_history=True, allow_stdin=False
    )


def test_setup_request_disables_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(python_kernel.BridgeServer, "start", lambda self: None)
    backend = KernelBackend()
    client = Mock()
    client.execute.return_value = "setup-id"
    client.get_shell_msg.return_value = {
        "parent_header": {"msg_id": "setup-id"},
        "content": {"status": "ok"},
    }
    monkeypatch.setattr(backend, "_kc", client)
    backend._exec_silent("pass")
    client.execute.assert_called_once_with(
        "pass", silent=True, store_history=False, allow_stdin=False
    )


@pytest.fixture
def kernel_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[KernelBackend]:
    monkeypatch.chdir(tmp_path)
    # Explicitly use this test interpreter, never discover/sync a user project.
    monkeypatch.setattr(
        KernelBackend, "_find_project_python", staticmethod(lambda: sys.executable)
    )
    backend = KernelBackend()
    monkeypatch.setenv("GPTME_SANDBOX", "none")
    monkeypatch.setattr(python_tool, "_resolve_kernel_backend", lambda: True)
    monkeypatch.setattr(python_kernel, "get_backend", lambda: backend)
    monkeypatch.setattr(python_tool, "registered_functions", {})
    monkeypatch.setattr(
        python_tool,
        "get_confirmation",
        lambda **kwargs: SimpleNamespace(action=ConfirmAction.CONFIRM),
    )
    try:
        yield backend
    finally:
        backend.shutdown()


def execute(code: str) -> str:
    return "\n".join(
        message.content for message in python_tool.execute_python(code, [], None)
    )


@pytest.mark.timeout(40)
@pytest.mark.parametrize("timeout", ["3", "0"])
@pytest.mark.parametrize(
    "code",
    [
        'input("Synthetic unattended prompt: ")',
        'def library_prompt():\n    return input("Synthetic library prompt: ")\nlibrary_prompt()',
    ],
)
def test_input_rejected_through_tool_and_kernel_reused(
    kernel_tool: KernelBackend,
    monkeypatch: pytest.MonkeyPatch,
    timeout: str,
    code: str,
) -> None:
    # Independent pytest deadline bounds even timeout-disabled admission failures.
    monkeypatch.setenv("GPTME_IPYTHON_TIMEOUT", timeout)
    assert python_tool._get_timeout() == (3 if timeout == "3" else None)
    assert "CONTROL_READY" in execute('sentinel = 41\nprint("CONTROL_READY")')
    result = execute(code)
    assert "StdinNotImplementedError" in result
    assert "ExecutionTimeout" not in result
    assert (
        "raw_input was called, but this frontend does not support input requests"
        in result
    )
    following = execute("sentinel += 1\nsentinel")
    assert "42" in following
    assert "Exception:" not in following
    error = execute('raise ValueError("ORDINARY_ERROR")')
    assert "ValueError: ORDINARY_ERROR" in error
    assert "AFTER_ERROR" in execute('print("AFTER_ERROR")')


@pytest.mark.timeout(40)
def test_timeout_interrupt_still_works_after_rejected_input(
    kernel_tool: KernelBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GPTME_IPYTHON_TIMEOUT", "3")
    assert "StdinNotImplementedError" in execute('input("Synthetic: ")')
    monkeypatch.setenv("GPTME_IPYTHON_TIMEOUT", "0.5")
    assert "ExecutionTimeout" in execute("import time; time.sleep(30)")
    assert "AFTER_TIMEOUT" in execute('print("AFTER_TIMEOUT")')
