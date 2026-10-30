"""Save approval applies only to the destination shown in its preview."""

from pathlib import Path

import pytest

from gptme.hooks import ConfirmationResult
from gptme.tools.save import execute_save


def test_save_preserves_confirmation_time_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "file.txt"
    target.write_text("BASELINE\n")

    def confirm(**kwargs: object) -> ConfirmationResult:
        assert "BASELINE" in str(kwargs["preview"])
        target.write_text("CONCURRENT\n")
        return ConfirmationResult.confirm()

    monkeypatch.setattr("gptme.hooks.get_confirmation", confirm)
    messages = list(
        execute_save(None, None, {"path": str(target), "content": "REPLACEMENT\n"})
    )
    assert target.read_text() == "CONCURRENT\n"
    assert any("changed" in message.content.lower() for message in messages)
    assert not any("Saved to" in message.content for message in messages)


@pytest.mark.parametrize(
    "mutation", ["delete", "appear", "replace", "bytes", "line_endings"]
)
@pytest.mark.parametrize("native", [True, False])
def test_save_rejects_changed_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str, native: bool
) -> None:
    target = tmp_path / "file.txt"
    if mutation != "appear":
        target.write_bytes(
            b"BASELINE\r\n" if mutation == "line_endings" else b"BASELINE\n"
        )

    def confirm(**kwargs: object) -> ConfirmationResult:
        if mutation == "delete":
            target.unlink()
        elif mutation == "replace":
            replacement = tmp_path / "replacement.txt"
            replacement.write_bytes(target.read_bytes())
            replacement.replace(target)
        elif mutation == "bytes":
            target.write_bytes(b"\xff\xfe\n")
        elif mutation == "line_endings":
            target.write_bytes(b"BASELINE\n")
        else:
            target.write_bytes(b"CONCURRENT\n")
        return ConfirmationResult.confirm()

    monkeypatch.setattr("gptme.hooks.get_confirmation", confirm)
    if native:
        messages = list(
            execute_save(None, None, {"path": str(target), "content": "REPLACEMENT\n"})
        )
    else:
        messages = list(execute_save("REPLACEMENT\n", [str(target)], None))
    assert (
        not target.exists()
        if mutation == "delete"
        else target.read_bytes() != b"REPLACEMENT\n"
    )
    assert any("changed" in message.content.lower() for message in messages)


@pytest.mark.parametrize("retarget", [False, True])
def test_save_rejects_symlink_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requires_symlinks: None,
    retarget: bool,
) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("BASELINE\n")
    second.write_text("BASELINE\n")
    target = tmp_path / "link.txt"
    target.symlink_to(first)

    def confirm(**kwargs: object) -> ConfirmationResult:
        if retarget:
            target.unlink()
            target.symlink_to(second)
        else:
            first.write_text("CONCURRENT\n")
        return ConfirmationResult.confirm()

    monkeypatch.setattr("gptme.hooks.get_confirmation", confirm)
    messages = list(execute_save("REPLACEMENT\n", [str(target)], None))
    assert first.read_text() == ("BASELINE\n" if retarget else "CONCURRENT\n")
    assert second.read_text() == "BASELINE\n"
    assert target.is_symlink()
    assert any("changed" in message.content.lower() for message in messages)


@pytest.mark.parametrize("mutate_round", [0, 1, 2])
def test_save_edited_content_keeps_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate_round: int
) -> None:
    from gptme.hooks import ConfirmAction

    target = tmp_path / "file.txt"
    target.write_text("BASELINE\n")
    calls = 0

    def confirm(**kwargs: object) -> ConfirmationResult:
        nonlocal calls
        calls += 1
        assert "BASELINE" in str(kwargs["preview"])
        if calls == mutate_round:
            target.write_text("CONCURRENT\n")
        if calls == 1:
            return ConfirmationResult(
                action=ConfirmAction.EDIT, edited_content="EDITED\n"
            )
        assert "EDITED" in str(kwargs["preview"])
        return ConfirmationResult.confirm()

    monkeypatch.setattr("gptme.hooks.get_confirmation", confirm)
    messages = list(execute_save("REPLACEMENT\n", [str(target)], None))
    assert target.read_text() == ("CONCURRENT\n" if mutate_round else "EDITED\n")
    assert calls == (1 if mutate_round == 1 else 2)
    assert any("changed" in message.content.lower() for message in messages) == bool(
        mutate_round
    )


@pytest.mark.parametrize(
    "kind", ["skip", "new", "unchanged", "symlink", "binary", "crlf"]
)
def test_save_baseline_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, requires_symlinks: None, kind: str
) -> None:
    from gptme.hooks import ConfirmAction

    target = tmp_path / "file.txt"
    if kind == "symlink":
        real = tmp_path / "real.txt"
        real.write_text("BASELINE\n")
        target.symlink_to(real)
    elif kind != "new":
        target.write_bytes(
            b"\xff\xfe"
            if kind == "binary"
            else b"BASELINE\r\n"
            if kind == "crlf"
            else b"BASELINE\n"
        )

    def confirm(**kwargs: object) -> ConfirmationResult:
        if kind == "skip":
            target.write_text("CONCURRENT\n")
            return ConfirmationResult(action=ConfirmAction.SKIP)
        return ConfirmationResult.confirm()

    monkeypatch.setattr("gptme.hooks.get_confirmation", confirm)
    messages = list(execute_save("REPLACEMENT", [str(target)], None))
    expected = (
        b"CONCURRENT\n"
        if kind == "skip"
        else b"REPLACEMENT\r\n"
        if kind == "crlf"
        else b"REPLACEMENT\n"
    )
    assert target.read_bytes() == expected
    assert any("Saved to" in message.content for message in messages) == (
        kind != "skip"
    )
    assert target.is_symlink() == (kind == "symlink")


@pytest.mark.parametrize("boundary", ["pre_hook", "yielded_hook", "mkdir_confirm"])
def test_save_revalidates_after_interactive_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    import gptme.hooks as hooks
    from gptme.hooks import HookType
    from gptme.message import Message
    from gptme.tools import save

    target = (
        tmp_path / "missing" / "file.txt"
        if boundary == "mkdir_confirm"
        else tmp_path / "file.txt"
    )
    if boundary != "mkdir_confirm":
        target.write_text("BASELINE\n")
    monkeypatch.setattr(
        hooks, "get_confirmation", lambda **kwargs: ConfirmationResult.confirm()
    )
    original_trigger = hooks.trigger_hook

    def trigger(hook_type: HookType, *args: object, **kwargs: object):
        if hook_type == HookType.FILE_SAVE_PRE:
            if boundary == "pre_hook":
                target.write_text("CONCURRENT\n")
            elif boundary == "yielded_hook":
                yield Message("system", "hook paused")
            return
        yield from original_trigger(hook_type, *args, **kwargs)

    monkeypatch.setattr(hooks, "trigger_hook", trigger)

    def mkdir_confirm(prompt: str) -> bool:
        target.parent.mkdir()
        target.write_text("CONCURRENT\n")
        return True

    monkeypatch.setattr(save, "confirm", mkdir_confirm)
    operation = execute_save("REPLACEMENT\n", [str(target)], None)
    messages = []
    if boundary == "yielded_hook":
        messages.append(next(operation))
        target.write_text("CONCURRENT\n")
    messages.extend(operation)
    assert target.read_text() == "CONCURRENT\n"
    assert any("changed" in message.content.lower() for message in messages)
    assert not any("Saved to" in message.content for message in messages)
