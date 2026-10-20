"""The parent's read of a child log must not hold the child's conversation lock."""

import sys
from pathlib import Path

import pytest

from gptme.logmanager import Log
from gptme.message import Message
from gptme.tools.subagent.types import Subagent

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="flock-based lock probe"
)


def test_reading_child_log_leaves_lock_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fcntl

    logdir = tmp_path / "child"
    logdir.mkdir()
    Log([Message("user", "hi"), Message("assistant", "done")]).write_jsonl(
        logdir / "conversation.jsonl"
    )
    # LogManager skips locking under pytest; exercise the real lock path.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    sa = Subagent(
        agent_id="child",
        prompt="hi",
        thread=None,
        logdir=logdir,
        model=None,
        execution_mode="subprocess",
    )
    assert len(sa.get_log().log.messages) == 2

    # A resumed subprocess child takes this lock; it must be acquirable.
    lockfile = logdir / ".lock"
    lockfile.touch()
    with lockfile.open("r+") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
