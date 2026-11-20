"""The in-process IPython backend must not share state between conversations.

A single process serves several server conversations. One ``InteractiveShell``
shared by all of them leaked variables from one conversation into another
(gptme/gptme#4951). Relative-cwd cells are pinned to the session workspace
for the duration of the cell and must not leave the host cwd changed.
"""

import atexit
import contextvars
import os
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from gptme.hooks import SessionEndHook, current_conversation_id
from gptme.logmanager import LogManager
from gptme.tools import python as python_tool
from gptme.tools.shell import set_workspace_cwd


def _close_shell(shell: Any) -> None:
    history = shell.history_manager
    if history is not None and history.save_thread is not None:
        history.save_thread.stop()
    atexit.unregister(shell.atexit_operations)
    shell.atexit_operations()
    if history is not None:
        history.db.close()


@pytest.fixture(autouse=True)
def inprocess(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GPTME_SANDBOX", "none")
    monkeypatch.setattr(python_tool, "_resolve_kernel_backend", lambda: False)
    monkeypatch.setattr(python_tool, "_ipython", None)
    monkeypatch.setattr(python_tool, "_conversation_ipython", {})
    monkeypatch.setattr(python_tool, "registered_functions", {})
    monkeypatch.setattr(python_tool, "registered_function_tools", {})
    yield
    for shell in python_tool._conversation_ipython.values():
        _close_shell(shell)
    if python_tool._ipython is not None:
        _close_shell(python_tool._ipython)


def _in_context(conversation_id: str | None, fn: Any) -> Any:
    ctx = contextvars.copy_context()
    ctx.run(current_conversation_id.set, conversation_id)
    return ctx.run(fn)


def _shell(conversation_id: str | None) -> Any:
    return _in_context(conversation_id, python_tool._get_ipython)


def _run(conversation_id: str | None, code: str) -> None:
    _shell(conversation_id).run_cell(code, store_history=False)


def _in_server_session(conversation_id: str, workspace: Path, fn: Any) -> Any:
    ctx = contextvars.copy_context()

    def run() -> Any:
        current_conversation_id.set(conversation_id)
        set_workspace_cwd(str(workspace))
        return fn()

    return ctx.run(run)


def _execute(code: str) -> list[Any]:
    return list(python_tool.execute_python(code, [], None))


def test_two_conversations_get_distinct_shells() -> None:
    assert _shell("conv-a") is not _shell("conv-b")


def test_same_conversation_reuses_its_shell() -> None:
    assert _shell("conv-a") is _shell("conv-a")


def test_variables_do_not_leak_between_conversations() -> None:
    _run("conv-a", "leak_marker = 'from-a'")

    leaked = _in_context(
        "conv-b", lambda: "leak_marker" in python_tool._get_ipython().user_ns
    )
    assert leaked is False

    kept = _in_context(
        "conv-a", lambda: python_tool._get_ipython().user_ns.get("leak_marker")
    )
    assert kept == "from-a"


def _registered_helper() -> str:
    return "ok"


def test_registered_functions_present_in_every_conversation() -> None:
    python_tool.register_function(_registered_helper, tool_name="testtool")
    assert "_registered_helper" in _shell("conv-a").user_ns
    assert "_registered_helper" in _shell("conv-b").user_ns


def test_existing_conversation_shells_receive_new_helpers() -> None:
    shells = [_shell("conv-a"), _shell("conv-b"), _shell(None)]
    python_tool.register_function(_registered_helper, tool_name="testtool")
    for shell in shells:
        result = shell.run_cell("_registered_helper()", store_history=False)
        assert result.error_in_exec is None
        assert result.result == "ok"


def test_python_tool_owns_its_session_cleanup(tmp_path: Path) -> None:
    first = _shell("conv-a")
    first.push({"cleanup_marker": object()})
    history = first.history_manager
    save_thread = history.save_thread
    _shell("conv-b")
    manager = LogManager.load(tmp_path / "conv-a", create=True)
    cleanup_hooks = [
        cast(SessionEndHook, fn)
        for event, fn, _priority in (python_tool.tool.hooks or {}).values()
        if event == "session.end"
    ]
    assert cleanup_hooks, "ipython-only sessions need their own cleanup hook"
    for hook in cleanup_hooks:
        list(hook(manager))
    assert "cleanup_marker" not in first.user_ns
    assert first.history_manager is None
    if save_thread is not None:
        assert not save_thread.is_alive()
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        history.db.execute("SELECT 1")
    assert "conv-a" not in python_tool._conversation_ipython
    assert "conv-b" in python_tool._conversation_ipython
    # Repeated session-end delivery must be harmless.
    for hook in cleanup_hooks:
        list(hook(manager))


@pytest.mark.parametrize("live_state", ["session", "command", "idle"])
def test_server_cleanup_rechecks_conversation_liveness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_state: str
) -> None:
    pytest.importorskip("flask")
    from gptme.server.session_models import SessionManager

    # Model the gap between removal of the last session and delivery of its
    # SESSION_END hook: a reconnect or command may now own the same shell.
    monkeypatch.setattr(
        SessionManager,
        "_conversation_sessions",
        {"conv-a": {"replacement"}} if live_state == "session" else {},
    )
    monkeypatch.setattr(
        SessionManager,
        "_active_commands",
        {"conv-a"} if live_state == "command" else set(),
    )
    first = _shell("conv-a")
    first.push({"reconnect_marker": "kept"})
    manager = LogManager.load(tmp_path / "conv-a", create=True)
    list(python_tool._session_end_ipython_cleanup(manager, server_managed=True))
    SessionManager._evict_idle_conversation_state("conv-a", manager.logdir)

    if live_state == "idle":
        assert "conv-a" not in python_tool._conversation_ipython
        assert first.history_manager is None
    else:
        assert _shell("conv-a") is first
        assert first.user_ns["reconnect_marker"] == "kept"


def test_server_cleanup_closes_detached_shell_outside_manager_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("flask")
    from gptme.server.session_models import SessionManager

    monkeypatch.setattr(SessionManager, "_conversation_sessions", {})
    monkeypatch.setattr(SessionManager, "_active_commands", set())
    first = _shell("conv-a")
    close_shell = python_tool.close_conversation_ipython
    replacements = []

    def reconnect_then_close(shell: Any) -> None:
        # A reconnect may create state during slow teardown. Prove that teardown
        # released the manager lock and no longer owns the registry entry.
        assert SessionManager._lock.acquire(blocking=False)
        try:
            replacement = _shell("conv-a")
            replacement.push({"reconnect_marker": "kept"})
            replacements.append(replacement)
        finally:
            SessionManager._lock.release()
        close_shell(shell)

    monkeypatch.setattr(python_tool, "close_conversation_ipython", reconnect_then_close)
    SessionManager._evict_idle_conversation_state("conv-a", tmp_path / "conv-a")
    assert first.history_manager is None
    assert replacements[0] is not first
    assert _shell("conv-a") is replacements[0]
    assert replacements[0].user_ns["reconnect_marker"] == "kept"


@pytest.mark.parametrize("kernel", [False, True])
def test_plot_snapshot_matches_execution_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kernel: bool
) -> None:
    execution_dir = tmp_path / "execution"
    execution_dir.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(execution_dir)
    monkeypatch.setattr(python_tool, "current_workspace", lambda: workspace)
    monkeypatch.setattr(python_tool, "_resolve_kernel_backend", lambda: kernel)
    code = "from pathlib import Path; Path('plot.png').write_bytes(b'png')"
    if kernel:
        from gptme.message import Message

        def execute_kernel(code: str) -> Iterator[Message]:
            # A real kernel executes in current_workspace(), not the server cwd.
            with monkeypatch.context() as kernel_patch:
                kernel_patch.chdir(workspace)
                exec(code, {})
            yield Message("system", "Executed code block.")

        monkeypatch.setattr(python_tool, "_execute_python_kernel", execute_kernel)
    messages = list(python_tool.execute_python(code, [], None))
    artifacts = (messages[-1].metadata or {}).get("artifacts", [])
    expected_dir = workspace if kernel else execution_dir
    assert [a["path"] for a in artifacts] == [str(expected_dir / "plot.png")]
    assert Path.cwd() == execution_dir


def test_server_relative_cell_lands_in_own_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, ws_a, ws_b = (tmp_path / n for n in ("host", "a", "b"))
    for path in (host, ws_a, ws_b):
        path.mkdir()
    monkeypatch.chdir(host)

    def write(text: str) -> None:
        _execute(f"from pathlib import Path; Path('out.txt').write_text({text!r})")

    _in_server_session("conv-a", ws_a, lambda: write("from-a"))
    _in_server_session("conv-b", ws_b, lambda: write("from-b"))

    assert (ws_a / "out.txt").read_text() == "from-a"
    assert (ws_b / "out.txt").read_text() == "from-b"
    assert not (host / "out.txt").exists()
    assert Path.cwd() == host


def test_server_cell_os_chdir_does_not_stick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, workspace, other = (tmp_path / n for n in ("host", "workspace", "other"))
    for path in (host, workspace, other):
        path.mkdir()
    monkeypatch.chdir(host)

    _in_server_session(
        "conv-a",
        workspace,
        lambda: _execute(f"import os; os.chdir({str(other)!r})"),
    )
    assert Path.cwd() == host


def test_server_cell_exception_restores_host_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, workspace = (tmp_path / n for n in ("host", "workspace"))
    for path in (host, workspace):
        path.mkdir()
    monkeypatch.chdir(host)

    _in_server_session(
        "conv-a",
        workspace,
        lambda: _execute("raise RuntimeError('boom')"),
    )
    assert Path.cwd() == host


def test_server_cell_interrupt_restores_host_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, workspace = (tmp_path / n for n in ("host", "workspace"))
    for path in (host, workspace):
        path.mkdir()
    monkeypatch.chdir(host)

    def explode(*args: Any, **kwargs: Any) -> None:
        raise KeyboardInterrupt

    def cell() -> None:
        monkeypatch.setattr(python_tool._get_ipython(), "run_cell", explode)
        with pytest.raises(KeyboardInterrupt):
            _execute("1 + 1")

    _in_server_session("conv-a", workspace, cell)
    assert Path.cwd() == host


def test_concurrent_server_cells_stay_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, ws_a, ws_b = (tmp_path / n for n in ("host", "a", "b"))
    for path in (host, ws_a, ws_b):
        path.mkdir()
    monkeypatch.chdir(host)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def run(conversation_id: str, workspace: Path, text: str) -> None:
        def cell() -> None:
            barrier.wait()
            _execute(f"from pathlib import Path; Path('out.txt').write_text({text!r})")

        try:
            _in_server_session(conversation_id, workspace, cell)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=("conv-a", ws_a, "from-a")),
        threading.Thread(target=run, args=("conv-b", ws_b, "from-b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert (ws_a / "out.txt").read_text() == "from-a"
    assert (ws_b / "out.txt").read_text() == "from-b"
    assert Path.cwd() == host


def test_blocking_helper_releases_cwd_lock_for_child_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parent cell that waits must not deadlock a child's in-process Python.

    subagent_parallel joins children while the parent cell still holds the
    cwd lock unless that wait suspends the pin (gptme/gptme#4951).
    """
    host, parent_ws, child_ws = (tmp_path / n for n in ("host", "parent", "child"))
    for path in (host, parent_ws, child_ws):
        path.mkdir()
    monkeypatch.chdir(host)
    started = threading.Event()
    child_done = threading.Event()

    def blocking_helper() -> str:
        with python_tool.suspend_inprocess_session_cwd():
            started.set()
            assert child_done.wait(timeout=10)
        return "ok"

    python_tool.register_function(blocking_helper, tool_name="test")

    def parent() -> None:
        _execute("blocking_helper()")

    def child() -> None:
        assert started.wait(timeout=10)
        _in_server_session(
            "conv-child",
            child_ws,
            lambda: _execute(
                "from pathlib import Path; Path('from-child.txt').write_text('ok')"
            ),
        )
        child_done.set()

    child_thread = threading.Thread(target=child)
    child_thread.start()
    _in_server_session("conv-parent", parent_ws, parent)
    child_thread.join(timeout=10)
    assert not child_thread.is_alive()
    assert (child_ws / "from-child.txt").read_text() == "ok"
    assert Path.cwd() == host


def test_suspend_restores_the_cell_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, workspace, subdir = (tmp_path / n for n in ("host", "workspace", "sub"))
    for path in (host, workspace, subdir):
        path.mkdir()
    (workspace / "sub").mkdir()
    cell_dir = workspace / "sub"
    monkeypatch.chdir(host)

    def parent() -> None:
        with python_tool._inprocess_session_cwd():
            os.chdir(cell_dir)
            with python_tool.suspend_inprocess_session_cwd():
                assert Path.cwd() == host
            assert Path.cwd() == cell_dir
        assert Path.cwd() == host

    _in_server_session("conv-a", workspace, parent)


def test_suspend_does_not_capture_child_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, parent_ws, child_ws = (tmp_path / n for n in ("host", "parent", "child"))
    for path in (host, parent_ws, child_ws):
        path.mkdir()
    monkeypatch.chdir(host)
    started = threading.Event()
    child_done = threading.Event()

    def blocking_helper() -> str:
        with python_tool.suspend_inprocess_session_cwd():
            started.set()
            assert child_done.wait(timeout=10)
        return "ok"

    python_tool.register_function(blocking_helper, tool_name="test")

    def child() -> None:
        assert started.wait(timeout=10)
        _in_server_session(
            "conv-child",
            child_ws,
            lambda: _execute("print('child-secret')"),
        )
        child_done.set()

    child_thread = threading.Thread(target=child)
    child_thread.start()
    messages = _in_server_session(
        "conv-parent",
        parent_ws,
        lambda: _execute(
            "print('parent-before'); blocking_helper(); print('parent-after')"
        ),
    )
    child_thread.join(timeout=10)
    text = "\n".join(m.content for m in messages)
    assert "child-secret" not in text
    assert "parent-before" in text
    assert "parent-after" in text
    assert Path.cwd() == host


def test_cli_relative_cell_uses_process_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, other = (tmp_path / n for n in ("host", "other"))
    for path in (host, other):
        path.mkdir()
    monkeypatch.chdir(host)

    _execute("from pathlib import Path; Path('out.txt').write_text('cli')")

    assert (host / "out.txt").read_text() == "cli"
    assert not (other / "out.txt").exists()
    assert Path.cwd() == host


def test_cli_cell_os_chdir_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, sub = (tmp_path / n for n in ("host", "sub"))
    for path in (host, sub):
        path.mkdir()
    monkeypatch.chdir(host)

    _execute(f"import os; os.chdir({str(sub)!r})")
    assert Path.cwd() == sub
    _execute("from pathlib import Path; Path('out.txt').write_text('cli')")
    assert (sub / "out.txt").read_text() == "cli"


def test_inprocess_plot_snapshot_uses_session_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, workspace = (tmp_path / n for n in ("host", "workspace"))
    for path in (host, workspace):
        path.mkdir()
    monkeypatch.chdir(host)

    messages = _in_server_session(
        "conv-a",
        workspace,
        lambda: _execute(
            "from pathlib import Path; Path('plot.png').write_bytes(b'png')"
        ),
    )
    artifacts = (messages[-1].metadata or {}).get("artifacts", [])
    assert [a["path"] for a in artifacts] == [str(workspace / "plot.png")]
    assert Path.cwd() == host


def test_cli_context_keeps_the_process_global_shell() -> None:
    # Without a conversation id (CLI ``chat``) there is one shell per process.
    assert _shell(None) is _shell(None)
    assert _shell(None) is python_tool._ipython


def test_reset_drops_the_conversation_shell() -> None:
    first = _shell("conv-a")
    python_tool.reset_conversation_ipython("conv-a")
    assert _shell("conv-a") is not first


def test_reused_context_follows_conversation_and_reset() -> None:
    def switch_and_reset() -> None:
        current_conversation_id.set("conv-a")
        first = python_tool._get_ipython()
        current_conversation_id.set("conv-b")
        assert python_tool._get_ipython() is not first
        current_conversation_id.set("conv-a")
        assert python_tool._get_ipython() is first
        python_tool.reset_conversation_ipython("conv-a")
        assert python_tool._get_ipython() is not first

    contextvars.copy_context().run(switch_and_reset)
