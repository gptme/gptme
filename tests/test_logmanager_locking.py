from pathlib import Path

import pytest

from gptme.logmanager import LogManager


def test_same_process_conversation_lock_is_reentrant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second LogManager in the same process must reuse the conversation lock."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    logdir = tmp_path / "conversation"

    first = LogManager(logdir=logdir)
    try:
        second = LogManager(logdir=logdir)
        assert second.logdir == first.logdir
    finally:
        first._release_lock()

    # Releasing the owning manager must let this process acquire the OS lock again.
    with LogManager(logdir=logdir):
        pass
