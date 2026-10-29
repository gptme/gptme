"""Tests for the save and append tools."""

import os
from pathlib import Path

import pytest

from gptme.hooks import ConfirmationResult
from gptme.message import Message
from gptme.tools import save as save_tool
from gptme.tools.base import ToolUse
from gptme.tools.save import (
    _get_preview_lang,
    _read_text_safe,
    execute_append,
    execute_append_impl,
    execute_save,
    execute_save_impl,
    preview_append,
    preview_save,
)


def test_save_tool(tmp_path: Path):
    """Test the save tool."""
    # Test saving a new file
    path = tmp_path / "test.txt"
    content = "Hello, world!\n"
    messages = list(execute_save(content, [str(path)], None))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert path.read_text() == content

    # Test saving an existing file
    content = "Hello again!\n"
    messages = list(execute_save(content, [str(path)], None))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert path.read_text() == content

    # Test saving to a non-existent directory
    path = tmp_path / "subdir" / "test.txt"
    content = "Hello, world!\n"
    messages = list(execute_save(content, [str(path)], None))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert path.read_text() == content


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("final_newline", [False, True])
@pytest.mark.parametrize(
    "content",
    ["third\nfourth", "third\nfourth\n", "third\r\nfourth", "third\r\nfourth\r\n"],
)
def test_save_preserves_line_endings(
    tmp_path: Path, newline: bytes, final_newline: bool, content: str
) -> None:
    path = tmp_path / "existing.txt"
    original = newline.join([b"first", b"second"])
    if final_newline:
        original += newline
    path.write_bytes(original)

    messages = list(execute_save(content, [str(path)], None))

    assert len(messages) == 1
    assert "Saved to" in messages[0].content
    assert path.read_bytes() == newline.join([b"third", b"fourth", b""])


@pytest.mark.parametrize("original", [None, b"", b"\xff\xfe\r\n"])
@pytest.mark.parametrize("content", ["first\nsecond\n", "first\r\nsecond\r\n"])
def test_save_without_text_convention(
    tmp_path: Path, original: bytes | None, content: str
) -> None:
    path = tmp_path / "new.txt"
    if original is not None:
        path.write_bytes(original)

    list(execute_save(content, [str(path)], None))

    assert path.read_bytes() == content.encode("utf-8")


@pytest.mark.parametrize("original", [None, b"", b"old\r\ncontent\r\n", b"\xff\xfe"])
@pytest.mark.parametrize("native", [False, True])
def test_save_explicit_empty_content(
    tmp_path: Path, monkeypatch, original: bytes | None, native: bool
) -> None:
    path = tmp_path / "empty.txt"
    if original is not None:
        path.write_bytes(original)
    previews: list[str] = []

    def confirm(**kwargs):
        previews.append(kwargs["preview"])
        # Confirmation must happen before creation or truncation.
        assert path.exists() == (original is not None)
        if original is not None:
            assert path.read_bytes() == original
        return ConfirmationResult.confirm()

    monkeypatch.setattr("gptme.hooks.get_confirmation", confirm)
    messages = list(
        execute_save(
            "ignored fallback" if native else "",
            None if native else [str(path)],
            {"path": str(path), "content": ""} if native else None,
        )
    )

    assert any("Saved to" in message.content for message in messages)
    assert path.read_bytes() == b""
    assert len(previews) == 1
    if original == b"old\r\ncontent\r\n":
        assert "-old" in previews[0]
        assert "-content" in previews[0]
    else:
        assert previews == [""]


@pytest.mark.parametrize("original", [None, b"keep me\n"])
def test_empty_save_can_be_declined(tmp_path: Path, monkeypatch, original) -> None:
    path = tmp_path / "declined.txt"
    if original is not None:
        path.write_bytes(original)
    monkeypatch.setattr(
        "gptme.hooks.get_confirmation", lambda **_: ConfirmationResult.skip("declined")
    )

    messages = list(execute_save(None, None, {"path": str(path), "content": ""}))

    assert [m.content for m in messages] == ["declined"]
    assert path.exists() == (original is not None)
    if original is not None:
        assert path.read_bytes() == original


@pytest.mark.parametrize("content", [None, 0, False, [], {}])
def test_save_missing_or_invalid_content(tmp_path: Path, monkeypatch, content) -> None:
    path = tmp_path / "unchanged.txt"
    path.write_bytes(b"keep me\n")

    def unexpected_confirmation(**_):
        pytest.fail("missing/invalid content must not request confirmation")

    monkeypatch.setattr("gptme.hooks.get_confirmation", unexpected_confirmation)
    kwargs = {"path": str(path)}
    if content is not None:
        kwargs["content"] = content
    messages = list(execute_save(None, None, kwargs))

    assert [m.content for m in messages] == ["No content provided"]
    assert path.read_bytes() == b"keep me\n"


@pytest.mark.parametrize("native", [False, True])
def test_empty_append_remains_rejected(tmp_path: Path, native: bool) -> None:
    path = tmp_path / "unchanged.txt"
    path.write_bytes(b"no final newline")

    messages = list(
        execute_append(
            None if native else "",
            None if native else [str(path)],
            {"path": str(path), "content": ""} if native else None,
        )
    )

    assert [m.content for m in messages] == ["No content provided"]
    assert path.read_bytes() == b"no final newline"


@pytest.mark.parametrize("native", [False, True])
def test_empty_save_through_tool_use(tmp_path: Path, native: bool) -> None:
    path = tmp_path / "empty.txt"
    tool_use = (
        ToolUse("save", None, None, kwargs={"path": str(path), "content": ""})
        if native
        else ToolUse("save", [str(path)], "")
    )

    messages = list(tool_use.execute())

    assert any("Saved to" in message.content for message in messages)
    assert path.read_bytes() == b""


@pytest.mark.parametrize("edited", ["replacement", ""])
def test_empty_save_confirmation_edits(
    tmp_path: Path, monkeypatch, edited: str
) -> None:
    path = tmp_path / "edited.txt"
    path.write_bytes(b"original\n")
    previews: list[str] = []

    def confirm(**kwargs):
        previews.append(kwargs["preview"])
        if len(previews) == 1:
            return ConfirmationResult.edit(edited)
        return ConfirmationResult.confirm()

    monkeypatch.setattr("gptme.hooks.get_confirmation", confirm)
    messages = list(execute_save(None, None, {"path": str(path), "content": ""}))

    if edited:
        assert path.read_bytes() == b"replacement\n"
        assert len(previews) == 2
        assert "+replacement" in previews[1]
        assert messages[-1].content == "(content was edited by user)"
    else:
        # Keep the shared confirmation contract: an empty edit cancels.
        assert path.read_bytes() == b"original\n"
        assert len(previews) == 1
        assert "Editing returned no content" in messages[0].content


@pytest.mark.parametrize("target_kind", ["directory", "fifo", "escape"])
def test_empty_save_path_safeguards(
    tmp_path: Path, monkeypatch, target_kind: str
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "target"
    if target_kind == "escape":
        path = "../escape.txt"
    else:
        path = str(target)
        if target_kind == "directory":
            target.mkdir()
        elif hasattr(os, "mkfifo"):
            os.mkfifo(target)
        else:
            pytest.skip("FIFOs not supported on Windows")

    messages = list(execute_save("", [path], None))

    assert not any("Saved to" in message.content for message in messages)
    if target_kind == "escape":
        assert "Path traversal" in messages[0].content
        assert not (tmp_path.parent / "escape.txt").exists()
    else:
        assert "not a regular file" in messages[0].content


@pytest.mark.parametrize("approve_parent", [False, True])
def test_empty_save_missing_parent(
    tmp_path: Path, monkeypatch, approve_parent: bool
) -> None:
    path = tmp_path / "missing" / "empty.txt"
    monkeypatch.setattr(
        "gptme.hooks.get_confirmation", lambda **_: ConfirmationResult.confirm()
    )
    prompts: list[str] = []

    def confirm(prompt: str) -> bool:
        prompts.append(prompt)
        return approve_parent

    monkeypatch.setattr(save_tool, "confirm", confirm)
    messages = list(execute_save(None, None, {"path": str(path), "content": ""}))

    assert len(prompts) == 1
    if approve_parent:
        assert path.read_bytes() == b""
        assert "created missing folder" in messages[0].content
    else:
        assert not path.parent.exists()
        assert "refused to create a missing folder" in messages[0].content


def test_save_tool_placeholders(tmp_path: Path):
    """Test that the save tool detects placeholder lines."""
    path = tmp_path / "test.txt"
    placeholders = [
        "# ... rest of content goes here",
        "// ... rest of content goes here",
        "# ...",
        "// ...",
        '""" ... """',
        "# ... rest of the content is the same ...",
    ]
    for placeholder in placeholders:
        content = f"First line\n{placeholder}\nLast line\n"
        messages = list(execute_save(content, [str(path)], None))
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "placeholder lines" in messages[0].content


def test_append_tool(tmp_path: Path):
    """Test the append tool."""
    path = tmp_path / "test.txt"

    # Test appending to a new file
    content = "First line\n"
    messages = list(execute_append(content, [str(path)], None))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert path.read_text() == content

    # Test appending to existing file
    content2 = "Second line\n"
    messages = list(execute_append(content2, [str(path)], None))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert path.read_text() == content + content2


def test_append_tool_placeholders(tmp_path: Path):
    """Test that the append tool detects placeholder lines."""
    path = tmp_path / "test.txt"
    path.write_text("Existing content\n")

    placeholders = [
        "# ... rest of content goes here",
        "// ... rest of content goes here",
        "# ...",
        "// ...",
        '""" ... """',
        "# ... rest of the content is the same ...",
    ]
    for placeholder in placeholders:
        content = f"First line\n{placeholder}\nLast line\n"
        messages = list(execute_append(content, [str(path)], None))
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "placeholder lines" in messages[0].content


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("final_newline", [False, True])
@pytest.mark.parametrize(
    "content",
    ["third\nfourth", "third\nfourth\n", "third\r\nfourth", "third\r\nfourth\r\n"],
)
def test_append_preserves_line_endings(
    tmp_path: Path, newline: bytes, final_newline: bool, content: str
) -> None:
    path = tmp_path / "existing.txt"
    original = newline.join([b"first", b"second"])
    if final_newline:
        original += newline
    path.write_bytes(original)

    messages = list(execute_append(content, [str(path)], None))

    assert len(messages) == 1
    assert "Appended to" in messages[0].content
    assert path.read_bytes() == newline.join(
        [b"first", b"second", b"third", b"fourth", b""]
    )


def test_save_tool_path_traversal_relative(tmp_path: Path):
    """Test that path traversal via relative paths is blocked."""
    import os

    # Change to tmp_path so we can test relative paths
    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        # Create a subdirectory to work from
        subdir = tmp_path / "work"
        subdir.mkdir()
        os.chdir(subdir)

        # Try to escape with ../ - should return error message
        messages = list(execute_save("test", ["../../escape.txt"], None))
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "Path traversal detected" in messages[0].content
    finally:
        os.chdir(original_cwd)


def test_save_tool_path_traversal_symlink(tmp_path: Path, requires_symlinks):
    """Test that symlink-based path traversal is blocked."""
    import os

    # Change to tmp_path so cwd is known
    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        # Create a directory outside the cwd
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        # Create work directory
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        os.chdir(work_dir)

        # Create a symlink in work_dir pointing to outside_dir
        symlink = work_dir / "escape_link"
        symlink.symlink_to(outside_dir)

        # Try to save through symlink - should return error message
        messages = list(execute_save("test", ["escape_link/secret.txt"], None))
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "Path traversal detected" in messages[0].content
    finally:
        os.chdir(original_cwd)


def test_append_tool_path_traversal_relative(tmp_path: Path):
    """Test that path traversal via relative paths is blocked for append."""
    import os

    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        subdir = tmp_path / "work"
        subdir.mkdir()
        os.chdir(subdir)

        # Should return error message (matches save tool behavior)
        messages = list(execute_append("test", ["../../escape.txt"], None))
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "Path traversal" in messages[0].content
    finally:
        os.chdir(original_cwd)


def test_append_tool_path_traversal_symlink(tmp_path: Path, requires_symlinks):
    """Test that symlink-based path traversal is blocked for append."""
    import os

    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        os.chdir(work_dir)

        # Create a symlink in work_dir pointing to outside_dir
        symlink = work_dir / "escape_link"
        symlink.symlink_to(outside_dir)

        # Try to append through symlink - should return error message
        messages = list(execute_append("test", ["escape_link/secret.txt"], None))
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "Path traversal" in messages[0].content
    finally:
        os.chdir(original_cwd)


def test_read_text_safe_utf8(tmp_path: Path):
    """Test _read_text_safe with normal UTF-8 file."""
    path = tmp_path / "normal.txt"
    path.write_text("hello world\n")
    assert _read_text_safe(path) == "hello world\n"


def test_read_text_safe_binary(tmp_path: Path):
    """Test _read_text_safe returns None for binary files."""
    path = tmp_path / "binary.bin"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe")
    assert _read_text_safe(path) is None


def test_read_text_safe_nonexistent(tmp_path: Path):
    """Test _read_text_safe returns None for missing files."""
    path = tmp_path / "missing.txt"
    assert _read_text_safe(path) is None


def test_preview_save_binary_file(tmp_path: Path):
    """Test that preview_save handles binary files gracefully."""
    path = tmp_path / "binary.bin"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe")
    # When overwriting a binary file with text, show the full new content
    result = preview_save("new text content", path)
    assert result == "new text content"


def test_preview_append_binary_file(tmp_path: Path):
    """Test that preview_append handles binary files gracefully."""
    path = tmp_path / "binary.bin"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe")
    result = preview_append("appended text", path)
    assert result == "appended text"


def test_save_overwrites_binary_file(tmp_path: Path):
    """Test that save can overwrite a binary file without crashing."""
    path = tmp_path / "binary.bin"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe")
    messages = list(execute_save("new content", [str(path)], None))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "Saved to" in messages[0].content
    assert path.read_text() == "new content\n"


def test_append_to_binary_file(tmp_path: Path):
    """Test that append handles binary files without crashing."""
    path = tmp_path / "test.txt"
    # Write valid text first, then corrupt it with binary
    path.write_bytes(b"existing\xff\xfe")
    messages = list(execute_append("new line", [str(path)], None))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "Appended to" in messages[0].content
    assert path.read_bytes() == b"existing\xff\xfenew line\n"


def test_get_preview_lang_binary_file(tmp_path: Path):
    """Test that binary files don't get diff preview highlighting."""
    path = tmp_path / "binary.bin"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe")
    assert _get_preview_lang(path) is None


def test_execute_save_skips_diff_preview_for_binary_file(tmp_path: Path, monkeypatch):
    """Test that save passes no preview language for binary files."""
    path = tmp_path / "binary.bin"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe")
    captured: dict[str, str | None] = {}

    def fake_execute_with_confirmation(*args, **kwargs):
        captured["preview_lang"] = kwargs["preview_lang"]
        yield Message("system", "stub")

    monkeypatch.setattr(
        save_tool, "execute_with_confirmation", fake_execute_with_confirmation
    )

    messages = list(save_tool.execute_save("new content", [str(path)], None))

    assert len(messages) == 1
    assert messages[0].content == "stub"
    assert captured["preview_lang"] is None


@pytest.mark.timeout(10)
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs not supported on Windows")
@pytest.mark.parametrize("execute", [execute_save, execute_append])
def test_save_append_refuse_fifo(tmp_path: Path, execute):
    """Writing to (or previewing) a FIFO blocks forever; the tool must refuse it."""
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    messages = list(execute("hello\n", [str(fifo)], None))
    assert len(messages) == 1
    assert "not a regular file" in messages[0].content


@pytest.mark.parametrize("execute", [execute_save, execute_append])
def test_save_append_refuse_directory(tmp_path: Path, execute):
    """A directory target gets a clear message, not an IsADirectoryError."""
    target = tmp_path / "adir"
    target.mkdir()
    messages = list(execute("hello\n", [str(target)], None))
    assert len(messages) == 1
    assert "not a regular file" in messages[0].content
    assert target.is_dir()


@pytest.mark.timeout(10)
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs not supported on Windows")
@pytest.mark.parametrize("execute_impl", [execute_save_impl, execute_append_impl])
def test_impl_rechecks_target_after_confirmation(tmp_path: Path, execute_impl):
    """A target swapped for a FIFO after confirmation must not hang the write."""
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    messages = list(execute_impl("hello\n", fifo))
    assert len(messages) == 1
    assert "not a regular file" in messages[0].content


@pytest.mark.timeout(10)
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs not supported on Windows")
def test_append_rechecks_target_after_create_confirmation(tmp_path: Path, monkeypatch):
    """A FIFO created during append's second confirmation is refused."""
    target = tmp_path / "pipe"

    def create_fifo(_prompt: str) -> bool:
        os.mkfifo(target)
        return True

    monkeypatch.setattr(save_tool, "confirm", create_fifo)
    messages = list(execute_append_impl("hello\n", target))
    assert len(messages) == 1
    assert "not a regular file" in messages[0].content
