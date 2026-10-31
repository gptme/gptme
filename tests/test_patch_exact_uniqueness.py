"""Exact patch uniqueness includes occurrences that overlap each other."""

import json
from pathlib import Path

import pytest

from gptme.tools.patch import Patch, apply, execute_patch
from gptme.tools.patch_many import execute_patch_many


def block(original: str, updated: str = "changed") -> str:
    return f"<<<<<<< ORIGINAL\n{original}\n=======\n{updated}\n>>>>>>> UPDATED"


@pytest.mark.parametrize(
    ("content", "original"),
    [
        ("aaaa", "aaa"),
        ("abababa", "ababa"),
        ("雪雪雪", "雪雪"),
        ("repeat\nrepeat\nrepeat\n", "repeat\nrepeat"),
        ("aaa gap aaa", "aaa"),
    ],
)
def test_core_rejects_ambiguous_exact_matches(content: str, original: str) -> None:
    for patch_content in (False, True):
        with pytest.raises(ValueError, match="not unique.*matches") as exc:
            if patch_content:
                apply(block(original), content)
            else:
                Patch(original, "changed").apply(content)
        assert "surrounding context" in str(exc.value)


@pytest.mark.parametrize("tool", ["patch", "patch_many"])
@pytest.mark.parametrize(
    ("initial", "original"),
    [
        (b"aaaa", "aaa"),
        (b"start\nrepeat\nrepeat\nrepeat\nend\n", "repeat\nrepeat"),
        (b"start\r\nrepeat\r\nrepeat\r\nrepeat\r\nend\r\n", "repeat\nrepeat"),
    ],
)
def test_native_wrappers_preserve_ambiguous_file(
    tmp_path: Path, tool: str, initial: bytes, original: str
) -> None:
    target = tmp_path / "ambiguous.txt"
    target.write_bytes(initial)
    payload = block(original)
    if tool == "patch":
        messages = list(
            execute_patch(None, None, {"path": str(target), "patch": payload})
        )
    else:
        messages = list(
            execute_patch_many(
                None,
                None,
                {"patches": json.dumps([{"path": str(target), "patch": payload}])},
            )
        )
    response = "\n".join(message.content for message in messages)
    assert "not unique" in response
    assert "surrounding context" in response
    assert target.read_bytes() == initial


@pytest.mark.parametrize("ambiguous_first", [False, True])
def test_patch_many_overlap_aborts_all_files(
    tmp_path: Path, ambiguous_first: bool
) -> None:
    sibling, target = tmp_path / "sibling.txt", tmp_path / "ambiguous.txt"
    sibling.write_bytes(b"unique\r\n")
    target.write_bytes(b"repeat\nrepeat\nrepeat\n")
    entries = [
        {"path": str(sibling), "patch": block("unique")},
        {"path": str(target), "patch": block("repeat\nrepeat")},
    ]
    if ambiguous_first:
        entries.reverse()
    messages = list(execute_patch_many(None, None, {"patches": json.dumps(entries)}))
    response = "\n".join(message.content for message in messages)
    assert "not unique" in response
    assert "No files were written" in response
    assert sibling.read_bytes() == b"unique\r\n"
    assert target.read_bytes() == b"repeat\nrepeat\nrepeat\n"


@pytest.mark.parametrize("tool", ["patch", "patch_many"])
@pytest.mark.parametrize(
    ("initial", "payload", "expected"),
    [
        (
            "start\nrepeat\nrepeat\nend\n",
            block("repeat\nrepeat"),
            "start\nchanged\nend\n",
        ),
        (
            "repeat\nrepeat\nrepeat\nend\n",
            block("repeat\nrepeat\nend"),
            "repeat\nchanged\n",
        ),
        ("aaaa", block("aaaa"), "changed"),
        (
            "first\nsecond\n",
            block("first", "FIRST") + "\n" + block("FIRST\nsecond"),
            "changed\n",
        ),
        ("first\n  \nlast\n", block("first\n\nlast\n", "changed\n"), "changed\n"),
        # Exact matching still takes precedence over additional relaxed candidates.
        (
            "first\n\nlast\nfirst\n  \nlast\n",
            block("first\n\nlast"),
            "changed\nfirst\n  \nlast\n",
        ),
    ],
)
def test_native_unique_sequential_and_relaxed_controls(
    tmp_path: Path, tool: str, initial: str, payload: str, expected: str
) -> None:
    target = tmp_path / "valid.txt"
    target.write_text(initial, encoding="utf-8")
    if tool == "patch":
        messages = list(execute_patch(payload, [str(target)], None))
    else:
        messages = list(
            execute_patch_many(
                None,
                None,
                {"patches": json.dumps([{"path": str(target), "patch": payload}])},
            )
        )
    response = "\n".join(message.content for message in messages)
    assert "successfully" in response or "atomically" in response
    assert target.read_text(encoding="utf-8") == expected


@pytest.mark.parametrize("content", ["", "x"])
def test_empty_original_policy_unchanged(content: str) -> None:
    if content:
        with pytest.raises(ValueError, match="not unique"):
            Patch("", "changed").apply(content)
    else:
        assert Patch("", "changed").apply(content) == "changed"
    with pytest.raises(ValueError, match="not unique" if content else "did not change"):
        Patch("", "").apply(content)


def test_relaxed_matching_still_rejects_ambiguous_windows() -> None:
    with pytest.raises(ValueError, match="not unique.*relaxed whitespace"):
        Patch("first\n\nlast\n", "changed\n").apply("first\n \nlast\nfirst\n\t\nlast\n")


@pytest.mark.parametrize("tool", ["patch", "patch_many"])
def test_later_overlapping_hunk_does_not_publish_earlier_hunk(
    tmp_path: Path, tool: str
) -> None:
    target = tmp_path / "sequential.txt"
    initial = b"unique\nrepeat\nrepeat\nrepeat\n"
    target.write_bytes(initial)
    payload = block("unique") + "\n" + block("repeat\nrepeat")
    if tool == "patch":
        messages = list(execute_patch(payload, [str(target)], None))
    else:
        messages = list(
            execute_patch_many(
                None,
                None,
                {"patches": json.dumps([{"path": str(target), "patch": payload}])},
            )
        )
    assert "not unique" in "\n".join(message.content for message in messages)
    assert target.read_bytes() == initial
