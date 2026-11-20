"""File tools resolve relative paths in the session workspace, not the process cwd.

A server runs several conversations in one process and ``os.chdir`` is
process-wide, so each session sets a per-context workspace
(``set_workspace_cwd``). Two sessions with different workspaces must not see
each other's, whatever the process cwd happens to be.
"""

import contextvars
import os
import shlex
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from gptme.tools.base import current_workspace
from gptme.tools.shell import set_workspace_cwd


def _in_session(workspace: Path, fn):
    """Run ``fn`` in its own context with ``workspace`` as the session workspace."""

    def run():
        set_workspace_cwd(str(workspace))
        return fn()

    return contextvars.copy_context().run(run)


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    a, b, elsewhere = (tmp_path / n for n in ("a", "b", "elsewhere"))
    for d in (a, b, elsewhere):
        d.mkdir()
    # The process cwd is a *third* directory, as after another session's chdir.
    monkeypatch.chdir(elsewhere)
    return a, b, elsewhere


def test_current_workspace_defaults_to_cwd(workspaces):
    _, _, elsewhere = workspaces
    assert current_workspace() == elsewhere.resolve()


def test_current_workspace_prefers_session(workspaces):
    a, _, _ = workspaces
    assert _in_session(a, current_workspace) == a.resolve()


@pytest.mark.parametrize(
    "operation", ["workspace", "precommit", "save", "python", "auto_include"]
)
def test_tools_follow_shell_cd_in_each_session(
    workspaces: tuple[Path, Path, Path],
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gptme.tools.precommit import _get_repo_root
    from gptme.tools.save import execute_save_impl
    from gptme.tools.shell import ShellSession, get_shell

    a, b, elsewhere = workspaces
    monkeypatch.setenv("GPTME_SHELL_MEMORY_GUARD", "0")
    monkeypatch.setenv("GPTME_SANDBOX", "none")
    monkeypatch.setattr("gptme.tools.python._resolve_kernel_backend", lambda: False)
    sessions: list[tuple[contextvars.Context, ShellSession, Path, Path]] = []
    try:
        for root in (a, b):
            project = root / "project"
            project.mkdir()
            for repo in (root, project):
                subprocess.run(["git", "init", "-q", str(repo)], check=True)
            context = contextvars.copy_context()

            def start(root: Path = root, project: Path = project) -> ShellSession:
                set_workspace_cwd(str(root))
                shell = get_shell()
                status, _, error = shell.run(f"cd {shlex.quote(str(project))}")
                assert status == 0, error
                return shell

            shell = context.run(start)
            sessions.append((context, shell, root, project))

        for context, _, root, project in sessions:

            def use_tools(root: Path = root, project: Path = project) -> None:
                # The next server step rebinds the initial workspace. The live
                # shell's cwd must still win without moving the host process.
                set_workspace_cwd(str(root))
                if operation == "workspace":
                    assert current_workspace() == project.resolve()
                elif operation == "precommit":
                    repo_root = _get_repo_root()
                    assert repo_root is not None
                    assert repo_root.resolve() == project.resolve()
                elif operation == "python":
                    from gptme.tools import python as python_tool

                    list(
                        python_tool.execute_python(
                            "from pathlib import Path; "
                            f"Path('py-out.txt').write_text({root.name!r})",
                            [],
                            None,
                        )
                    )
                    assert (project / "py-out.txt").read_text().strip() == root.name
                    assert not (root / "py-out.txt").exists()
                elif operation == "auto_include":
                    (project / "marker.txt").write_text(root.name)
                    from gptme.util.context import _find_potential_paths

                    monkeypatch.setenv("GPTME_AUTO_INCLUDE_PATHS", "1")
                    assert "marker.txt" in _find_potential_paths("see marker.txt")
                else:
                    list(execute_save_impl(root.name, Path("result.txt")))
                    assert (project / "result.txt").read_text().strip() == root.name
                    assert not (root / "result.txt").exists()

            context.run(use_tools)
        assert Path.cwd() == elsewhere
    finally:
        for _, shell, _, _ in sessions:
            shell.close()
        from gptme.tools import python as python_tool

        ipython = python_tool._ipython
        if ipython is not None:
            history = ipython.history_manager
            if history is not None and history.save_thread is not None:
                history.save_thread.stop()
            python_tool._ipython = None


def test_save_relative_path_lands_in_session_workspace(workspaces):
    a, b, elsewhere = workspaces

    from gptme.tools.save import execute_save_impl

    for ws, text in ((a, "from a"), (b, "from b")):
        _in_session(
            ws, lambda text=text: list(execute_save_impl(text, Path("out.txt")))
        )

    assert (a / "out.txt").read_text().strip() == "from a"
    assert (b / "out.txt").read_text().strip() == "from b"
    assert not (elsewhere / "out.txt").exists()
    assert os.getcwd() == str(elsewhere.resolve())


def test_read_relative_path_reads_session_workspace(workspaces):
    a, b, _ = workspaces
    (a / "note.txt").write_text("note in a")
    (b / "note.txt").write_text("note in b")
    from gptme.tools.read import execute_read

    def read():
        return "\n".join(m.content for m in execute_read(None, ["note.txt"], None))

    assert "note in a" in _in_session(a, read)
    assert "note in b" in _in_session(b, read)


def test_traversal_guard_uses_session_workspace(workspaces):
    a, b, _ = workspaces
    from gptme.tools.save import execute_save_impl

    # ../b/x escapes workspace a, even though the process cwd's parent is shared.
    def escape():
        return list(execute_save_impl("x", Path("../b/x")))

    with pytest.raises(ValueError, match="Path traversal"):
        _in_session(a, escape)
    assert not (b / "x").exists()


def test_save_preview_and_baseline_use_session_workspace(workspaces):
    a, _, elsewhere = workspaces
    (a / "f.txt").write_text("workspace content\n")
    (elsewhere / "f.txt").write_text("cwd content\n")
    from gptme.tools.save import _SaveBaseline, preview_append, preview_save

    def run():
        baseline = _SaveBaseline.capture(Path("f.txt"))
        return (
            baseline.resolved,
            preview_save("new\n", Path("f.txt")),
            preview_append("more\n", Path("f.txt")),
        )

    resolved, save_prev, append_prev = _in_session(a, run)
    assert resolved == (a / "f.txt").resolve()
    assert "workspace content" in save_prev and "cwd content" not in save_prev
    assert "workspace content" in append_prev and "cwd content" not in append_prev


def test_view_image_relative_path_uses_session_workspace(workspaces):
    a, _, elsewhere = workspaces
    from PIL import Image

    from gptme.tools.vision import view_image

    Image.new("RGB", (4, 4), "red").save(a / "pic.png")

    msg = _in_session(a, lambda: view_image(Path("pic.png")))
    assert "Image not found" not in msg.content
    assert "outside allowed" not in msg.content
    assert msg.files and msg.files[0] == (a / "pic.png").resolve()
    assert not (elsewhere / "pic.png").exists()


@pytest.mark.timeout(10)
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs not supported on Windows")
@pytest.mark.parametrize("operation", ["save", "append"])
def test_save_append_refuse_workspace_fifo(workspaces, operation):
    """A FIFO that exists only in the session workspace is refused, not opened."""
    a, _, elsewhere = workspaces
    os.mkfifo(a / "pipe")
    from gptme.tools.save import execute_append, execute_save

    execute = execute_save if operation == "save" else execute_append
    messages = _in_session(a, lambda: list(execute("hello\n", ["pipe"], None)))
    assert len(messages) == 1
    assert "not a regular file" in messages[0].content
    assert not (elsewhere / "pipe").exists()


def test_precommit_detects_config_in_session_workspace(workspaces, monkeypatch):
    a, b, _ = workspaces
    (a / ".pre-commit-config.yaml").write_text("repos: []\n")
    from gptme.tools.precommit import use_checks

    # Pin auto-detection and runner availability, not the developer's config/PATH.
    config = Mock()
    config.get_env.return_value = ""
    monkeypatch.setattr("gptme.tools.precommit.get_config", lambda: config)
    monkeypatch.setattr(
        "gptme.tools.precommit.shutil.which",
        lambda name: str(a / name),
    )

    assert _in_session(a, use_checks) is True
    assert _in_session(b, use_checks) is False


def test_precommit_repo_root_is_session_repo(workspaces):
    import subprocess

    a, b, _ = workspaces
    for ws in (a, b):
        subprocess.run(["git", "init", "-q", str(ws)], check=True)

    from gptme.tools.precommit import _get_repo_root

    assert _in_session(a, _get_repo_root).resolve() == a.resolve()
    assert _in_session(b, _get_repo_root).resolve() == b.resolve()


def test_vent_records_session_workspace(workspaces, monkeypatch, tmp_path):
    import json

    a, _, _ = workspaces
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    from gptme.tools.vent import _vent_this_turn, execute_vent

    def vent():
        _vent_this_turn.set(False)
        return execute_vent("stuck", None, None)

    _in_session(a, vent)
    ledger = tmp_path / "data" / "gptme" / "friction-ledger.jsonl"
    entry = json.loads(ledger.read_text().splitlines()[-1])
    assert entry["workspace"] == str(a.resolve())


def test_display_path_relative_to_session_workspace(workspaces):
    a, _, _ = workspaces
    f = a / "src" / "x.py"

    from gptme.util.context import file_to_display_path

    shown = _in_session(a, lambda: file_to_display_path(f, a.resolve()))
    assert shown == Path("src/x.py")


@pytest.mark.parametrize("check_modified", [False, True])
@pytest.mark.parametrize("source_state", ["unchanged", "modified", "missing"])
@pytest.mark.parametrize("relative", [False, True])
def test_attachment_reads_original_path_not_display_path(
    workspaces, check_modified, source_state, relative
):
    a, _, elsewhere = workspaces
    source = a / "note.txt"
    source.write_text("session attachment content")
    (elsewhere / "note.txt").write_text("other conversation content")

    from gptme.message import Message
    from gptme.util.context import embed_attached_file_content, md_codeblock

    attachment = source.relative_to(a) if relative else source
    msg = Message("user", "Read the attachment", files=[attachment])
    expected = "session attachment content"
    if source_state == "modified":
        later = msg.timestamp.timestamp() + 60
        os.utime(source, (later, later))
        if check_modified:
            expected = "<file was modified after message>"
    elif source_state == "missing":
        source.unlink()
        expected = "<file not found, may have been moved>"

    result = _in_session(
        a,
        lambda: embed_attached_file_content(msg, a.resolve(), check_modified),
    )

    if source_state == "missing" and not check_modified:
        assert result.content == msg.content
        assert result.files == [source]
    else:
        assert result.content == msg.content + "\n\n" + md_codeblock(
            "note.txt", expected
        )
        assert result.files == []


def test_binary_attachment_keeps_original_path(workspaces):
    a, _, elsewhere = workspaces
    from PIL import Image

    from gptme.message import Message
    from gptme.util.context import embed_attached_file_content

    source = a / "pic.png"
    Image.new("RGB", (2, 2), "red").save(source)
    Image.new("RGB", (2, 2), "blue").save(elsewhere / "pic.png")
    msg = Message("user", "Inspect the attachment", files=[source])

    result = _in_session(a, lambda: embed_attached_file_content(msg, a.resolve()))
    assert result.content == msg.content
    assert result.files == [source]
