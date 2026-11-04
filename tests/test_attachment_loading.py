"""Attachment policy preserves explicit loading and legacy opt-in boundaries."""

import pytest
from PIL import Image

from gptme.message import Message
from gptme.prompts import prompt_workspace
from gptme.util import context


@pytest.fixture(autouse=True)
def isolated_attachment_policy(monkeypatch):
    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    monkeypatch.delenv("GPTME_AUTO_INCLUDE_PATHS", raising=False)
    monkeypatch.setattr(context, "use_fresh_context", lambda: False)


def test_fresh_text_attaches_explicit_path_but_not_plain_mention(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "use_fresh_context", lambda: True)
    source = tmp_path / "notes.txt"
    source.write_text("initial-content-marker")
    plain = Message("user", f"Discuss {source} later")
    explicit = Message("user", f"Read @{source}")

    assert context._include_paths(plain, tmp_path) is plain
    attached = context._include_paths(explicit, tmp_path)
    assert attached.content == explicit.content
    assert attached.files == [source]

    # Fresh mode defers text loading rather than snapshotting it at submission.
    source.write_text("updated-content-marker")
    embedded = context.embed_attached_file_content(attached, tmp_path)
    assert "updated-content-marker" in embedded.content
    assert "initial-content-marker" not in embedded.content
    assert embedded.files == []
    assert context.embed_attached_file_content(plain, tmp_path).content == plain.content


def test_legacy_opt_in_attaches_plain_image_and_lists_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GPTME_AUTO_INCLUDE_PATHS", "1")
    picture = tmp_path / "image.png"
    Image.new("RGB", (2, 2), "red").save(picture)
    directory = tmp_path / "src"
    directory.mkdir()
    (directory / "child.py").write_text("child-content-marker")
    prompt = "Inspect image.png and ./src/"

    result = context._include_paths(Message("user", prompt), tmp_path)

    assert result.content.startswith(prompt)
    assert [tmp_path / path for path in result.files] == [picture]
    assert "child.py" in result.content
    assert "child-content-marker" not in result.content


@pytest.mark.parametrize("disabled", [False, True])
def test_configured_workspace_files_load_independently_of_path_policy(
    tmp_path, monkeypatch, disabled
):
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    source = workspace / "briefing.txt"
    source.write_text("configured-content-marker")
    (workspace / "unselected.txt").write_text("unselected-content-marker")
    (workspace / "gptme.toml").write_text('[prompt]\nfiles = ["briefing.txt"]\n')
    if disabled:
        monkeypatch.setenv("GPTME_DISABLE_PATH_INCLUDE", "1")

    # Real TOML configuration and workspace discovery, with unrelated runtime
    # context disabled so this checks the configured file-loading boundary.
    messages = list(
        prompt_workspace(
            workspace,
            include_user_context=False,
            include_context_cmd=False,
            include_runtime_context=False,
        )
    )
    selected = [message for message in messages if source in message.files]
    assert len(selected) == 1
    assert selected[0].role == "system"
    assert selected[0].files == [source]
    assert context._include_paths(selected[0], workspace) is selected[0]

    embedded = context.embed_attached_file_content(selected[0], workspace)
    assert "configured-content-marker" in embedded.content
    assert "unselected-content-marker" not in embedded.content
    assert embedded.files == []

    # Programmatic message attachments use the same metadata-loading boundary.
    user = Message("user", "Use the attached briefing", files=[source])
    assert context._include_paths(user, workspace) is user
    assert (
        "configured-content-marker"
        in context.embed_attached_file_content(user, workspace).content
    )
