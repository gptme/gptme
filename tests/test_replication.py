"""Tests for gptme/logmanager/replication.py"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

from gptme.config.core import Config, _config_var
from gptme.config.models import EventLogReplicationConfig, UserConfig
from gptme.logmanager import LogManager, eventlog
from gptme.logmanager.replication import (
    FakeBackend,
    ReplicationWorker,
    _event_log_key,
    get_worker,
    recover_from_remote,
    reset_worker,
)
from gptme.message import Message

if TYPE_CHECKING:
    pass


@pytest.fixture(autouse=True)
def _reset_worker():
    """Ensure no process-level worker leaks between tests."""
    reset_worker()
    yield
    reset_worker()


@pytest.fixture
def logdir(tmp_path: Path) -> Path:
    d = tmp_path / "conv-abc123"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def fake_backend() -> FakeBackend:
    return FakeBackend()


# ── object key helpers ────────────────────────────────────────────────────────


def test_event_log_key():
    assert _event_log_key("conv-abc") == "conv-abc/events.jsonl"
    assert (
        _event_log_key("conv-abc", "experiment")
        == "conv-abc/branches/experiment/events.jsonl"
    )


# ── FakeBackend ───────────────────────────────────────────────────────────────


def test_fake_backend_round_trip(tmp_path: Path):
    """FakeBackend upload then download recovers the same bytes."""
    backend = FakeBackend()
    src = tmp_path / "events.jsonl"
    src.write_text('{"seq":1}\n')

    backend.upload("my/key", src)
    assert len(backend.upload_calls) == 1

    dest = tmp_path / "recovered.jsonl"
    found = backend.download("my/key", dest)
    assert found
    assert dest.read_text() == '{"seq":1}\n'


def test_fake_backend_missing_key(tmp_path: Path):
    """FakeBackend download returns False for a key that was never uploaded."""
    backend = FakeBackend()
    dest = tmp_path / "out.jsonl"
    assert not backend.download("nonexistent/key", dest)


def test_fake_backend_fail_upload(tmp_path: Path):
    """FakeBackend raises when fail_upload is set."""
    backend = FakeBackend()
    backend.fail_upload = True
    src = tmp_path / "events.jsonl"
    src.write_text("")
    with pytest.raises(OSError, match="simulated upload failure"):
        backend.upload("key", src)


# ── ReplicationWorker ─────────────────────────────────────────────────────────


def _write_event_log(logdir: Path, n: int = 1) -> None:
    """Write *n* dummy events into logdir for upload."""
    for i in range(1, n + 1):
        eventlog.append_event(
            logdir,
            {"seq": i, "ts": "2026-01-01T00:00:00Z", "type": "test", "payload": {}},
        )


def _wait_until(predicate: Callable[[], bool], timeout: float = 1.0) -> bool:
    """Wait for a worker-thread condition without a fixed test sleep."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.001)
    return predicate()


def test_worker_enqueues_upload(logdir: Path, fake_backend: FakeBackend):
    """Enqueuing a logdir leads to one upload call after debounce."""
    _write_event_log(logdir)
    worker = ReplicationWorker(fake_backend, debounce_s=0.05)
    worker.enqueue(logdir, "conv-abc123")
    worker.stop(timeout=2.0)
    assert len(fake_backend.upload_calls) == 1
    key, path = fake_backend.upload_calls[0]
    assert key == "conv-abc123/events.jsonl"
    assert path == logdir / "events.jsonl"


def test_worker_debounces_multiple_enqueues(logdir: Path, fake_backend: FakeBackend):
    """Many enqueues for the same logdir collapse into a single upload."""
    _write_event_log(logdir, n=5)
    worker = ReplicationWorker(fake_backend, debounce_s=0.1)
    for _ in range(10):
        worker.enqueue(logdir, "conv-abc123")
    worker.stop(timeout=2.0)
    assert len(fake_backend.upload_calls) == 1


def test_worker_keeps_branches_separate(tmp_path: Path, fake_backend: FakeBackend):
    """Branch uploads use distinct debounce entries and remote keys."""
    main_dir = tmp_path / "conv"
    branch_dir = main_dir / "branches" / "experiment"
    main_dir.mkdir()
    branch_dir.mkdir(parents=True)
    _write_event_log(main_dir)
    _write_event_log(branch_dir)

    worker = ReplicationWorker(fake_backend, debounce_s=0.1)
    worker.enqueue(main_dir, "conv-abc123")
    worker.enqueue(branch_dir, "conv-abc123", "experiment")
    worker.stop(timeout=2.0)

    assert set(fake_backend._store) == {
        "conv-abc123/events.jsonl",
        "conv-abc123/branches/experiment/events.jsonl",
    }


def test_worker_retries_on_transient_failure(logdir: Path):
    """Worker retries on transient upload failure and does not raise into append."""
    backend = FakeBackend()
    _write_event_log(logdir)

    call_count = 0

    def flaky_upload(key: str, local_path: Path) -> None:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise OSError("transient")
        backend._store[key] = local_path.read_bytes()

    backend.upload = flaky_upload  # type: ignore

    worker = ReplicationWorker(
        backend, debounce_s=0.01, max_retries=3, retry_backoff=0.01
    )
    worker.enqueue(logdir, "conv-abc123")
    assert _wait_until(lambda: call_count == 3)
    worker.stop(timeout=5.0)
    assert call_count == 3
    assert "conv-abc123/events.jsonl" in backend._store


def test_worker_gives_up_after_max_retries(logdir: Path, caplog):
    """Worker logs a warning and gives up after max_retries failures."""
    import logging

    backend = FakeBackend()
    backend.fail_upload = True
    _write_event_log(logdir)

    worker = ReplicationWorker(
        backend, debounce_s=0.01, max_retries=2, retry_backoff=0.01
    )
    with caplog.at_level(logging.WARNING, logger="gptme.logmanager.replication"):
        worker.enqueue(logdir, "conv-abc123")
        assert _wait_until(lambda: len(backend.upload_calls) == 2)
        worker.stop(timeout=5.0)

    assert any("after 2 attempts" in r.message for r in caplog.records)
    assert len(backend.upload_calls) == 2  # tried max_retries times


def test_worker_stop_interrupts_retry_backoff(logdir: Path):
    """Shutdown preserves retry attempts without waiting through backoff."""
    backend = FakeBackend()
    backend.fail_upload = True
    _write_event_log(logdir)
    worker = ReplicationWorker(
        backend, debounce_s=0.01, max_retries=7, retry_backoff=30.0
    )
    worker.enqueue(logdir, "conv-abc123")
    assert _wait_until(lambda: len(backend.upload_calls) == 1)

    started = time.monotonic()
    worker.stop(timeout=1.0)

    assert time.monotonic() - started < 1.0
    assert not worker._thread.is_alive()
    assert len(backend.upload_calls) == 7


def test_worker_shutdown_retries_transient_final_upload(logdir: Path):
    """A transient final-upload failure retains its configured retry budget."""
    backend = FakeBackend()
    _write_event_log(logdir)
    call_count = 0

    def flaky_upload(key: str, local_path: Path) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError("transient")
        backend._store[key] = local_path.read_bytes()

    backend.upload = flaky_upload  # type: ignore
    worker = ReplicationWorker(
        backend, debounce_s=30.0, max_retries=3, retry_backoff=30.0
    )
    worker.enqueue(logdir, "conv-abc123")
    worker.stop(timeout=1.0)

    assert call_count == 2
    assert "conv-abc123/events.jsonl" in backend._store


def test_worker_noop_when_log_missing(logdir: Path, fake_backend: FakeBackend):
    """Worker does not call upload when events.jsonl is absent."""
    # Don't write any events — logdir exists but events.jsonl does not
    worker = ReplicationWorker(fake_backend, debounce_s=0.01)
    worker.enqueue(logdir, "conv-abc123")
    worker.stop(timeout=2.0)
    assert len(fake_backend.upload_calls) == 0


def test_get_worker_isolates_backend_and_retry_config(logdir: Path):
    """Context-local destinations and retry settings get isolated workers."""
    _write_event_log(logdir)
    first = FakeBackend()
    first_worker = get_worker(first, debounce_ms=10, max_retries=1)
    first_worker.enqueue(logdir, "conv-first")

    second = FakeBackend()
    second_worker = get_worker(second, debounce_ms=20, max_retries=2)
    second_worker.enqueue(logdir, "conv-second")
    reset_worker()

    assert first_worker is not second_worker
    assert len(first.upload_calls) == 1
    assert len(second.upload_calls) == 1
    assert second_worker._max_retries == 2


def test_logmanager_forwards_branch_and_retry_config(
    logdir: Path, monkeypatch: pytest.MonkeyPatch
):
    """LogManager forwards the production branch event source and config."""
    config = Config(
        user=UserConfig(
            session_event_log_replication=EventLogReplicationConfig(
                enabled=True,
                backend="s3",
                bucket="test-bucket",
                upload_debounce_ms=25,
                max_retries=7,
            )
        )
    )
    token = _config_var.set(config)
    calls: list[tuple[Path, int, int, str]] = []

    class StubWorker:
        def enqueue(self, event_dir: Path, chat_id: str, branch: str) -> None:
            calls.append((event_dir, 25, 7, branch))

    def stub_get_worker(backend, debounce_ms: int, max_retries: int):
        assert debounce_ms == 25
        assert max_retries == 7
        return StubWorker()

    monkeypatch.setattr("gptme.logmanager.replication.get_worker", stub_get_worker)
    try:
        manager = LogManager(logdir=logdir, branch="experiment", lock=False)
        manager.append(Message(role="user", content="branch message"))
    finally:
        _config_var.reset(token)

    branch_event_dir = logdir / "branches" / "experiment"
    assert calls == [(branch_event_dir, 25, 7, "experiment")]
    assert (branch_event_dir / eventlog.EVENT_LOG_NAME).exists()
    assert not (logdir / "branches" / eventlog.EVENT_LOG_NAME).exists()


def test_logmanager_keeps_non_main_branch_event_histories_separate(
    logdir: Path, monkeypatch: pytest.MonkeyPatch
):
    """Production writes never mix two non-main branch event streams."""
    config = Config(user=UserConfig())
    token = _config_var.set(config)
    try:
        manager = LogManager(logdir=logdir, branch="alpha", lock=False)
        manager.append(Message(role="user", content="alpha message"))
        manager.branch("beta")
        manager.append(Message(role="user", content="beta message"))
    finally:
        _config_var.reset(token)

    alpha = eventlog.recover_messages(logdir / "branches" / "alpha")
    beta = eventlog.recover_messages(logdir / "branches" / "beta")
    assert alpha is not None
    assert beta is not None
    assert [message["content"] for message in alpha] == ["alpha message"]
    assert [message["content"] for message in beta] == ["beta message"]


# ── recover_from_remote ───────────────────────────────────────────────────────


def test_recover_from_remote_missing(fake_backend: FakeBackend):
    """Returns None when no remote object exists."""
    result = recover_from_remote("conv-missing", fake_backend)
    assert result is None


def test_recover_from_remote_round_trip(tmp_path: Path, fake_backend: FakeBackend):
    """Remote events.jsonl is downloaded and used to reconstruct messages."""
    from gptme.message import Message

    logdir = tmp_path / "local"
    logdir.mkdir()

    msg = Message(role="user", content="hello from recovery")
    seq = eventlog.sequence_number(logdir)
    event = eventlog.build_message_append_event(seq, msg)
    eventlog.append_event(logdir, event)

    # Upload the local event log to the fake backend
    local_events_path = logdir / eventlog.EVENT_LOG_NAME
    fake_backend.upload(_event_log_key("conv-recover"), local_events_path)

    messages = recover_from_remote("conv-recover", fake_backend)
    assert messages is not None
    assert len(messages) == 1
    assert messages[0]["content"] == "hello from recovery"
    assert messages[0]["role"] == "user"


def test_recover_from_remote_branch(tmp_path: Path, fake_backend: FakeBackend):
    """Recovery reads the requested branch-specific remote object."""
    from gptme.message import Message

    logdir = tmp_path / "branch"
    logdir.mkdir()
    eventlog.append_event(
        logdir,
        eventlog.build_message_append_event(
            1, Message(role="user", content="branch message")
        ),
    )
    fake_backend.upload(
        _event_log_key("conv-recover", "experiment"),
        logdir / eventlog.EVENT_LOG_NAME,
    )

    messages = recover_from_remote("conv-recover", fake_backend, "experiment")
    assert messages is not None
    assert messages[0]["content"] == "branch message"


def test_recover_from_remote_with_checkpoint(tmp_path: Path, fake_backend: FakeBackend):
    """Recovery works even when the remote log contains checkpoint events."""
    from gptme.message import Message

    logdir = tmp_path / "ckpt"
    logdir.mkdir()

    msgs = [Message(role="user", content=f"msg {i}") for i in range(3)]
    for _i, msg in enumerate(msgs):
        seq = eventlog.sequence_number(logdir)
        event = eventlog.build_message_append_event(seq, msg)
        eventlog.append_event(logdir, event)

    # Write a checkpoint
    seq = eventlog.sequence_number(logdir)
    eventlog.write_checkpoint(logdir, seq, [m.to_dict() for m in msgs])

    # Append one more message after the checkpoint
    extra = Message(role="assistant", content="after checkpoint")
    seq = eventlog.sequence_number(logdir)
    eventlog.append_event(logdir, eventlog.build_message_append_event(seq, extra))

    local_path = logdir / eventlog.EVENT_LOG_NAME
    fake_backend.upload(_event_log_key("conv-ckpt"), local_path)

    recovered = recover_from_remote("conv-ckpt", fake_backend)
    assert recovered is not None
    assert len(recovered) == 4
    assert recovered[-1]["content"] == "after checkpoint"


def test_recover_from_remote_handles_download_failure(caplog):
    """Returns None (no crash) when download raises unexpectedly."""
    import logging

    class BrokenBackend:
        def download(self, key: str, dest: Path) -> bool:
            raise RuntimeError("network error")

        def upload(self, key: str, local_path: Path) -> None:
            pass

    with caplog.at_level(logging.WARNING, logger="gptme.logmanager.replication"):
        result = recover_from_remote("conv-broken", BrokenBackend())

    assert result is None
    assert any("download failed" in r.message for r in caplog.records)
