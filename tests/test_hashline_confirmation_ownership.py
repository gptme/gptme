"""Run the actual writer across the user-confirmation boundary."""

from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.hooks.confirm import ConfirmationResult
from gptme.tools._hashline_snapshot import (
    clear,
    compute_tag,
    lookup_snapshot,
    store_snapshot,
)
from gptme.tools.hashline_edit import execute_hashline_edit


@pytest.mark.parametrize(
    "recovered", [False, True], ids=["ordinary", "merge-recovered"]
)
@pytest.mark.parametrize("action", ["confirm", "edit", "skip"])
@pytest.mark.parametrize("change", ["unchanged", "text", "deleted", "invalid-utf8"])
def test_confirmation_preserves_target_ownership(tmp_path, recovered, action, change):
    clear()
    target = tmp_path / "target.txt"
    original = "alpha\nbeta\ngamma\ndelta\n"
    target.write_text(original)
    tag = store_snapshot(str(target.resolve()), original)
    live = original + ("external-before-confirmation\n" if recovered else "")
    target.write_text(live)
    external = live.replace("beta", "EXTERNAL")
    edited = live.replace("alpha", "USER_ALPHA")
    calls = []

    def confirm(**kwargs):
        calls.append(kwargs)
        if change == "text":
            target.write_text(external)
        elif change == "deleted":
            target.unlink()
        elif change == "invalid-utf8":
            target.write_bytes(b"\xffexternal bytes\n")
        if action == "edit":
            return ConfirmationResult.edit(edited)
        if action == "skip":
            return ConfirmationResult.skip()
        return ConfirmationResult.confirm()

    with patch("gptme.hooks.get_confirmation", side_effect=confirm):
        messages = [
            message.content
            for message in execute_hashline_edit(
                f"[{target}#{tag}]\nPUT 1.=1:\n+ALPHA", [str(target)], None
            )
        ]
    assert len(calls) == 1
    assert calls[0]["preview"] == live.replace("alpha", "ALPHA")
    applied = any("hashline_edit applied" in message for message in messages)
    if change == "unchanged" and action != "skip":
        assert applied, messages
        expected = edited if action == "edit" else live.replace("alpha", "ALPHA")
        assert target.read_text() == expected
        assert lookup_snapshot(str(target.resolve()), compute_tag(expected)) == (
            True,
            expected,
        )
    else:
        assert not applied, messages
        if change == "deleted":
            assert not target.exists()
        elif change == "invalid-utf8":
            assert target.read_bytes() == b"\xffexternal bytes\n"
        else:
            assert target.read_text() == (external if change == "text" else live)
        # An aborted write must not manufacture a new snapshot/tag.
        assert lookup_snapshot(str(target.resolve()), tag) == (True, original)
        if action != "skip":
            assert any(
                "re-read" in message or "fresh snapshot" in message
                for message in messages
            )
    assert set(tmp_path.iterdir()) == ({target} if target.exists() else set())


@pytest.mark.parametrize("recovered", [False, True])
@pytest.mark.parametrize("action", ["confirm", "edit"])
def test_post_confirmation_permission_failure_aborts(tmp_path, recovered, action):
    clear()
    target = tmp_path / "target.txt"
    original = "alpha\nbeta\ngamma\ndelta\n"
    target.write_text(original)
    tag = store_snapshot(str(target.resolve()), original)
    live = original + ("external-before-confirmation\n" if recovered else "")
    target.write_text(live)
    read_text = Path.read_text
    confirmed = False

    def confirm(**kwargs):
        nonlocal confirmed
        confirmed = True
        if action == "edit":
            return ConfirmationResult.edit("USER_EDIT\n")
        return ConfirmationResult.confirm()

    def guarded_read(path, *args, **kwargs):
        if confirmed and path == target:
            raise PermissionError("target became unreadable")
        return read_text(path, *args, **kwargs)

    with (
        patch("gptme.hooks.get_confirmation", side_effect=confirm),
        patch.object(Path, "read_text", guarded_read),
    ):
        messages = [
            message.content
            for message in execute_hashline_edit(
                f"[{target}#{tag}]\nPUT 1.=1:\n+ALPHA", [str(target)], None
            )
        ]
    assert confirmed
    assert target.read_text() == live
    assert any("cannot re-read" in message for message in messages), messages
    assert not any("applied" in message for message in messages)
    assert lookup_snapshot(str(target.resolve()), tag) == (True, original)
    assert set(tmp_path.iterdir()) == {target}
