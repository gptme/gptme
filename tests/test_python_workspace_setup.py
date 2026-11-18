"""Python setup must use the caller's workspace while the host cwd stays put."""

from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.hooks import ConfirmAction
from gptme.tools import python as py
from gptme.tools.python_kernel import KernelBackend
from gptme.tools.shell import set_workspace_cwd


@pytest.fixture
def directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    host, workspace = tmp_path / "host", tmp_path / "workspace"
    host.mkdir()
    workspace.mkdir()
    monkeypatch.chdir(host)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    set_workspace_cwd(str(workspace))
    return host, workspace


def test_project_venv_comes_from_session_workspace(
    directories: tuple[Path, Path],
) -> None:
    host, workspace = directories
    (host / ".venv").mkdir()
    (workspace / ".venv").mkdir()
    assert py._detect_venv() == workspace / ".venv"
    assert py._detect_venv(env_only=True) is None
    assert Path.cwd() == host


@pytest.mark.parametrize("marker", [".venv", "uv.lock", "pyproject.toml"])
def test_auto_detection_tracks_workspace_before_first_cell(
    directories: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, marker: str
) -> None:
    host, workspace = directories
    if marker == ".venv":
        (workspace / marker).mkdir()
    else:
        (workspace / marker).write_text(
            "[tool.uv]\n" if marker.endswith("toml") else ""
        )
    monkeypatch.setattr(py, "_IPYTHON_BACKEND", "auto")
    monkeypatch.setattr(py, "_kernel_mode_cache", None)
    monkeypatch.setattr(py, "_kernel_mode_cache_key", None)
    monkeypatch.setattr(py, "_kernel_mode_frozen", False)
    monkeypatch.setattr(KernelBackend, "_find_uv", staticmethod(lambda: "/usr/bin/uv"))
    with patch.object(py.importlib.util, "find_spec", return_value=object()):
        assert py._should_use_kernel()
        set_workspace_cwd(str(host))
        assert not py._should_use_kernel()
        set_workspace_cwd(str(workspace))
        assert py._should_use_kernel()
    assert Path.cwd() == host


def test_sandbox_receives_session_workspace(directories: tuple[Path, Path]) -> None:
    host, workspace = directories
    with (
        patch.object(py, "get_confirmation") as confirm,
        patch.dict("os.environ", {"GPTME_SANDBOX": "docker"}),
        patch("gptme.sandbox.SandboxConfig.check_available", return_value=(True, "")),
        patch("gptme.sandbox.sandbox_exec_python", return_value=("ok", "", 0)) as run,
    ):
        confirm.return_value.action = ConfirmAction.CONFIRM
        list(py.execute_python("print('ok')", [], {}))
    assert run.call_args.args[0].workspace == workspace
    assert Path.cwd() == host


def test_kernel_starts_in_session_workspace(
    directories: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("jupyter_client")
    pytest.importorskip("ipykernel")
    monkeypatch.setenv("GPTME_SHELL_MEMORY_GUARD", "0")
    host, workspace = directories
    backend = KernelBackend()
    try:
        backend._ensure_started()
        # Query the freshly started kernel directly, before execute() can sync
        # its directory. Startup code and `uv run` must already see this path.
        msg_id = backend._kc.execute(
            "import os", silent=True, user_expressions={"cwd": "os.getcwd()"}
        )
        reply = backend._kc.get_shell_msg(timeout=10)
        assert reply["parent_header"]["msg_id"] == msg_id
        assert reply["content"]["user_expressions"]["cwd"]["data"][
            "text/plain"
        ] == repr(str(workspace))
        assert Path.cwd() == host
    finally:
        backend.shutdown()
