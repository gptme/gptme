"""Native patch_many confirmation must preserve literal patch content."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from gptme.hooks import ConfirmationResult
from gptme.message import Message
from gptme.tools.patch import DIVIDER, ORIGINAL, UPDATED
from gptme.tools.patch_many import execute_patch_many, execute_patch_many_impl


def _patch(original: str, updated: str) -> str:
    return f"{ORIGINAL}{original}{DIVIDER}{updated}{UPDATED}"


def _payload(entries: list[tuple[Path, str]]) -> str:
    return "\n\n".join(f"=== PATH: {path} ===\n{patch}" for path, patch in entries)


def _execute(entries: list[tuple[Path, str]], form: str) -> list[Message]:
    if form == "kwargs":
        return list(
            execute_patch_many(
                None,
                None,
                {
                    "patches": json.dumps(
                        [{"path": str(p), "patch": s} for p, s in entries]
                    )
                },
            )
        )
    if form == "simple":
        return list(
            execute_patch_many(
                "\n\n".join(patch for _, patch in entries),
                [str(path) for path, _ in entries],
                None,
            )
        )
    return list(execute_patch_many(_payload(entries), None, None))


@pytest.fixture
def decisions(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[list[ConfirmationResult]], list[str]]:
    """Replace only the user decision; run the real confirmation wrapper."""

    def install(results: list[ConfirmationResult]) -> list[str]:
        previews: list[str] = []
        responses = iter(results)

        def confirm(*, preview: str, **_kwargs: object) -> ConfirmationResult:
            previews.append(preview)
            return next(responses)

        monkeypatch.setattr("gptme.hooks.get_confirmation", confirm)
        return previews

    return install


@pytest.mark.parametrize("form", ["kwargs", "simple", "sections"])
@pytest.mark.parametrize("side", ["original", "updated", "both"])
def test_literal_headers_survive_native_confirmation(
    tmp_path: Path,
    decisions: Callable[[list[ConfirmationResult]], list[str]],
    form: str,
    side: str,
) -> None:
    target = tmp_path / "target.txt"
    decoy = tmp_path / "not-a-target.txt"
    decoy.write_text("must remain unchanged\n")
    literal = f"=== PATH: {decoy} ==="
    original = f"before\n{literal}\nafter" if side != "updated" else "old"
    updated = f"new before\n{literal}\nnew after" if side != "original" else "new"
    target.write_text(original + "\n")
    previews = decisions([ConfirmationResult.confirm()])

    messages = _execute([(target, _patch(original, updated))], form)

    assert target.read_text() == updated + "\n", messages
    assert decoy.read_text() == "must remain unchanged\n"
    assert len(previews) == 1
    assert literal in previews[0]


@pytest.mark.parametrize("form", ["kwargs", "sections"])
def test_multiple_hunks_files_and_repeated_paths_match_direct_execution(
    tmp_path: Path,
    decisions: Callable[[list[ConfirmationResult]], list[str]],
    form: str,
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    literal = "=== PATH: ordinary-documentation.txt ==="
    originals = {first: "alpha\nbeta\n", second: "gamma\n"}
    for path, text in originals.items():
        path.write_text(text)
    entries = [
        (first, _patch("alpha", literal) + "\n\n" + _patch("beta", "BETA")),
        (second, _patch("gamma", literal)),
        (first, _patch(literal, literal + "\nfinished")),
    ]
    list(execute_patch_many_impl(entries))
    expected = {path: path.read_bytes() for path in originals}
    assert expected[first] == f"{literal}\nfinished\nBETA\n".encode()
    assert expected[second] == f"{literal}\n".encode()
    for path, text in originals.items():
        path.write_text(text)
    decisions([ConfirmationResult.confirm()])

    messages = _execute(entries, form)

    assert {path: path.read_bytes() for path in originals} == expected, messages


@pytest.mark.parametrize("approve_edit", [True, False])
def test_edited_literal_header_payload_requires_reconfirmation(
    tmp_path: Path,
    decisions: Callable[[list[ConfirmationResult]], list[str]],
    approve_edit: bool,
) -> None:
    target = tmp_path / "target"
    target.write_text("old\n")
    updated = "=== PATH: just text.txt ===\nuser edit"
    edited = _payload([(target, _patch("old", updated))])
    previews = decisions(
        [
            ConfirmationResult.edit(edited),
            ConfirmationResult.confirm() if approve_edit else ConfirmationResult.skip(),
        ]
    )

    messages = _execute([(target, _patch("old", "proposed"))], "kwargs")

    assert previews == [_payload([(target, _patch("old", "proposed"))]), edited]
    assert target.read_text() == (updated + "\n" if approve_edit else "old\n"), messages
    assert (
        any("content was edited by user" in m.content for m in messages) == approve_edit
    )


def test_cancelled_payload_preserves_all_files(
    tmp_path: Path,
    decisions: Callable[[list[ConfirmationResult]], list[str]],
) -> None:
    target = tmp_path / "target"
    target.write_text("old\n")
    decisions([ConfirmationResult.skip("Cancelled")])
    messages = _execute(
        [(target, _patch("old", "=== PATH: literal.txt ==="))], "kwargs"
    )
    assert target.read_text() == "old\n"
    assert [m.content for m in messages] == ["Cancelled"]


@pytest.mark.parametrize(
    "malformation",
    [
        "missing-header",
        "unterminated-hunk",
        "bad-later-hunk",
        "empty-section",
        "trailing-garbage",
        "leading-garbage",
    ],
)
def test_malformed_edited_confirmation_writes_nothing(
    tmp_path: Path,
    decisions: Callable[[list[ConfirmationResult]], list[str]],
    malformation: str,
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_text("one\n")
    second.write_text("two\n")
    good = _payload([(first, _patch("one", "ONE"))])
    if malformation == "missing-header":
        edited = _patch("one", "ONE")
    elif malformation == "unterminated-hunk":
        edited = (
            good + f"\n\n=== PATH: {second} ===\n<<<<<<< ORIGINAL\ntwo\n=======\nTWO"
        )
    elif malformation == "empty-section":
        edited = good + f"\n\n=== PATH: {second} ===\n"
    elif malformation == "trailing-garbage":
        edited = good + "\nnot a patch"
    elif malformation == "leading-garbage":
        edited = "not a patch\n" + good
    else:
        edited = good + f"\n\n=== PATH: {second} ===\nnot a conflict-marker patch"
    decisions([ConfirmationResult.edit(edited), ConfirmationResult.confirm()])

    messages = _execute([(first, _patch("one", "original proposal"))], "kwargs")

    assert first.read_text() == "one\n", messages
    assert second.read_text() == "two\n", messages
    assert any("Error" in m.content or "aborted" in m.content for m in messages)


@pytest.mark.parametrize("form", ["simple", "sections"])
def test_existing_markdown_formats_still_apply(
    tmp_path: Path,
    decisions: Callable[[list[ConfirmationResult]], list[str]],
    form: str,
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_text("one\n")
    second.write_text("two\n")
    decisions([ConfirmationResult.confirm()])
    messages = _execute(
        [(first, _patch("one", "ONE")), (second, _patch("two", "TWO"))], form
    )
    assert first.read_text() == "ONE\n", messages
    assert second.read_text() == "TWO\n", messages


@pytest.mark.parametrize("form", ["kwargs", "simple", "sections"])
@pytest.mark.parametrize("first_line", ["ordinary text", "======="])
def test_original_closing_marker_does_not_end_hunk_before_divider(
    tmp_path: Path,
    decisions: Callable[[list[ConfirmationResult]], list[str]],
    form: str,
    first_line: str,
) -> None:
    target = tmp_path / "target"
    # A divider at the start of ORIGINAL content lacks the preceding newline
    # required by Patch._from_codeblock, so it is literal content too.
    original = f"{first_line}\n>>>>>>> UPDATED\n=== PATH: literal.txt ===\ntail"
    target.write_text(original + "\n")
    decisions([ConfirmationResult.confirm()])

    messages = _execute([(target, _patch(original, "new"))], form)

    assert target.read_text() == "new\n", messages


@pytest.mark.parametrize("form", ["kwargs", "simple", "sections"])
@pytest.mark.parametrize("empty_side", ["original", "updated", "compact-updated"])
def test_empty_patch_content_round_trip(
    tmp_path: Path,
    decisions: Callable[[list[ConfirmationResult]], list[str]],
    form: str,
    empty_side: str,
) -> None:
    target = tmp_path / "target"
    original, updated = ("", "new") if empty_side == "original" else ("old", "")
    target.write_text(original)
    patch = _patch(original, updated)
    if empty_side == "compact-updated":
        patch = f"{ORIGINAL}{original}{DIVIDER}{UPDATED[1:]}"
    list(execute_patch_many_impl([(target, patch)]))
    expected = target.read_bytes()
    assert expected == updated.encode()
    target.write_text(original)
    decisions([ConfirmationResult.confirm()])

    messages = _execute([(target, patch)], form)

    assert target.read_bytes() == expected, messages
