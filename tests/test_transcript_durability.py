"""Persistence barriers use real files; only syscall observation/failure is injected."""

import importlib
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gptme.logmanager import Log, LogManager, eventlog
from gptme.message import Message

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux syscall observation via /proc"
)


@pytest.fixture
def synced_paths(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    paths: list[Path] = []
    fsync = os.fsync

    def record(fd: int) -> None:
        paths.append(Path(os.readlink(f"/proc/self/fd/{fd}")))
        fsync(fd)

    monkeypatch.setattr(os, "fsync", record)
    return paths


def test_explicit_sync_covers_transcripts_and_namespaces(
    tmp_path: Path, synced_paths: list[Path]
) -> None:
    logdir = tmp_path / "logs" / "session"
    with LogManager(logdir=logdir, lock=False) as manager:
        manager.append(Message("user", "question", quiet=True))
        manager.create_view("compact", manager.log)
        manager.switch_view("compact")
        manager.append(Message("assistant", "final answer", quiet=True))
        synced_paths.clear()
        manager.write(sync=True)

    expected = [
        logdir / "conversation.jsonl",
        logdir / "views" / "compact.jsonl",
        logdir / "events.jsonl",
    ]
    for path in expected:
        assert path in synced_paths, f"unacknowledged transcript: {path}"
        assert path.parent in synced_paths
        assert synced_paths.index(path) < synced_paths.index(path.parent)
    assert logdir.parent in synced_paths
    assert tmp_path in synced_paths
    assert tmp_path.parent not in synced_paths
    assert Log.read_jsonl(expected[0])[-1].content == "final answer"
    events = eventlog.recover_messages(logdir)
    assert events is not None
    assert events[-1]["content"] == "final answer"


def test_write_jsonl_replacement_syncs_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "conversation.jsonl"
    Log([Message("assistant", "old")]).write_jsonl(path)
    replace = os.replace
    order: list[str] = []
    fsync = os.fsync

    def record_replace(src, dst) -> None:
        order.append("replace")
        replace(src, dst)

    def record_sync(fd: int) -> None:
        observed = Path(os.readlink(f"/proc/self/fd/{fd}"))
        order.append("directory" if observed.is_dir() else "file")
        fsync(fd)

    monkeypatch.setattr(os, "replace", record_replace)
    monkeypatch.setattr(os, "fsync", record_sync)
    Log([Message("assistant", "new")]).write_jsonl(path)
    assert order == ["file", "replace", "directory"]
    assert Log.read_jsonl(path)[-1].content == "new"


def test_checkpoint_replacement_syncs_namespace(
    tmp_path: Path, synced_paths: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    replace = os.replace
    order: list[str] = []
    fsync = os.fsync

    def record_replace(src, dst) -> None:
        order.append("replace")
        replace(src, dst)

    def record_sync(fd: int) -> None:
        path = Path(os.readlink(f"/proc/self/fd/{fd}"))
        order.append("directory" if path.is_dir() else "file")
        fsync(fd)

    monkeypatch.setattr(os, "replace", record_replace)
    monkeypatch.setattr(os, "fsync", record_sync)
    eventlog.write_checkpoint(tmp_path, [Message("assistant", "answer").to_dict()])
    assert order == ["file", "replace", "directory"]
    events = eventlog.recover_messages(tmp_path)
    assert events is not None
    assert events[0]["content"] == "answer"


def test_directory_sync_failure_prevents_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fsync = os.fsync

    def fail_directory(fd: int) -> None:
        if Path(os.readlink(f"/proc/self/fd/{fd}")).is_dir():
            raise OSError("injected directory I/O failure")
        fsync(fd)

    with LogManager(logdir=tmp_path, lock=False) as manager:
        manager.append(Message("assistant", "answer", quiet=True))
        monkeypatch.setattr(os, "fsync", fail_directory)
        with pytest.raises(OSError, match="injected directory I/O failure"):
            manager.write(sync=True)


def test_branch_restart_barrier_covers_main_branch_and_recovery(
    tmp_path: Path, synced_paths: list[Path]
) -> None:
    with LogManager(logdir=tmp_path, lock=False) as manager:
        manager.append(Message("user", "main", quiet=True))
        manager.branch("alternative")
        manager.append(Message("assistant", "branch final", quiet=True))
        synced_paths.clear()
        manager.write(sync=True)  # Same barrier used by both restart callers.
    for path in [
        tmp_path / "conversation.jsonl",
        tmp_path / "branches" / "alternative.jsonl",
        tmp_path / "events.jsonl",
        tmp_path / "branches" / "alternative" / "events.jsonl",
    ]:
        assert path in synced_paths
        assert synced_paths.index(path) < synced_paths.index(path.parent)
    with LogManager.load(tmp_path, branch="alternative", lock=False) as recovered:
        assert recovered.log[-1].content == "branch final"
    events = eventlog.recover_messages(tmp_path / "branches" / "alternative")
    assert events is not None
    assert events[-1]["content"] == "branch final"


def test_killed_rewrite_preserves_acknowledged_transcript(tmp_path: Path) -> None:
    """Kill only an isolated writer after temp-file sync, before replacement."""
    path = tmp_path / "conversation.jsonl"
    Log([Message("assistant", "acknowledged answer")]).write_jsonl(path)
    before = path.read_bytes()
    program = """
import os, signal, sys
from gptme.logmanager import Log
from gptme.message import Message
def die(src, dst):
    os.kill(os.getpid(), signal.SIGKILL)
os.replace = die
Log([Message('assistant', 'unacknowledged replacement')]).write_jsonl(sys.argv[1])
"""
    result = subprocess.run([sys.executable, "-c", program, str(path)], check=False)
    assert result.returncode == -signal.SIGKILL
    assert path.read_bytes() == before
    assert Log.read_jsonl(path)[-1].content == "acknowledged answer"


def test_failed_fork_keeps_original_namespace_barrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fork_root = tmp_path / "forks"
    (fork_root / "exists").mkdir(parents=True)
    monkeypatch.setenv("GPTME_LOGS_HOME", str(fork_root))
    original = tmp_path / "original" / "session"
    with LogManager(logdir=original, lock=False) as manager:
        manager.append(Message("assistant", "answer", quiet=True))
        with pytest.raises(FileExistsError):
            manager.fork("exists")
        manager.write(sync=True)
        assert manager.logdir == original


def test_relative_logdir_stays_pinned_after_chdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with LogManager(logdir="session", lock=False) as manager:
        monkeypatch.chdir(tmp_path.parent)
        manager.append(Message("assistant", "answer", quiet=True))
        manager.write(sync=True)
        assert manager.logfile == tmp_path / "session" / "conversation.jsonl"


def test_killed_process_after_ack_recovers_final_output(tmp_path: Path) -> None:
    program = """
import os, signal, sys
from pathlib import Path
from gptme.logmanager import LogManager
from gptme.message import Message
manager = LogManager(logdir=Path(sys.argv[1]), lock=False)
manager.append(Message('assistant', 'acknowledged answer', quiet=True))
manager.write(sync=True)
print('ACK', flush=True)
os.kill(os.getpid(), signal.SIGKILL)
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() == "ACK"
    assert result.returncode == -signal.SIGKILL
    with LogManager.load(tmp_path, lock=False) as manager:
        assert manager.log[-1].content == "acknowledged answer"
    events = eventlog.recover_messages(tmp_path)
    assert events is not None
    assert events[-1]["content"] == "acknowledged answer"


@pytest.mark.parametrize("complete_tool", [False, True])
@pytest.mark.parametrize("fail_barrier", [False, True])
def test_chat_success_acknowledges_final_hook_output(
    tmp_path: Path, complete_tool: bool, fail_barrier: bool
) -> None:
    # gptme.__init__ exports the ``chat`` function, so importing the module via
    # importlib avoids binding that package attribute when test order changes.
    chat_module = importlib.import_module("gptme.chat")
    from gptme.hooks import HookType
    from gptme.logmanager import durability
    from gptme.tools.complete import SessionCompleteException

    barriers: list[str] = []
    sync_directories = durability.sync_directories

    def barrier(paths: set[Path], root: Path) -> None:
        barriers.append(Log.read_jsonl(tmp_path / "conversation.jsonl")[-1].content)
        if fail_barrier:
            raise OSError("final barrier failed")
        sync_directories(paths, root)

    def process(manager, *args, **kwargs) -> None:
        manager.append(Message("assistant", "final answer", quiet=True))
        if complete_tool:
            raise SessionCompleteException("completed")

    def hooks(hook, **kwargs):
        if hook == HookType.SESSION_END:
            return [Message("system", "final hook record", quiet=True)]
        return []

    with (
        patch.object(chat_module, "init"),
        patch.object(chat_module, "trigger_hook", side_effect=hooks),
        patch.object(chat_module, "get_default_model", return_value=None),
        patch.object(
            chat_module,
            "get_model",
            return_value=SimpleNamespace(supports_streaming=True),
        ),
        patch.object(chat_module.os, "chdir"),
        patch.object(chat_module, "_process_message_conversation", side_effect=process),
        patch("gptme.logmanager.manager.sync_directories", side_effect=barrier),
    ):

        def run() -> None:
            chat_module.chat(
                [Message("user", "hello", quiet=True)],
                [],
                tmp_path,
                tmp_path,
                "mock/model",
                interactive=False,
                tool_format="markdown",
                output_format="quiet",
            )

        if fail_barrier:
            with pytest.raises(OSError, match="final barrier failed"):
                run()
        else:
            run()
            assert (
                Log.read_jsonl(tmp_path / "conversation.jsonl")[-1].content
                == "final hook record"
            )
            events = eventlog.recover_messages(tmp_path)
            assert events is not None
            assert events[-1]["content"] == "final hook record"
    assert barriers == ["final hook record"]


@pytest.mark.parametrize("fail_barrier", [False, True])
def test_consumed_command_waits_for_turn_post_barrier(
    tmp_path: Path, fail_barrier: bool
) -> None:
    chat_module = importlib.import_module("gptme.chat")
    from gptme.hooks import HookType
    from gptme.logmanager import durability

    barriers: list[str] = []
    sync_directories = durability.sync_directories

    def barrier(paths: set[Path], root: Path) -> None:
        barriers.append(Log.read_jsonl(tmp_path / "conversation.jsonl")[-1].content)
        if fail_barrier:
            raise OSError("command barrier failed")
        sync_directories(paths, root)

    def hooks(hook, **kwargs):
        if hook == HookType.TURN_POST:
            return [Message("system", "turn post record", quiet=True)]
        return []

    with (
        LogManager(logdir=tmp_path, lock=False) as manager,
        patch.object(
            chat_module,
            "step",
            return_value=iter([Message("user", "/handled", quiet=True)]),
        ),
        patch.object(chat_module, "execute_cmd", return_value=True),
        patch.object(chat_module, "trigger_hook", side_effect=hooks),
        patch("gptme.logmanager.manager.sync_directories", side_effect=barrier),
    ):
        if fail_barrier:
            with pytest.raises(OSError, match="command barrier failed"):
                chat_module._process_message_conversation(
                    manager, stream=False, tool_format="markdown", model=None
                )
        else:
            chat_module._process_message_conversation(
                manager, stream=False, tool_format="markdown", model=None
            )
            assert manager.log[-1].content == "turn post record"

    assert barriers == ["turn post record"]
