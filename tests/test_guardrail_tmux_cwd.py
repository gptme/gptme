"""Guard checks query only an isolated tmux server and synthetic files."""

import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from gptme.hooks.guardrails import guardrail_hook, guardrail_pre_hook
from gptme.hooks.types import ToolExecutePreData
from gptme.tools.base import ToolUse


@pytest.fixture
def pane_tree(tmp_path, monkeypatch):
    if not shutil.which("tmux"):
        pytest.skip("tmux not installed")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GPTME_GUARDRAILS", "enforce")
    process = tmp_path / "process"
    pane = tmp_path / "pane"
    process.mkdir()
    pane.mkdir()
    monkeypatch.chdir(process)
    socket = f"gptme-guard-{uuid.uuid4().hex}"
    run = subprocess.run

    def isolated_run(cmd, *args, **kwargs):
        if cmd[0] == "tmux":
            cmd = ["tmux", "-L", socket, "-f", "/dev/null", *cmd[1:]]
        return run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", isolated_run)
    isolated_run(
        ["tmux", "new-session", "-d", "-s", "gptme_1", "-c", str(pane), "sleep 120"],
        check=True,
    )
    try:
        # new-session returns before its child necessarily reaches chdir().
        # Wait for the observable cwd, not an arbitrary startup delay, before
        # comparing two independent hook queries against the synthetic tree.
        from gptme.tools.tmux import get_tmux_pane_cwd

        deadline = time.monotonic() + 5
        while get_tmux_pane_cwd("1") != pane:
            if time.monotonic() >= deadline:
                pytest.fail("isolated tmux pane did not reach its requested directory")
            time.sleep(0.01)
        yield process, pane
    finally:
        isolated_run(["tmux", "kill-server"], capture_output=True)


def blocked(use):
    confirm = guardrail_hook(use)
    data = ToolExecutePreData(tool_use=use)
    list(guardrail_pre_hook(data))
    assert (confirm is not None) == data.denied
    return confirm is not None


@pytest.mark.parametrize("target", ["1", "gptme_1", "gptme_1:0.0", "%0"])
@pytest.mark.parametrize("secret_in_pane", [True, False])
def test_send_keys_uses_target_pane_directory(pane_tree, target, secret_in_pane):
    process, pane = pane_tree
    secret_dir = pane if secret_in_pane else process
    safe_dir = process if secret_in_pane else pane
    (secret_dir / "config").mkdir()
    (safe_dir / "config").mkdir()
    (secret_dir / "config" / "client.pem").write_text("synthetic\n")
    (safe_dir / "config" / "notes.txt").write_text("ordinary\n")
    use = ToolUse("tmux", [], f"send-keys {target} 'cat config/*' Enter")
    assert blocked(use) == secret_in_pane


@pytest.mark.parametrize("excluded_in_pane", [True, False])
def test_exclusion_file_is_read_from_pane_directory(pane_tree, excluded_in_pane):
    process, pane = pane_tree
    for directory in (process, pane):
        (directory / ".env").write_text("TOKEN=synthetic\n")
    (pane / "exclude").write_text(".env\n" if excluded_in_pane else "nothing\n")
    (process / "exclude").write_text("nothing\n" if excluded_in_pane else ".env\n")
    use = ToolUse(
        "tmux", [], "send-keys 1 'grep -r TOKEN --exclude-from=exclude .' Enter"
    )
    assert blocked(use) != excluded_in_pane


@pytest.mark.parametrize("secret_in_process", [True, False])
def test_new_session_uses_process_directory_not_hook_workspace(
    pane_tree, secret_in_process
):
    process, pane = pane_tree
    secret_dir = process if secret_in_process else pane
    safe_dir = pane if secret_in_process else process
    (secret_dir / "config").mkdir()
    (secret_dir / "config" / "client.pem").write_text("synthetic\n")
    (safe_dir / "config").mkdir()
    (safe_dir / "config" / "notes.txt").write_text("ordinary\n")
    use = ToolUse("tmux", [], "new-session cat config/*")
    assert (guardrail_hook(use, workspace=pane) is not None) == secret_in_process
    data = ToolExecutePreData(tool_use=use, workspace=pane)
    list(guardrail_pre_hook(data))
    assert data.denied == secret_in_process


@pytest.mark.parametrize("output", ["unrelated\t/tmp\n", "gptme_1\t\n", "bad-format\n"])
def test_invalid_pane_context_is_not_used(monkeypatch, output):
    from gptme.tools.tmux import get_tmux_pane_cwd

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, output, ""),
    )
    assert get_tmux_pane_cwd("%0") is None


@pytest.mark.parametrize(
    "error", [OSError("missing tmux"), subprocess.TimeoutExpired("tmux", 2)]
)
def test_unavailable_tmux_context_falls_back(monkeypatch, error):
    from gptme.tools.tmux import get_tmux_pane_cwd

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(subprocess, "run", fail)
    assert get_tmux_pane_cwd("1") is None


def test_metadata_query_does_not_interpret_option_like_target(monkeypatch):
    from gptme.tools.tmux import get_tmux_pane_cwd

    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "gptme_1\t/tmp\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert get_tmux_pane_cwd("-x") == Path("/tmp")
    assert calls[0][4] == "gptme_-x"


def test_unavailable_pane_retains_process_relative_checks(pane_tree, monkeypatch):
    process, _ = pane_tree
    (process / "config").mkdir()
    (process / "config" / "client.pem").write_text("synthetic\n")
    monkeypatch.setattr("gptme.tools.tmux.get_tmux_pane_cwd", lambda _: None)
    assert blocked(ToolUse("tmux", [], "send-keys 1 'cat config/*' Enter"))


def test_unavailable_process_cwd_does_not_hide_literal_secret(pane_tree, monkeypatch):
    def unavailable():
        raise FileNotFoundError("cwd unavailable")

    monkeypatch.setattr(Path, "cwd", staticmethod(unavailable))
    assert blocked(ToolUse("tmux", [], "new-session cat ~/.ssh/id_rsa"))


def test_new_session_checks_the_session_workspace(pane_tree, monkeypatch):
    """``new-session`` starts in the session workspace, not the process cwd.

    A server serves several conversations and chdir's the whole process for one
    of them, so the process cwd names another conversation's directory.
    """
    from gptme.tools import shell

    process, _ = pane_tree
    session = process.parent / "session"
    (session / "config").mkdir(parents=True)
    (session / "config" / "client.pem").write_text("synthetic\n")
    monkeypatch.setattr(shell, "get_workspace_cwd", lambda: str(session))
    assert blocked(ToolUse("tmux", [], "new-session cat config/*"))


def test_new_session_ignores_a_foreign_process_cwd(pane_tree, monkeypatch):
    """A secret in the process cwd but not the session workspace is not a hit."""
    from gptme.tools import shell

    process, _ = pane_tree
    session = process.parent / "session"
    (session / "config").mkdir(parents=True)
    (session / "config" / "notes.txt").write_text("ordinary\n")
    (process / "config").mkdir()
    (process / "config" / "client.pem").write_text("synthetic\n")
    monkeypatch.setattr(shell, "get_workspace_cwd", lambda: str(session))
    assert not blocked(ToolUse("tmux", [], "new-session cat config/*"))
