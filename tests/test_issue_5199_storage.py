"""Regression tests for storage robustness defects from gptme/gptme#5199."""

from __future__ import annotations

import json
import stat
import sys
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from gptme.logmanager import LogManager, rename_conversation
from gptme.logmanager.conv_checkpoints import (
    list_conv_checkpoints,
    resolve_conv_checkpoint,
    save_conv_checkpoint,
)
from gptme.logmanager.eventlog import (
    CHECKPOINT_INTERVAL,
    EVENT_LOG_NAME,
    append_next_event,
    build_message_append_event,
    read_events,
    recover_messages,
    sequence_number,
    write_checkpoint,
)
from gptme.message import Message

if TYPE_CHECKING:
    from pathlib import Path

# ── 1. event log rows of the wrong shape ─────────────────────────────


@pytest.mark.parametrize(
    "bad_row",
    [
        "[]",
        "1",
        '"x"',
        "null",
        '{"type": "undo"}',
        '{"seq": true}',
        '{"seq": "3"}',
        # integer seq, but the payload recovery indexes is missing or wrong
        '{"seq": 2, "type": "message_append"}',
        '{"seq": 2, "type": "checkpoint"}',
        '{"seq": 2, "type": "message_edit", "payload": []}',
        '{"seq": 2, "type": "undo", "payload": []}',
        # explicit null differs from a missing payload: recovery calls .get on it
        '{"seq": 2, "type": "undo", "payload": null}',
    ],
)
def test_wrong_shape_event_rows_are_skipped(tmp_path: Path, bad_row: str) -> None:
    msg = Message("user", "hello")
    append_next_event(tmp_path, lambda seq: build_message_append_event(seq, msg))
    with open(tmp_path / EVENT_LOG_NAME, "a", encoding="utf-8") as f:
        f.write(bad_row + "\n")

    assert [e["seq"] for e in read_events(tmp_path)] == [1]
    assert sequence_number(tmp_path) == 2
    event = append_next_event(
        tmp_path, lambda seq: build_message_append_event(seq, msg)
    )
    assert event["seq"] == 2
    recovered = recover_messages(tmp_path)
    assert recovered is not None
    assert [m["content"] for m in recovered] == ["hello", "hello"]
    checkpoint = write_checkpoint(tmp_path, recovered)
    assert checkpoint["seq"] == 3


def test_undo_without_payload_key_still_undoes_one(tmp_path: Path) -> None:
    a, b = Message("user", "a"), Message("user", "b")
    append_next_event(tmp_path, lambda seq: build_message_append_event(seq, a))
    append_next_event(tmp_path, lambda seq: build_message_append_event(seq, b))
    with open(tmp_path / EVENT_LOG_NAME, "a", encoding="utf-8") as f:
        f.write('{"seq": 3, "type": "undo"}\n')

    recovered = recover_messages(tmp_path)
    assert recovered is not None
    assert [m["content"] for m in recovered] == ["a"]


def test_checkpoint_due_append_survives_seqless_row(tmp_path: Path) -> None:
    manager = LogManager(logdir=tmp_path, lock=False)
    manager.append(Message("user", "first"))
    with open(tmp_path / EVENT_LOG_NAME, "a", encoding="utf-8") as f:
        f.write('{"type": "message_append"}\n')
    # Appends through the next checkpoint must not raise.
    for i in range(CHECKPOINT_INTERVAL + 1):
        manager.append(Message("user", f"m{i}"))
    recovered = recover_messages(tmp_path)
    assert recovered is not None
    assert len(recovered) == CHECKPOINT_INTERVAL + 2


# ── 2. conv-checkpoints.jsonl damage ─────────────────────────────────


def test_conv_checkpoint_append_after_torn_tail(tmp_path: Path) -> None:
    save_conv_checkpoint(tmp_path, 1, "a")
    with open(tmp_path / "conv-checkpoints.jsonl", "a", encoding="utf-8") as f:
        f.write('{"index": 2, "lab')  # torn write, no newline
    save_conv_checkpoint(tmp_path, 3, "b")
    assert [c.label for c in list_conv_checkpoints(tmp_path)] == ["a", "b"]
    assert resolve_conv_checkpoint(tmp_path, "b").index == 3


@pytest.mark.parametrize(
    "bad",
    [
        b"[1, 2]\n",
        b"\xff\n",
        b"null\n",
        b'{"index": "x", "label": "c", "timestamp": "t"}\n',
        b'{"index": true, "label": "c", "timestamp": "t"}\n',
    ],
)
def test_conv_checkpoint_bad_rows_are_skipped(tmp_path: Path, bad: bytes) -> None:
    save_conv_checkpoint(tmp_path, 1, "a")
    with open(tmp_path / "conv-checkpoints.jsonl", "ab") as f:
        f.write(bad)
    save_conv_checkpoint(tmp_path, 2, "b")
    assert [c.label for c in list_conv_checkpoints(tmp_path)] == ["a", "b"]


# ── 5. /backtrack --5 ────────────────────────────────────────────────


@pytest.mark.parametrize("target", ["--5", "-", "5-", "-5-"])
def test_resolve_malformed_numeric_target_raises_keyerror(
    tmp_path: Path, target: str
) -> None:
    with pytest.raises(KeyError):
        resolve_conv_checkpoint(tmp_path, target)


@pytest.mark.parametrize(("target", "index"), [("5", 5), ("-5", -5), ("0", 0)])
def test_resolve_numeric_target(tmp_path: Path, target: str, index: int) -> None:
    assert resolve_conv_checkpoint(tmp_path, target).index == index


# ── 3. rename_conversation is atomic ─────────────────────────────────


def test_rename_failure_mid_dump_keeps_config(tmp_path: Path) -> None:
    conv_dir = tmp_path / "conv"
    conv_dir.mkdir()
    (conv_dir / "conversation.jsonl").write_text("")
    original = '[chat]\nname = "old"\nmodel = "m"\nworkspace = "/w"\n'
    (conv_dir / "config.toml").write_text(original)

    class Conv:
        path = str(conv_dir / "conversation.jsonl")

    def failing_dump(data, f, *args, **kwargs):
        f.write("[chat]\nna")
        raise OSError(28, "No space left on device")

    with (
        patch(
            "gptme.logmanager.conversations.get_conversation_by_id",
            return_value=Conv(),
        ),
        patch("gptme.logmanager.conversations.tomlkit.dump", failing_dump),
        pytest.raises(OSError, match="No space left"),
    ):
        rename_conversation("conv", "new")

    assert (conv_dir / "config.toml").read_text() == original
    assert not [p.name for p in conv_dir.iterdir() if p.name.endswith(".tmp")]


def test_rename_reads_utf8_config_under_legacy_default_codec(tmp_path: Path) -> None:
    """A UTF-8 config.toml (what gptme writes) must survive a rename on a
    machine whose default text codec is a legacy code page."""
    import builtins

    conv_dir = tmp_path / "conv"
    conv_dir.mkdir()
    (conv_dir / "conversation.jsonl").write_text("")
    # "Á" is UTF-8 c3 81, and cp1252 has no character for 0x81: a default-codec
    # read fails outright instead of round-tripping mojibake back to the same
    # bytes (as "é" -> "Ã©" -> c3 a9 would), so this distinguishes the fix.
    (conv_dir / "config.toml").write_bytes(
        '[chat]\nname = "old"\nworkspace = "/w/Ácme"\n'.encode()
    )

    class Conv:
        path = str(conv_dir / "conversation.jsonl")

    real_open = builtins.open

    def legacy_open(file, mode="r", *args, **kwargs):
        if "b" not in mode and kwargs.get("encoding") is None and len(args) < 2:
            kwargs["encoding"] = "cp1252"
        return real_open(file, mode, *args, **kwargs)

    with (
        patch(
            "gptme.logmanager.conversations.get_conversation_by_id",
            return_value=Conv(),
        ),
        patch.object(builtins, "open", legacy_open),
    ):
        assert rename_conversation("conv", "new")

    text = (conv_dir / "config.toml").read_bytes().decode("utf-8")
    assert 'workspace = "/w/Ácme"' in text
    assert 'name = "new"' in text


def _rename(conv_dir: Path) -> None:
    class Conv:
        path = str(conv_dir / "conversation.jsonl")

    with patch(
        "gptme.logmanager.conversations.get_conversation_by_id",
        return_value=Conv(),
    ):
        assert rename_conversation("conv", "new")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes and symlinks")
def test_rename_keeps_mode_and_symlink(tmp_path: Path) -> None:
    conv_dir = tmp_path / "conv"
    conv_dir.mkdir()
    (conv_dir / "conversation.jsonl").write_text("")
    real = tmp_path / "shared-config.toml"
    real.write_text('[chat]\nname = "old"\n')
    real.chmod(0o640)
    (conv_dir / "config.toml").symlink_to(real)

    _rename(conv_dir)

    assert (conv_dir / "config.toml").is_symlink()
    assert 'name = "new"' in real.read_text()
    assert stat.S_IMODE(real.stat().st_mode) == 0o640


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_reencoding_backup_keeps_private_mode(tmp_path: Path) -> None:
    from gptme.config import user

    conv_dir = tmp_path / "conv"
    conv_dir.mkdir()
    (conv_dir / "conversation.jsonl").write_text("")
    config = conv_dir / "config.toml"
    config.write_text('[chat]\nname = "old"\n[env]\nKEY = "secret"\n')
    config.chmod(0o600)
    user._encoding_unverified.add(str(config.resolve()))

    _rename(conv_dir)

    backup = conv_dir / "config.toml.orig"
    assert "secret" in backup.read_text()
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


# ── 4. write() must not clobber branches other managers appended to ──


def _contents(path: Path) -> list[str]:
    return [json.loads(line)["content"] for line in path.read_text().splitlines()]


def test_other_branch_append_does_not_rewrite_main(tmp_path: Path) -> None:
    logdir = tmp_path / "conv"
    setup = LogManager(logdir=logdir, lock=False)
    setup.append(Message("user", "m1"))
    setup.branch("dev")
    setup.append(Message("user", "d1"))

    a = LogManager.load(logdir, lock=False)
    b = LogManager.load(logdir, branch="dev", lock=False)
    a.append(Message("user", "m2"))
    b.append(Message("user", "d2"))

    assert _contents(logdir / "conversation.jsonl") == ["m1", "m2"]
    assert _contents(logdir / "branches" / "dev.jsonl") == ["m1", "d1", "d2"]


def test_other_branch_append_does_not_rewrite_sibling_branch(tmp_path: Path) -> None:
    logdir = tmp_path / "conv"
    setup = LogManager(logdir=logdir, lock=False)
    setup.append(Message("user", "m1"))
    setup.branch("dev")
    setup.append(Message("user", "dev1"))
    setup.branch("feat")
    setup.append(Message("user", "feat1"))

    a = LogManager.load(logdir, branch="dev", lock=False)
    b = LogManager.load(logdir, branch="feat", lock=False)
    a.append(Message("user", "dev2"))
    b.append(Message("user", "feat2"))

    assert _contents(logdir / "branches" / "dev.jsonl") == ["m1", "dev1", "dev2"]
    assert _contents(logdir / "branches" / "feat.jsonl") == [
        "m1",
        "dev1",
        "feat1",
        "feat2",
    ]


def test_branch_append_does_not_rewrite_main_with_unsnapshotted_attachment(
    tmp_path: Path,
) -> None:
    """Loading re-snapshots an attachment whose stored copy is missing (legacy
    logs, deleted store entries), which replaces the message object. That must
    not make an unchanged main look locally modified and get rewritten."""
    import shutil

    logdir = tmp_path / "conv"
    attachment = tmp_path / "notes.txt"
    attachment.write_text("hello")
    setup = LogManager(logdir=logdir, lock=False)
    setup.append(Message("user", "m1", files=[attachment]))
    setup.branch("dev")
    setup.append(Message("user", "d1"))
    shutil.rmtree(logdir / "files")
    # A legacy log: written before attachments carried content hashes.
    for path in (logdir / "conversation.jsonl", logdir / "branches" / "dev.jsonl"):
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        for row in rows:
            row.pop("file_hashes", None)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    a = LogManager.load(logdir, lock=False)
    b = LogManager.load(logdir, branch="dev", lock=False)
    a.append(Message("user", "m2"))
    b.append(Message("user", "d2"))

    assert _contents(logdir / "conversation.jsonl") == ["m1", "m2"]
    assert _contents(logdir / "branches" / "dev.jsonl") == ["m1", "d1", "d2"]


@pytest.mark.parametrize("transcript", ["branch", "view"])
@pytest.mark.parametrize("source_change", ["edit", "delete"])
def test_activated_legacy_transcript_persists_attachment_snapshot(
    tmp_path: Path, transcript: str, source_change: str
) -> None:
    from gptme.logmanager import Log
    from gptme.util.file_storage import get_stored_path

    logdir = tmp_path / "conv"
    attachment = tmp_path / "notes.txt"
    attachment.write_text("original bytes")
    setup = LogManager(logdir=logdir, lock=False)
    setup.append(Message("user", "main"))
    if transcript == "branch":
        directory, name = "branches", "dev"
    else:
        directory, name = "views", "compacted-001"
    path = logdir / directory / f"{name}.jsonl"
    path.parent.mkdir(exist_ok=True)
    Log([Message("user", "legacy", files=[attachment])]).write_jsonl(path)
    original = path.read_bytes()

    manager = LogManager.load(logdir, lock=False)
    # Unrelated writes must not migrate an inactive transcript from a stale copy.
    manager.append(Message("user", "another main message"))
    assert path.read_bytes() == original
    if transcript == "branch":
        manager.branch(name)
    else:
        manager.switch_view(name)
    manager.append(Message("user", "active append"))
    snapshot_hash = manager.log.messages[0].file_hashes[str(attachment)]
    assert json.loads(path.read_text().splitlines()[0])["file_hashes"] == {
        str(attachment): snapshot_hash
    }

    if source_change == "edit":
        attachment.write_text("different bytes")
    else:
        attachment.unlink()
    reloaded = LogManager.load(
        logdir, branch=name if transcript == "branch" else "main", lock=False
    )
    assert reloaded.log.messages[0].file_hashes[str(attachment)] == snapshot_hash
    stored = get_stored_path(logdir, snapshot_hash, ".txt")
    assert stored is not None
    assert stored.read_text() == "original bytes"
