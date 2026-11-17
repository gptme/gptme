"""Regression coverage for unlimited cells and directory-independent delivery."""

from pathlib import Path

import pytest

pytest.importorskip("jupyter_client")
pytest.importorskip("ipykernel")

from gptme.tools.python_kernel import KernelBackend


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    kb = KernelBackend()
    yield kb
    kb.shutdown()


def test_unlimited_timeout(backend: KernelBackend) -> None:
    result = backend.execute("6 * 7", timeout=None)
    assert result.error is None
    assert result.result_text == "42"


def test_message_delivery_after_chdir(backend: KernelBackend, tmp_path: Path) -> None:
    target = tmp_path / "child"
    target.mkdir()
    result = backend.execute(
        "import os\nfrom gptme.message import Message\n"
        f"os.chdir({str(target)!r})\nMessage('system', 'delivered after chdir')"
    )
    assert result.error is None, result.error
    assert [m.content for m in backend._bridge.messages] == ["delivered after chdir"]


def test_regular_helpers_still_reject_changed_directory(
    backend: KernelBackend, tmp_path: Path
) -> None:
    def host_helper() -> str:
        return "must not run"

    backend.register_bridge_function(host_helper)
    target = tmp_path / "child"
    target.mkdir()
    result = backend.execute(f"import os; os.chdir({str(target)!r}); host_helper()")
    assert result.error is not None
    assert "working directory differs" in result.error["evalue"]


def _in_session(workspace: Path, fn):
    """Run ``fn`` in its own context with ``workspace`` as the session workspace."""
    import contextvars

    from gptme.tools.shell import set_workspace_cwd

    def run():
        set_workspace_cwd(str(workspace))
        return fn()

    return contextvars.copy_context().run(run)


@pytest.fixture
def session_workspace(tmp_path: Path) -> Path:
    """A server session's workspace, distinct from the process cwd (``tmp_path``)."""
    ws = tmp_path / "session-ws"
    ws.mkdir()
    return ws


def test_cell_runs_in_session_workspace(
    backend: KernelBackend, session_workspace: Path
) -> None:
    result = _in_session(
        session_workspace, lambda: backend.execute("import os; os.getcwd()")
    )
    assert result.error is None, result.error
    assert result.result_text == repr(str(session_workspace.resolve()))


def test_helpers_run_from_session_workspace(
    backend: KernelBackend, session_workspace: Path
) -> None:
    def host_helper() -> str:
        return "helper ran"

    backend.register_bridge_function(host_helper)
    result = _in_session(session_workspace, lambda: backend.execute("host_helper()"))
    assert result.error is None, result.error
    assert result.result_text == repr("helper ran")


@pytest.mark.parametrize("operation", ["save", "load"])
def test_browser_state_uses_session_workspace(
    backend: KernelBackend,
    session_workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """A bridged browser helper must not read or overwrite another workspace."""
    import json
    from unittest.mock import MagicMock

    pytest.importorskip("playwright")
    from gptme.tools import _browser_playwright as bp
    from gptme.tools import _browser_thread as bt
    from gptme.tools import browser as browser_tool

    own_state = {"cookies": [], "origins": [], "marker": "session A"}
    other_state = {"cookies": [], "origins": [], "marker": "session B"}
    target = session_workspace / "session.json"
    other_target = tmp_path / "session.json"
    other_target.write_text(json.dumps(other_state))
    if operation == "load":
        target.write_text(json.dumps(own_state))

    context = MagicMock()
    context.storage_state.side_effect = lambda *, path: Path(path).write_text(
        json.dumps(own_state)
    )
    monkeypatch.setattr(browser_tool, "browser", "playwright")
    monkeypatch.setattr(bp, "_current_context", context)
    monkeypatch.setattr(bp, "_close_current_page", lambda: None)
    monkeypatch.setattr(bt, "_override_storage_state", None)
    # Keep browser startup out of this file-I/O/bridge regression. The real
    # callback runs in the bridge's captured session context.
    monkeypatch.setattr(bp, "_execute_with_retry", lambda fn, *args: fn(None, *args))
    helper = getattr(browser_tool, f"{operation}_browser_state")
    backend.register_bridge_function(helper)
    result = _in_session(
        session_workspace,
        lambda: backend.execute(f"{operation}_browser_state('session.json')"),
    )
    assert result.error is None, result.error
    assert str(target) in result.result_text
    assert json.loads(target.read_text()) == own_state
    assert json.loads(other_target.read_text()) == other_state
    if operation == "load":
        assert bt._override_storage_state == target
