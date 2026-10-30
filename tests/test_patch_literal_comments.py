"""Ellipsis comments are literal patch content, never omission wildcards."""

from pathlib import Path

import pytest

from gptme.tools.patch import Patch, apply, execute_patch_impl
from gptme.tools.patch_many import execute_patch_many_impl


def block(original: str, updated: str) -> str:
    return f"<<<<<<< ORIGINAL\n{original}\n=======\n{updated}\n>>>>>>> UPDATED"


@pytest.mark.parametrize("prefix", ["#", "//", '"'])
@pytest.mark.parametrize("indent", ["", "    ", "\t"])
@pytest.mark.parametrize("mixed", [False, True])
def test_literal_comment_writer(
    tmp_path: Path, prefix: str, indent: str, mixed: bool
) -> None:
    old = f"{indent}{prefix} ... old explanation"
    new = f"{indent}{prefix} ... new explanation"
    if mixed:
        old += "\nvalue = 1"
        new += "\nvalue = 2"
    target = tmp_path / "example.txt"
    target.write_text(old + "\n")
    messages = list(execute_patch_impl(block(old, new), target))
    assert "successfully" in messages[0].content
    assert target.read_text() == new + "\n"


@pytest.mark.parametrize("prefix", ["#", "//", '"'])
@pytest.mark.parametrize("comment", ["...", "... existing code ...", "...expected"])
def test_literal_context(prefix: str, comment: str) -> None:
    old = f"{prefix} {comment}\nvalue = 1"
    new = f"{prefix} {comment}\nvalue = 2"
    payload = block(old, new)
    assert list(Patch.from_codeblock(payload)) == [Patch(old, new)]
    assert apply(payload, old + "\n") == new + "\n"
    with pytest.raises(ValueError, match="not found"):
        apply(payload, f"{prefix} ... different actual comment\nvalue = 1\n")


def test_add_and_remove_literal_comments() -> None:
    assert apply(block("value = 1", "# ... explanation\nvalue = 1"), "value = 1") == (
        "# ... explanation\nvalue = 1"
    )
    assert (
        apply(
            block("# ... explanation\nvalue = 1", "value = 1"),
            "# ... explanation\nvalue = 1",
        )
        == "value = 1"
    )


def test_multi_hunk_literal_comments() -> None:
    payload = (
        block("# ... first", "# ... changed first")
        + "\n"
        + block("// ... second", "// ... changed second")
    )
    assert apply(payload, "# ... first\nmiddle\n// ... second\n") == (
        "# ... changed first\nmiddle\n// ... changed second\n"
    )


@pytest.mark.parametrize("stale", [False, True])
def test_patch_many_literal_comments(tmp_path: Path, stale: bool) -> None:
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    first.write_text("ordinary old\n")
    second.write_text("# ... actual\nvalue = 1\n")
    expected = "# ... expected" if stale else "# ... actual"
    messages = list(
        execute_patch_many_impl(
            [
                (first, block("ordinary old", "ordinary new")),
                (second, block(expected + "\nvalue = 1", "# ... updated\nvalue = 2")),
            ]
        )
    )
    if stale:
        assert "aborted" in messages[0].content
        assert first.read_text() == "ordinary old\n"
        assert second.read_text() == "# ... actual\nvalue = 1\n"
    else:
        assert "atomically" in messages[0].content
        assert first.read_text() == "ordinary new\n"
        assert second.read_text() == "# ... updated\nvalue = 2\n"
