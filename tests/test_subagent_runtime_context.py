"""Thread children bind their own workspace and conversation tool state."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import Context
from importlib import import_module
from pathlib import Path
from threading import Barrier

import pytest

from gptme.hooks import current_conversation_id, current_session_id
from gptme.tools import shell
from gptme.tools.base import current_workspace
from gptme.tools.save import execute_save_impl
from gptme.tools.subagent import execution
from gptme.util.workspace import get_sandbox_root


@pytest.fixture
def child_runner(monkeypatch):
    """Keep prompt/model setup offline; run the real thread entry and file/shell tools."""
    monkeypatch.setattr(
        import_module("gptme.executor"),
        "prepare_execution_environment",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        import_module("gptme.prompts"), "get_prompt", lambda *a, **kw: []
    )
    monkeypatch.setattr(execution, "_ensure_subagent_signal_tools_loaded", lambda: None)
    monkeypatch.setattr(execution, "get_tools", lambda: [])
    monkeypatch.setattr(execution, "_load_agent_memory", lambda profile: (None, None))

    def run(workspace: Path, logdir: Path) -> None:
        execution._create_subagent_thread(
            prompt="work",
            logdir=logdir,
            model="mock/echo",
            context_mode="full",
            context_include=None,
            workspace=workspace,
        )

    return run


@pytest.fixture
def parent_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_SHELL_MEMORY_GUARD", "0")
    workspace = tmp_path / "parent"
    workspace.mkdir()
    context = Context()
    context.run(shell.set_workspace_cwd, str(workspace))
    context.run(current_conversation_id.set, "parent")
    context.run(current_session_id.set, "parent-request")
    parent_shell = context.run(shell.get_shell)
    # A copied context may carry the replacement-shell seed from a promoted call.
    context.run(shell._promoted_next_cwd.set, str(workspace))
    context.run(shell._promoted_next_state.set, "export CHILD_MUST_NOT_INHERIT=1")
    try:
        yield context, workspace, parent_shell
    finally:
        shell.close_conversation_shell("parent")


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("inherit_parent", [False, True])
def test_child_runtime_isolated_and_cleaned(
    tmp_path, monkeypatch, child_runner, parent_runtime, fails, inherit_parent
):
    parent, parent_workspace, parent_shell = parent_runtime
    workspace = tmp_path / "child"
    workspace.mkdir()
    logdir = tmp_path / "child-conversation"
    seen = []

    def chat(*args, **kwargs):
        assert current_conversation_id.get() == logdir.name
        assert current_session_id.get() is None
        assert current_workspace() == workspace
        assert get_sandbox_root() == workspace.resolve()
        child_shell = shell.get_shell()
        seen.append(child_shell)
        assert child_shell is not parent_shell
        assert child_shell.get_cwd() == workspace
        assert shell._promoted_next_cwd.get() is None
        assert shell._promoted_next_state.get() is None
        list(execute_save_impl("child data", Path("answer.txt")))
        if fails:
            raise RuntimeError("child failed")

    monkeypatch.setattr(import_module("gptme.chat"), "chat", chat)
    cwd_before = Path.cwd()
    child_context = parent.copy() if inherit_parent else Context()
    if fails:
        with pytest.raises(RuntimeError, match="child failed"):
            child_context.run(child_runner, workspace, logdir)
    else:
        child_context.run(child_runner, workspace, logdir)

    assert (workspace / "answer.txt").read_text().strip() == "child data"
    assert not (parent_workspace / "answer.txt").exists()
    assert parent.run(current_conversation_id.get) == "parent"
    assert parent.run(current_session_id.get) == "parent-request"
    assert parent.run(current_workspace) == parent_workspace
    assert parent.run(get_sandbox_root) == parent_workspace.resolve()
    assert parent.run(shell.get_shell) is parent_shell
    assert parent_shell.process.poll() is None
    assert seen[0].process.poll() is not None
    assert logdir.name not in shell._conversation_shells
    assert Path.cwd() == cwd_before


def test_concurrent_children_use_distinct_runtimes(
    tmp_path, monkeypatch, child_runner, parent_runtime
):
    parent, _, parent_shell = parent_runtime
    workspaces = [tmp_path / "a", tmp_path / "b"]
    for workspace in workspaces:
        workspace.mkdir()
    ready = Barrier(2)
    seen = []

    def chat(*args, logdir, workspace, **kwargs):
        child_shell = shell.get_shell()
        seen.append(child_shell)
        ready.wait(timeout=10)
        assert current_conversation_id.get() == logdir.name
        assert current_workspace() == workspace
        assert get_sandbox_root() == workspace.resolve()
        assert child_shell is not parent_shell
        list(execute_save_impl(workspace.name, Path("answer.txt")))
        assert child_shell.get_cwd() == workspace

    monkeypatch.setattr(import_module("gptme.chat"), "chat", chat)
    contexts = [parent.copy(), parent.copy()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(context.run, child_runner, workspace, tmp_path / f"log-{i}")
            for i, (context, workspace) in enumerate(zip(contexts, workspaces))
        ]
        for future in futures:
            future.result(timeout=20)

    assert seen[0] is not seen[1]
    for workspace in workspaces:
        assert (workspace / "answer.txt").read_text().strip() == workspace.name
    assert parent_shell.process.poll() is None
    assert all(child.process.poll() is not None for child in seen)


@pytest.mark.parametrize("fails", [False, True])
def test_child_kernel_shutdown_preserves_parent(
    tmp_path, monkeypatch, child_runner, parent_runtime, fails
):
    """Real child kernels and bridges must exit, including when chat raises."""
    import sys

    pytest.importorskip("jupyter_client")
    pytest.importorskip("ipykernel")
    from gptme.tools import python_kernel

    parent, _, _ = parent_runtime
    workspace = tmp_path / "kernel-child"
    workspace.mkdir()
    logdir = tmp_path / "kernel-child-conversation"
    monkeypatch.setattr(
        python_kernel.KernelBackend,
        "_find_project_python",
        staticmethod(lambda: sys.executable),
    )
    parent_backend = parent.run(python_kernel.get_backend)
    seen = []

    def chat(*args, **kwargs):
        assert current_conversation_id.get() == logdir.name
        python_kernel.freeze_backend_choice(logdir.name, True)
        backend = python_kernel.get_backend()
        seen.append(backend)
        assert backend is not parent_backend
        result = backend.execute("6 * 7")
        assert result.error is None, result.error
        assert result.result_text == "42"
        assert backend.is_alive()
        if fails:
            raise RuntimeError("child failed")

    monkeypatch.setattr(import_module("gptme.chat"), "chat", chat)
    try:
        assert parent.run(parent_backend.execute, "parent_value = 17").error is None
        if fails:
            with pytest.raises(RuntimeError, match="child failed"):
                parent.copy().run(child_runner, workspace, logdir)
        else:
            parent.copy().run(child_runner, workspace, logdir)

        assert not seen[0].is_alive()
        assert seen[0]._bridge._thread is None
        assert logdir.name not in python_kernel._backends
        assert python_kernel.get_backend_choice(logdir.name) is None
        assert parent.run(python_kernel.get_backend) is parent_backend
        assert parent.run(parent_backend.execute, "parent_value").result_text == "17"
    finally:
        # Also reclaim the deliberately leaked backend during the RED test run.
        for conversation_id in (logdir.name, "parent"):
            backend = python_kernel.detach_backend(conversation_id)
            if backend is not None:
                backend.shutdown()
