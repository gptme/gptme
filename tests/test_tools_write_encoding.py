"""Single-file writers must reject invalid UTF-8 before truncating a file."""

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from gptme.hooks import HookType
from gptme.tools.patch import execute_patch_impl
from gptme.tools.save import execute_append_impl, execute_save_impl

if TYPE_CHECKING:
    from gptme.message import Message


def _patch_content(replacement: str) -> str:
    return "<<<<<<< ORIGINAL\noriginal\n=======\n" + replacement + "\n>>>>>>> UPDATED"


@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"], ids=["high", "low"])
@pytest.mark.parametrize("operation", ["save", "patch"])
@pytest.mark.parametrize("symlink", [False, True], ids=["file", "symlink"])
def test_unencodable_write_preserves_existing_file(
    tmp_path: Path,
    surrogate: str,
    operation: str,
    symlink: bool,
    request: pytest.FixtureRequest,
) -> None:
    target = tmp_path / "original.txt"
    original = b"original\r\nuntouched\r\n"
    target.write_bytes(original)
    target.chmod(0o640)
    mode = target.stat().st_mode
    path = target
    if symlink:
        request.getfixturevalue("requires_symlinks")
        path = tmp_path / "link.txt"
        path.symlink_to(target)
    messages: list[Message] = []
    handler = execute_save_impl if operation == "save" else execute_patch_impl
    content = "replacement" + surrogate
    if operation == "patch":
        content = _patch_content(content)

    with (
        patch("gptme.hooks.trigger_hook", return_value=[]) as hook,
        pytest.raises(ValueError, match="UTF-8") as error,
    ):
        messages.extend(handler(content, path))

    assert target.read_bytes() == original
    assert target.stat().st_mode == mode
    if symlink:
        assert path.is_symlink()
        assert path.readlink() == target
    assert "UTF-8" in str(error.value)
    assert not messages
    assert not any(
        call.args[0] == HookType.FILE_SAVE_POST for call in hook.call_args_list
    )


@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"], ids=["high", "low"])
def test_unencodable_save_does_not_create_file(tmp_path: Path, surrogate: str) -> None:
    path = tmp_path / "new.txt"
    messages: list[Message] = []
    with (
        patch("gptme.hooks.trigger_hook", return_value=[]) as hook,
        pytest.raises(ValueError, match="UTF-8") as error,
    ):
        messages.extend(execute_save_impl("replacement" + surrogate, path))

    assert not path.exists()
    assert "UTF-8" in str(error.value)
    assert not messages
    assert not any(
        call.args[0] == HookType.FILE_SAVE_POST for call in hook.call_args_list
    )


@pytest.mark.parametrize("operation", ["save", "patch"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("replacement", ["ASCII", "café Ω", "astral 🚀"])
def test_valid_unicode_write_preserves_newlines_and_symlink(
    tmp_path: Path, operation: str, newline: str, replacement: str, requires_symlinks
) -> None:
    target = tmp_path / "original.txt"
    target.write_bytes(("original" + newline).encode("utf-8"))
    target.chmod(0o640)
    mode = target.stat().st_mode
    path = tmp_path / "link.txt"
    path.symlink_to(target)
    content = replacement + "\r\nsecond\r\n"
    handler = execute_save_impl
    if operation == "patch":
        content = _patch_content(content.rstrip("\r\n"))
        handler = execute_patch_impl

    with patch("gptme.hooks.trigger_hook", return_value=[]):
        messages = list(handler(content, path))

    assert target.read_bytes() == (replacement + newline + "second" + newline).encode(
        "utf-8"
    )
    assert target.stat().st_mode == mode
    assert path.is_symlink()
    assert path.readlink() == target
    assert len(messages) == 1
    assert (
        "Saved to" in messages[0].content
        or "successfully applied" in messages[0].content
    )


@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"], ids=["high", "low"])
def test_unencodable_append_preserves_existing_bytes(
    tmp_path: Path, surrogate: str
) -> None:
    """Control: append's encoding failure already leaves existing bytes intact."""
    path = tmp_path / "original.txt"
    original = b"original\r\n"
    path.write_bytes(original)
    with pytest.raises(UnicodeEncodeError):
        list(execute_append_impl("replacement" + surrogate, path))
    assert path.read_bytes() == original
