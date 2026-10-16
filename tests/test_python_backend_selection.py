"""Backend-selection regressions run even without the optional kernel extra."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.message import Message
from gptme.tools import python as py
from gptme.tools.python_kernel import KernelExecResult


@pytest.mark.parametrize("missing", ["jupyter_client", "ipykernel"])
def test_auto_without_optional_dependency(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: str
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".venv").mkdir()
    monkeypatch.setattr(py, "_IPYTHON_BACKEND", "auto")
    monkeypatch.setattr(py, "_kernel_mode_cache", None)
    with patch.object(
        py.importlib.util,
        "find_spec",
        side_effect=lambda name: None if name == missing else object(),
    ):
        assert not py._should_use_kernel()


def test_registration_defers_backend_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(py, "_IPYTHON_BACKEND", "auto")
    monkeypatch.setattr(py, "_kernel_mode_cache", None)
    monkeypatch.setattr(py, "_kernel_mode_frozen", False)
    monkeypatch.setattr(py, "registered_functions", {})

    def helper() -> int:
        return 42

    py.register_function(helper)
    assert py._kernel_mode_cache is None
    workspace = tmp_path / "selected"
    workspace.mkdir()
    (workspace / ".venv").mkdir()
    monkeypatch.chdir(workspace)
    with patch.object(py.importlib.util, "find_spec", return_value=object()):
        assert py._should_use_kernel()


@pytest.mark.parametrize(
    ("stderr", "error"),
    [("warning", None), ("", {"ename": "ValueError", "evalue": "cell failed"})],
)
def test_helper_messages_preserve_cell_diagnostics(
    stderr: str, error: dict | None
) -> None:
    backend = MagicMock()
    backend._bridge.messages = [Message("system", "helper result")]
    backend.execute.return_value = KernelExecResult(stderr=stderr, error=error)
    with patch("gptme.tools.python_kernel.get_backend", return_value=backend):
        messages = list(py._execute_python_kernel("helper(); raise ValueError()"))
    assert messages[0].content == "helper result"
    assert len(messages) == 2
    assert (stderr or "cell failed") in messages[1].content


def test_disabled_timeout_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GPTME_IPYTHON_TIMEOUT", "0")
    backend = MagicMock()
    backend.execute.return_value = KernelExecResult()
    backend._bridge.messages = []
    with patch("gptme.tools.python_kernel.get_backend", return_value=backend):
        list(py._execute_python_kernel("42"))
    backend.execute.assert_called_once_with("42", timeout=None)


def test_registered_helpers_synced_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def helper() -> int:
        return 42

    monkeypatch.setattr(py, "registered_functions", {"helper": helper})
    backend = MagicMock()
    backend._bridge._functions = {}
    backend._bridge.messages = []
    backend.execute.return_value = KernelExecResult()
    with patch("gptme.tools.python_kernel.get_backend", return_value=backend):
        list(py._execute_python_kernel("helper()"))
    backend.register_bridge_function.assert_called_once_with(helper)


def test_explicit_kernel_does_not_silently_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(py, "_IPYTHON_BACKEND", "kernel")
    with patch.object(py.importlib.util, "find_spec", return_value=None):
        assert py._should_use_kernel()


def test_registration_does_not_create_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(py, "_IPYTHON_BACKEND", "kernel")
    monkeypatch.setattr(py, "registered_functions", {})

    def helper() -> int:
        return 42

    with patch("gptme.tools.python_kernel.get_backend") as get_backend:
        py.register_function(helper)
    get_backend.assert_not_called()
