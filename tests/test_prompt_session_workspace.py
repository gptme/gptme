"""Prompt attachments follow the session workspace without a process chdir."""

from contextvars import Context
from pathlib import Path

import pytest

from gptme.message import Message
from gptme.tools.shell import set_workspace_cwd
from gptme.util.context import _find_potential_paths, include_paths


def in_workspace(workspace, function, *args, **kwargs):
    context = Context()
    context.run(set_workspace_cwd, str(workspace))
    return context.run(function, *args, **kwargs)


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    a, b, host = (tmp_path / name for name in ("a", "b", "host"))
    for path in (a, b, host):
        path.mkdir()
    (a / "note.txt").write_text("WORKSPACE-A")
    (b / "note.txt").write_text("WORKSPACE-B")
    (host / "host-only.txt").write_text("HOST-ONLY")
    monkeypatch.chdir(host)
    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    monkeypatch.delenv("GPTME_FRESH", raising=False)
    monkeypatch.delenv("GPTME_AUTO_INCLUDE_PATHS", raising=False)
    return a, b, host


@pytest.mark.parametrize("fresh", [False, True])
@pytest.mark.parametrize("reference", ["@note.txt", "@./note.txt", "note.txt"])
def test_text_attachment_uses_session_workspace(
    workspaces, monkeypatch, fresh, reference
):
    a, b, host = workspaces
    if fresh:
        monkeypatch.setenv("GPTME_FRESH", "1")
    if not reference.startswith("@"):
        monkeypatch.setenv("GPTME_AUTO_INCLUDE_PATHS", "1")

    for workspace, content in ((a, "WORKSPACE-A"), (b, "WORKSPACE-B")):
        message = in_workspace(
            workspace, include_paths, Message("user", f"read {reference}"), workspace
        )
        if fresh:
            assert message.files == [workspace / "note.txt"]
            assert message.files[0].read_text() == content
        else:
            assert content in message.content
            other_content = "WORKSPACE-B" if workspace == a else "WORKSPACE-A"
            assert other_content not in message.content
    assert Path.cwd() == host


def test_binary_attachment_remains_readable_outside_session(workspaces):
    a, _, host = workspaces
    image = a / "picture.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    message = in_workspace(
        a, include_paths, Message("user", "inspect @./picture.png"), a
    )

    assert message.files == [image]
    assert message.files[0].read_bytes() == image.read_bytes()
    assert Path.cwd() == host


def test_explicit_name_in_host_directory_is_not_a_session_reference(workspaces):
    a, _, _ = workspaces
    assert (
        in_workspace(
            a, _find_potential_paths, "read @host-only.txt", auto_include=False
        )
        == []
    )


@pytest.mark.parametrize("fresh", [False, True])
def test_symlink_attachment_keeps_reference_file_type(
    workspaces, monkeypatch, fresh, requires_symlinks
):
    a, _, _ = workspaces
    target = a / "cache.bin"
    target.write_text("SYMLINKED-TEXT")
    link = a / "notes.txt"
    link.symlink_to("cache.bin")
    if fresh:
        monkeypatch.setenv("GPTME_FRESH", "1")

    message = in_workspace(a, include_paths, Message("user", "read @notes.txt"), a)
    if fresh:
        assert message.files == [link]
        assert message.files[0].read_text() == "SYMLINKED-TEXT"
    else:
        assert "SYMLINKED-TEXT" in message.content
