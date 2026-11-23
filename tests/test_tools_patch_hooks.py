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
