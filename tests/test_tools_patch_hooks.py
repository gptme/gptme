"""patch and patch_many fire file.patch.pre/post (gptme/gptme#5091).

Both were declared hook types with no call site, so a linter registered for
them never ran. They take the same arguments as file.save.pre/post.
"""

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
from gptme.tools.patch import DIVIDER, ORIGINAL, UPDATED, execute_patch_impl
from gptme.tools.patch_many import execute_patch_many_impl

Event = tuple[str, Path, str, bool | None, str]


def _hunk(old: str, new: str) -> str:
    return f"{ORIGINAL}{old}{DIVIDER}{new}{UPDATED}"


@pytest.fixture
def patch_events() -> Generator[list[Event], None, None]:
    previous = get_registry()
    set_registry(HookRegistry())
    events: list[Event] = []

    def pre(
        log: Any, workspace: Path | None, path: Path, content: str
    ) -> Generator[Message, None, None]:
        # Record what is on disk when PRE runs: it must still be the original.
        events.append(("pre", path, content, None, path.read_text()))
        yield Message("system", f"pre {path.name}")

    def post(
        log: Any, workspace: Path | None, path: Path, content: str, created: bool
    ) -> Generator[Message, None, None]:
        events.append(("post", path, content, created, path.read_text()))
        yield Message("system", f"post {path.name}")

    register_hook("patch-test-pre", HookType.FILE_PATCH_PRE, pre)
    register_hook("patch-test-post", HookType.FILE_PATCH_POST, post)
    try:
        yield events
    finally:
        set_registry(previous)


def test_patch_fires_file_patch_hooks(tmp_path: Path, patch_events: list[Event]):
    path = tmp_path / "a.txt"
    path.write_text("hello\n")

    msgs = list(execute_patch_impl(_hunk("hello", "world"), path))

    assert [(kind, p, c, created) for kind, p, c, created, _ in patch_events] == [
        ("pre", path.resolve(), "world\n", None),
        ("post", path.resolve(), "world\n", False),
    ]
    # PRE runs before the write, POST after it.
    assert patch_events[0][4] == "hello\n"
    assert patch_events[1][4] == "world\n"
    # Hook messages reach the conversation, before the tool's own result.
    contents = [m.content for m in msgs]
    assert contents[:2] == ["pre a.txt", "post a.txt"]
    assert "Patch successfully applied" in contents[2]


def test_failed_patch_fires_no_hooks(tmp_path: Path, patch_events: list[Event]):
    path = tmp_path / "a.txt"
    path.write_text("hello\n")

    with pytest.raises(ValueError, match="Patch failed"):
        list(execute_patch_impl(_hunk("missing", "world"), path))

    assert patch_events == []
    assert path.read_text() == "hello\n"


def test_patch_many_fires_hooks_per_file(tmp_path: Path, patch_events: list[Event]):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("one\n")
    b.write_text("two\n")

    msgs = list(
        execute_patch_many_impl([(a, _hunk("one", "ONE")), (b, _hunk("two", "TWO"))])
    )

    assert [(kind, p.name, c, created) for kind, p, c, created, _ in patch_events] == [
        ("pre", "a.txt", "ONE\n", None),
        ("pre", "b.txt", "TWO\n", None),
        ("post", "a.txt", "ONE\n", False),
        ("post", "b.txt", "TWO\n", False),
    ]
    # Every PRE sees the untouched originals; every POST sees the batch on disk.
    assert [e[4] for e in patch_events] == ["one\n", "two\n", "ONE\n", "TWO\n"]
    assert "Applied 2 hunk(s) atomically to 2 file(s)" in msgs[-1].content


def test_aborted_patch_many_fires_no_hooks(tmp_path: Path, patch_events: list[Event]):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("one\n")
    b.write_text("two\n")

    msgs = list(
        execute_patch_many_impl(
            [(a, _hunk("one", "ONE")), (b, _hunk("missing", "TWO"))]
        )
    )

    assert patch_events == []
    assert "No files were written" in msgs[-1].content
    assert a.read_text() == "one\n"


@pytest.mark.parametrize("tool", ["patch", "patch_many"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("replacement_newline", ["\n", "\r\n", "\r"])
def test_patch_hooks_normalize_replacement_line_endings(
    tmp_path: Path,
    patch_events: list[Event],
    tool: str,
    newline: str,
    replacement_newline: str,
) -> None:
    path = tmp_path / "a.txt"
    path.write_bytes(f"hello{newline}".encode())
    patch = _hunk("hello", f"new one{replacement_newline}new two")

    if tool == "patch":
        list(execute_patch_impl(patch, path))
    else:
        list(execute_patch_many_impl([(path, patch)]))

    normalized = "new one\nnew two\n"
    assert [(e[0], e[2]) for e in patch_events] == [
        ("pre", normalized),
        ("post", normalized),
    ]
    assert patch_events[1][4] == normalized
    assert path.read_bytes() == normalized.replace("\n", newline).encode()


@pytest.mark.parametrize("tool", ["patch", "patch_many"])
def test_patch_write_failure_fires_pre_but_not_post(
    tmp_path: Path,
    patch_events: list[Event],
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
) -> None:
    import os

    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("one\n")
    b.write_text("two\n")
    replace = os.replace
    failed = False

    def fail_publication(src: Any, dst: Any) -> None:
        nonlocal failed
        if Path(dst) == b and not failed:
            failed = True
            raise OSError("injected publication failure")
        replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_publication)

    if tool == "patch":
        with pytest.raises(OSError, match="injected publication failure"):
            list(execute_patch_impl(_hunk("two", "TWO"), b))
        expected = [("pre", "b.txt", "TWO\n")]
    else:
        msgs = list(
            execute_patch_many_impl(
                [(a, _hunk("one", "ONE")), (b, _hunk("two", "TWO"))]
            )
        )
        assert "injected publication failure" in msgs[-1].content
        assert "Original contents restored" in msgs[-1].content
        expected = [("pre", "a.txt", "ONE\n"), ("pre", "b.txt", "TWO\n")]

    assert failed
    assert [(e[0], e[1].name, e[2]) for e in patch_events] == expected
    assert a.read_text() == "one\n"
    assert b.read_text() == "two\n"
    assert set(tmp_path.iterdir()) == {a, b}


def test_patch_many_staging_failure_fires_pre_but_not_post(
    tmp_path: Path,
    patch_events: list[Event],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "a.txt"
    path.write_text("hello\n")

    def fail_staging(*args: Any, **kwargs: Any) -> None:
        raise OSError("injected staging failure")

    monkeypatch.setattr("gptme.tools.patch_many.tempfile.mkstemp", fail_staging)
    msgs = list(execute_patch_many_impl([(path, _hunk("hello", "world"))]))

    assert "injected staging failure" in msgs[-1].content
    assert [(e[0], e[2]) for e in patch_events] == [("pre", "world\n")]
    assert path.read_text() == "hello\n"
    assert set(tmp_path.iterdir()) == {path}
