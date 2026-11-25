"""No-op saves preserve concurrent growth but repair a shortened transcript."""

from pathlib import Path

import pytest

from gptme.logmanager import LogManager
from gptme.logmanager.manager import Log
from gptme.message import Message


@pytest.mark.parametrize("partial", [False, True])
def test_unchanged_log_repairs_truncated_file(tmp_path: Path, partial: bool) -> None:
    path = tmp_path / "conversation.jsonl"
    Log([Message("user", "one"), Message("assistant", "two")]).write_jsonl(path)
    original = path.read_bytes()
    log = Log.read_jsonl(path)
    path.write_bytes(original[: len(original) // 2] if partial else b"")

    log.write_jsonl(path, append=True)

    assert path.read_bytes() == original


def test_inactive_branch_repairs_truncated_file(tmp_path: Path) -> None:
    manager = LogManager(logdir=tmp_path, lock=False)
    manager.append(Message("user", "one"))
    manager.branch("dev")
    manager.append(Message("assistant", "two"))
    manager = LogManager.load(tmp_path, branch="dev", lock=False)
    path = tmp_path / "conversation.jsonl"
    original = path.read_bytes()
    path.write_bytes(b"")

    manager.write()

    assert path.read_bytes() == original
