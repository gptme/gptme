"""Append participates in the real file-save hook lifecycle."""

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from gptme.hooks import (
    HookRegistry,
    HookType,
    get_registry,
    register_hook,
    set_registry,
)
from gptme.message import Message
from gptme.tools import save


@pytest.fixture
def file_events() -> Generator[
    list[tuple[str, dict[str, Any], bytes | None]], None, None
]:
    previous = get_registry()
    set_registry(HookRegistry())
    events: list[tuple[str, dict[str, Any], bytes | None]] = []

    def pre(
        log: Any, workspace: Path | None, path: Path, content: str
    ) -> Generator[Message, None, None]:
        payload = {"log": log, "workspace": workspace, "path": path, "content": content}
        events.append(("pre", payload, path.read_bytes() if path.exists() else None))
        yield Message("system", "pre hook message")

    def post(
        log: Any, workspace: Path | None, path: Path, content: str, created: bool
    ) -> Generator[Message, None, None]:
        payload = {
            "log": log,
            "workspace": workspace,
            "path": path,
            "content": content,
            "created": created,
        }
        events.append(("post", payload, path.read_bytes()))
        yield Message("system", "post hook message")

    register_hook("append-test-pre", HookType.FILE_SAVE_PRE, pre, priority=10)
    register_hook("append-test-post", HookType.FILE_SAVE_POST, post)
    try:
        yield events
    finally:
        set_registry(previous)


@pytest.mark.parametrize("original", [None, b"", b"before", b"before\n", b"before\r\n"])
@pytest.mark.parametrize("operation", ["append", "save"])
def test_file_write_hook_payloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    file_events: list,
    original: bytes | None,
    operation: str,
) -> None:
    path = tmp_path / "file.txt"
    if original is not None:
        path.write_bytes(original)
    monkeypatch.setattr(save, "confirm", lambda *args, **kwargs: True)
    execute = save.execute_append if operation == "append" else save.execute_save
    messages = list(execute(None, None, {"path": str(path), "content": "added"}))

    assert [event[0] for event in file_events] == ["pre", "post"]
    pre, post = file_events
    assert pre[1] == {"log": None, "workspace": None, "path": path, "content": "added"}
    assert pre[2] == original
    expected_chunk = (
        "\nadded\n" if operation == "append" and original == b"before" else "added\n"
    )
    assert post[1] == {
        "log": None,
        "workspace": None,
        "path": path,
        "content": expected_chunk,
        "created": original is None,
    }
    newline = b"\r\n" if original == b"before\r\n" else b"\n"
    expected = expected_chunk.replace("\n", newline.decode()).encode()
    if operation == "append":
        expected = (original or b"") + expected
    assert post[2] == path.read_bytes() == expected
    assert [message.content for message in messages[:2]] == [
        "pre hook message",
        "post hook message",
    ]
    assert ("Appended to" if operation == "append" else "Saved to") in messages[
        -1
    ].content


@pytest.mark.parametrize(
    "refusal", ["file", "folder", "confirmation", "empty", "directory", "traversal"]
)
def test_append_refusal_emits_no_save_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_events: list, refusal: str
) -> None:
    path = tmp_path / "file.txt"
    content = "added"
    monkeypatch.setattr(save, "confirm", lambda *args, **kwargs: False)
    if refusal == "folder":
        path = tmp_path / "missing" / "file.txt"
    elif refusal == "confirmation":
        from gptme.hooks import ConfirmAction, ConfirmationResult

        monkeypatch.setattr(
            "gptme.hooks.get_confirmation",
            lambda **kwargs: ConfirmationResult(ConfirmAction.SKIP),
        )
        path.write_text("before\n")
    elif refusal == "empty":
        content = ""
    elif refusal == "directory":
        path.mkdir()
    elif refusal == "traversal":
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.chdir(work)
        path = Path("../escape.txt")

    messages = list(save.execute_append(content, [str(path)], None))

    assert messages
    assert not file_events
    if refusal == "confirmation":
        assert path.read_text() == "before\n"
    elif refusal == "directory":
        assert path.is_dir()
    else:
        assert not path.exists()


@pytest.mark.parametrize("original", [None, b"before\n"])
def test_append_failed_write_emits_no_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    file_events: list,
    original: bytes | None,
) -> None:
    path = tmp_path / "file.txt"
    if original is not None:
        path.write_bytes(original)
    monkeypatch.setattr(save, "confirm", lambda *args, **kwargs: True)

    def failed_open(*args: Any, **kwargs: Any) -> None:
        raise OSError("write denied")

    monkeypatch.setattr(save, "open", failed_open, raising=False)
    with pytest.raises(OSError, match="write denied"):
        list(save.execute_append_impl("added", path))

    assert [event[0] for event in file_events] == ["pre"]
    assert (path.read_bytes() if path.exists() else None) == original


@pytest.mark.timeout(10)
@pytest.mark.parametrize("replacement", ["directory", "fifo"])
def test_append_rechecks_target_after_pre_hook(
    tmp_path: Path, file_events: list, replacement: str
) -> None:
    import os

    if replacement == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs not supported on Windows")
    path = tmp_path / "file.txt"
    path.write_text("before\n")

    def replace(
        log: Any, workspace: Path | None, path: Path, content: str
    ) -> Generator[Message, None, None]:
        path.unlink()
        if replacement == "fifo":
            os.mkfifo(path)
        else:
            path.mkdir()
        yield Message("system", "replaced target")

    register_hook("replace-target", HookType.FILE_SAVE_PRE, replace)
    messages = list(save.execute_append_impl("added", path))

    assert [event[0] for event in file_events] == ["pre"]
    assert "not a regular file" in messages[-1].content


@pytest.mark.parametrize("failure", ["write", "close"])
def test_append_stream_failure_emits_no_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_events: list, failure: str
) -> None:
    path = tmp_path / "file.txt"
    path.write_text("before\n")

    class FailingStream:
        def __enter__(self) -> "FailingStream":
            return self

        def write(self, content: str) -> None:
            if failure == "write":
                raise OSError("stream write failed")

        def __exit__(self, *args: Any) -> None:
            if failure == "close":
                raise OSError("stream close failed")

    monkeypatch.setattr(
        save, "open", lambda *args, **kwargs: FailingStream(), raising=False
    )
    with pytest.raises(OSError, match=f"stream {failure} failed"):
        list(save.execute_append_impl("added", path))

    assert [event[0] for event in file_events] == ["pre"]


def test_append_relative_path_missing_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_events: list
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(save, "confirm", lambda *args, **kwargs: True)
    path = Path("nested/file.txt")

    list(save.execute_append("added", [str(path)], None))

    assert [event[0] for event in file_events] == ["pre", "post"]
    assert all(event[1]["path"] == tmp_path / path for event in file_events)
    assert file_events[0][2] is None
    assert file_events[1][1]["created"] is True
    assert path.read_bytes() == b"added\n"


def test_append_pre_created_destination_retains_initial_created_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_events: list
) -> None:
    path = tmp_path / "file.txt"
    monkeypatch.setattr(save, "confirm", lambda *args, **kwargs: True)

    def create(
        log: Any, workspace: Path | None, path: Path, content: str
    ) -> Generator[Message, None, None]:
        path.write_bytes(b"from hook")
        yield Message("system", "created by pre")

    register_hook("create-target", HookType.FILE_SAVE_PRE, create)
    list(save.execute_append_impl("added", path))

    assert [event[0] for event in file_events] == ["pre", "post"]
    assert file_events[0][2] is None
    assert file_events[1][1]["created"] is True
    assert file_events[1][1]["content"] == "\nadded\n"
    assert path.read_bytes() == b"from hook\nadded\n"
