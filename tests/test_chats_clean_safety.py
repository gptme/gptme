"""Cleanup must not discard owned histories or pending user work."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gptme.cli.cmd_chats import chats_clean
from gptme.logmanager import LogManager
from gptme.prompt_queue import get_prompt_queue_path, queue_prompt
from gptme.tools.chats import find_empty_conversations


@pytest.fixture
def histories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("gptme.logmanager.conversations.get_logs_dir", lambda: tmp_path)
    monkeypatch.setattr("gptme.cli.cmd_chats._ensure_tools", lambda: None)
    return tmp_path


def make_history(root: Path, name: str = "abandoned") -> Path:
    directory = root / name
    directory.mkdir()
    # Persist config so metadata discovery does not initialise a legacy workspace.
    (directory / "config.toml").write_text('[chat]\nname = "fixture"\n')
    (directory / "conversation.jsonl").write_text(
        json.dumps({"role": "system", "content": "Synthetic cleanup fixture"}) + "\n"
    )
    return directory


def clean(*args: str) -> dict:
    result = CliRunner().invoke(chats_clean, ["--include-test", "--json", *args])
    assert result.exit_code == 0, result.output + repr(result.exception)
    return json.loads(result.output)


@pytest.mark.parametrize("steer", [False, True])
def test_pending_queue_is_preserved(histories: Path, steer: bool) -> None:
    directory = make_history(histories)
    queue_prompt(directory, "Unsent user work", steer=steer)
    before = get_prompt_queue_path(directory).read_bytes()
    result = clean("--delete")
    assert result["deleted"] == 0
    assert get_prompt_queue_path(directory).read_bytes() == before


def test_live_other_process_is_preserved(histories: Path) -> None:
    directory = make_history(histories)
    code = """
import sys
from pathlib import Path
from gptme.logmanager import LogManager
with LogManager.load(Path(sys.argv[1]), lock=True):
    print('locked', flush=True)
    sys.stdin.readline()
"""
    env = os.environ.copy()
    env.pop("PYTEST_CURRENT_TEST", None)
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(directory)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        before = (directory / "conversation.jsonl").read_bytes()
        assert clean("--delete")["deleted"] == 0
        assert (directory / "conversation.jsonl").read_bytes() == before
        assert child.poll() is None
    finally:
        child.communicate("\n", timeout=15)
    assert child.returncode == 0


def test_same_process_owner_is_not_reused_for_cleanup(
    histories: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = make_history(histories)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with LogManager.load(directory, lock=True):
        assert clean("--delete")["deleted"] == 0
        assert directory.exists()


@pytest.mark.parametrize("lock_contents", [None, "999999999", "current-pid"])
def test_abandoned_and_stale_lock_controls(
    histories: Path, lock_contents: str | None
) -> None:
    directory = make_history(histories)
    if lock_contents is not None:
        contents = str(os.getpid()) if lock_contents == "current-pid" else lock_contents
        (directory / ".lock").write_text(contents)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    dry = clean()
    assert dry["found"] == 1 and dry["deleted"] == 0
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
    actual = clean("--delete")
    assert actual["deleted"] == 1
    assert actual["freed_bytes"] == dry["total_bytes"]
    assert not directory.exists()


@pytest.mark.parametrize("mutation", ["queue", "messages", "branch"])
def test_scan_to_delete_revalidates(histories: Path, mutation: str) -> None:
    directory = make_history(histories)

    def scan(**kwargs: object) -> list[dict]:
        results = find_empty_conversations(include_test=True)
        assert len(results) == 1
        if mutation == "queue":
            queue_prompt(directory, "Work queued after scan")
        elif mutation == "messages":
            with (directory / "conversation.jsonl").open("a") as file:
                file.write(json.dumps({"role": "user", "content": "New work"}) + "\n")
        else:
            (directory / "branches").mkdir()
            (directory / "branches" / "saved.jsonl").write_text(
                json.dumps({"role": "user", "content": "Saved work"}) + "\n"
            )
        return results

    with patch("gptme.cli.cmd_chats.find_empty_conversations", side_effect=scan):
        result = clean("--delete")
    assert result["deleted"] == 0
    assert directory.exists()


def test_saved_branch_protected_at_scan(histories: Path) -> None:
    directory = make_history(histories)
    (directory / "branches").mkdir()
    (directory / "branches" / "saved.jsonl").write_text(
        json.dumps({"role": "user", "content": "Saved work"}) + "\n"
    )
    assert clean("--delete")["deleted"] == 0
    assert directory.exists()


def test_queue_cannot_recreate_deleted_history(histories: Path) -> None:
    directory = make_history(histories)
    assert clean("--delete")["deleted"] == 1
    with pytest.raises(FileNotFoundError):
        queue_prompt(directory, "Must not acknowledge an orphaned prompt")
    assert not directory.exists()


@pytest.mark.parametrize("payload", ['{"content":', "[]\n", "{}\n"])
def test_damaged_history_is_not_empty(histories: Path, payload: str) -> None:
    directory = make_history(histories)
    (directory / "conversation.jsonl").write_text(payload)
    # Direct deletion admission must be conservative even if discovery tolerated
    # or skipped the damaged record.
    from gptme.logmanager.cleanup import delete_empty_conversation

    assert not delete_empty_conversation(directory.name)
    assert (directory / "conversation.jsonl").read_text() == payload


@pytest.mark.parametrize("role", [None, 42, [], {}])
@pytest.mark.parametrize(
    "history", ["conversation.jsonl", "lossless.jsonl", "views/compacted.jsonl"]
)
def test_invalid_message_role_is_preserved(
    histories: Path, role: object, history: str
) -> None:
    from gptme.logmanager.cleanup import delete_empty_conversation

    directory = make_history(histories)
    target = directory / history
    target.parent.mkdir(exist_ok=True)
    record: dict[str, object] = {"content": "Recoverable user work"}
    if role is not None:
        record["role"] = role
    payload = (json.dumps(record) + "\n").encode()
    target.write_bytes(payload)
    assert clean("--delete")["deleted"] == 0
    assert not delete_empty_conversation(directory.name)
    assert target.read_bytes() == payload


@pytest.mark.parametrize("history", ["lossless.jsonl", "views/compacted.jsonl"])
def test_deeply_nested_history_is_preserved_and_cleanup_continues(
    histories: Path, history: str
) -> None:
    from gptme.logmanager.cleanup import delete_empty_conversation

    directory = make_history(histories, "damaged")
    target = directory / history
    target.parent.mkdir(exist_ok=True)
    # Truncated JSON stays damaged even on decoders that support deeper nesting.
    payload = (
        '{"role":"system","content":"Recoverable work","extra":' + "[" * 10000 + "0\n"
    ).encode()
    target.write_bytes(payload)
    before = {
        path.relative_to(directory): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }
    abandoned = make_history(histories, "abandoned")

    assert not delete_empty_conversation(directory.name)
    assert clean()["found"] == 1
    result = clean("--delete")
    assert result["deleted"] == 1
    assert not abandoned.exists()
    assert {
        path.relative_to(directory): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    } == before


@pytest.mark.parametrize("extra", ["lossless.jsonl", "views/compacted.jsonl"])
def test_alternate_history_is_preserved(histories: Path, extra: str) -> None:
    directory = make_history(histories)
    target = directory / extra
    target.parent.mkdir(exist_ok=True)
    target.write_text(
        json.dumps({"role": "user", "content": "Saved work"})
        + "\n"
        + json.dumps({"role": "assistant", "content": "Reply"})
        + "\n"
    )
    assert clean("--delete")["deleted"] == 0
    assert target.exists()


def test_queue_publication_wins_cleanup_race(
    histories: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    from contextlib import contextmanager

    import gptme.prompt_queue as queue_module

    directory = make_history(histories)
    entered = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []
    original = queue_module._prompt_queue_lock

    @contextmanager
    def paused_queue_lock(logdir: Path):
        with original(logdir):
            entered.set()
            assert release.wait(10)
            yield

    monkeypatch.setattr(queue_module, "_prompt_queue_lock", paused_queue_lock)

    def publish() -> None:
        try:
            queue_prompt(directory, "Publication in flight")
        except BaseException as exc:
            errors.append(exc)

    writer = threading.Thread(target=publish)
    writer.start()
    try:
        assert entered.wait(10)
        assert clean("--delete")["deleted"] == 0
    finally:
        release.set()
        writer.join(10)
    assert not writer.is_alive()
    assert not errors
    assert "Publication in flight" in get_prompt_queue_path(directory).read_text()
    assert clean("--delete")["deleted"] == 0


def test_cleanup_wins_queue_publication_race(
    histories: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    import gptme.logmanager.cleanup as cleanup_module
    from gptme.logmanager.conversations import get_conversation_by_id

    directory = make_history(histories)
    conv = get_conversation_by_id(directory.name)
    assert conv is not None
    entered = threading.Event()
    attempted = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []
    deleted: list[bool] = []
    original = cleanup_module._eligible

    def paused_eligible(logdir: Path, max_messages: int) -> bool:
        eligible = original(logdir, max_messages)
        entered.set()
        assert release.wait(10)
        return eligible

    monkeypatch.setattr(cleanup_module, "_eligible", paused_eligible)

    def remove() -> None:
        deleted.append(
            cleanup_module.cleanup_conversation(conv, max_messages=1, delete=True)
        )

    def publish() -> None:
        attempted.set()
        try:
            queue_prompt(directory, "Arriving during deletion")
        except BaseException as exc:
            errors.append(exc)

    cleaner = threading.Thread(target=remove)
    writer = threading.Thread(target=publish)
    cleaner.start()
    try:
        assert entered.wait(10)
        writer.start()
        assert attempted.wait(10)
    finally:
        release.set()
        cleaner.join(10)
        if writer.ident is not None:
            writer.join(10)
    assert not cleaner.is_alive() and not writer.is_alive()
    assert deleted == [True]
    assert len(errors) == 1 and isinstance(errors[0], FileNotFoundError)
    assert not directory.exists()


def test_deletion_permission_failure_is_observable(histories: Path) -> None:
    directory = make_history(histories)
    with patch(
        "gptme.logmanager.cleanup.shutil.rmtree",
        side_effect=PermissionError("Synthetic deletion failure"),
    ):
        result = CliRunner().invoke(chats_clean, ["--include-test", "--delete"])
    assert result.exit_code == 0, result.output
    assert "Synthetic deletion failure" in result.output
    assert "Deleted 0 conversation(s)" in result.output
    assert directory.exists()


def test_text_cleanup_counts_only_abandoned(histories: Path) -> None:
    abandoned = make_history(histories, "abandoned")
    pending = make_history(histories, "pending")
    queue_prompt(pending, "Unsent")
    result = CliRunner().invoke(chats_clean, ["--include-test", "--delete"])
    assert result.exit_code == 0, result.output
    assert "Found 1 conversation(s)" in result.output
    assert "Deleted 1 conversation(s)" in result.output
    assert not abandoned.exists() and pending.exists()


def test_startup_wins_cleanup_race(
    histories: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    directory = make_history(histories)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    entered = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []
    original = LogManager._acquire_lock

    def paused_acquire(manager: LogManager) -> None:
        entered.set()
        assert release.wait(10)
        original(manager)

    monkeypatch.setattr(LogManager, "_acquire_lock", paused_acquire)
    managers: list[LogManager] = []

    def start() -> None:
        try:
            managers.append(LogManager.load(directory))
        except BaseException as exc:
            errors.append(exc)

    starter = threading.Thread(target=start)
    starter.start()
    try:
        assert entered.wait(10)
        assert clean("--delete")["deleted"] == 0
    finally:
        release.set()
        starter.join(10)
    try:
        assert not starter.is_alive() and not errors
        assert len(managers) == 1
        assert clean("--delete")["deleted"] == 0
    finally:
        for manager in managers:
            manager._release_lock()
    assert clean("--delete")["deleted"] == 1


@pytest.mark.parametrize("release_first", [0, 1])
def test_reused_managers_protect_until_last_release(
    histories: Path, monkeypatch: pytest.MonkeyPatch, release_first: int
) -> None:
    directory = make_history(histories)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    managers = [LogManager.load(directory), LogManager.load(directory)]
    try:
        managers[release_first]._release_lock()
        assert clean("--delete")["deleted"] == 0
    finally:
        for manager in managers:
            manager._release_lock()
    assert clean("--delete")["deleted"] == 1


def test_load_cannot_resurrect_history_deleted_before_ownership(
    histories: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import contextmanager

    import gptme.logmanager.locking as locking
    from gptme.logmanager.cleanup import delete_empty_conversation

    directory = make_history(histories)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    original = locking.conversation_lifecycle_lock
    deleted: list[bool] = []

    @contextmanager
    def delete_before_startup(logdir: Path, *, blocking: bool = True):
        if blocking and not deleted:
            deleted.append(delete_empty_conversation(directory.name))
        with original(logdir, blocking=blocking):
            yield

    monkeypatch.setattr(locking, "conversation_lifecycle_lock", delete_before_startup)
    with pytest.raises(FileNotFoundError), LogManager.load(directory, create=False):
        pass
    assert deleted == [True]
    assert not directory.exists()


def test_first_use_lifecycle_gate_cross_process(histories: Path) -> None:
    """Exercise native POSIX/Windows contention on a new, empty gate file."""
    from gptme.logmanager.locking import conversation_lifecycle_lock

    directory = make_history(histories)
    code = """
import sys
from pathlib import Path
from gptme.logmanager.locking import conversation_lifecycle_lock
with conversation_lifecycle_lock(Path(sys.argv[1])):
    print('locked', flush=True)
    sys.stdin.readline()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(directory)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        with (
            pytest.raises(OSError, match=".*"),
            conversation_lifecycle_lock(directory, blocking=False),
        ):
            pytest.fail("Acquired an owned lifecycle gate")
    finally:
        child.communicate("\n", timeout=15)
    assert child.returncode == 0
    with conversation_lifecycle_lock(directory):
        assert directory.exists()
    # Locks beyond EOF require no first-use write racing another owner's lock.
    assert all(
        p.stat().st_size == 0 for p in (histories / ".conversation-locks").iterdir()
    )


@pytest.mark.parametrize("name", ["example", "example.jsonl"])
@pytest.mark.parametrize("lock", [False, True])
def test_load_normalizes_history_file_once(
    histories: Path, monkeypatch: pytest.MonkeyPatch, name: str, lock: bool
) -> None:
    directory = make_history(histories, name)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with LogManager.load(directory / "conversation.jsonl", lock=lock) as manager:
        assert manager.logdir == directory
        assert manager.log.messages[0].content == "Synthetic cleanup fixture"
        if lock:
            assert clean("--delete")["deleted"] == 0
    assert clean("--delete")["deleted"] == 1


def test_read_and_search_jsonl_named_chat(
    histories: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from gptme.tools.chats import read_chat, search_chats

    directory = make_history(histories, "example.jsonl")
    with (directory / "conversation.jsonl").open("a") as file:
        file.write(json.dumps({"role": "user", "content": "searchable fixture"}) + "\n")
    read_chat(directory.name)
    assert "searchable fixture" in capsys.readouterr().out
    search_chats("searchable fixture")
    assert "searchable fixture" in capsys.readouterr().out
