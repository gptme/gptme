"""File tools resolve relative paths in the session workspace, not the process cwd.

A server runs several conversations in one process and ``os.chdir`` is
process-wide, so each session sets a per-context workspace
(``set_workspace_cwd``). Two sessions with different workspaces must not see
each other's, whatever the process cwd happens to be.
"""

import contextvars
import os
from pathlib import Path

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
