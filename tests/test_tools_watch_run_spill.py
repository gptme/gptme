"""Run-watch previews must not destroy recoverable output."""

import json
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from gptme.tools import shell_background as background
from gptme.tools import watch as watch_module


def run_watch(payload: bytes, logdir: Path | None) -> watch_module.Watch:
    with subprocess.Popen(
        [sys.executable, "-c", f"import os; os.write(1, {payload!r})"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as proc:
        watch = watch_module.Watch(
            id="w-spill",
            kind="run",
            description="capture test",
            created=time.time(),
            deadline=time.time() + 10,
            proc=proc,
            logdir=logdir,
        )
        watch_module._poll_run(watch, proc)
        assert proc.returncode == 0
        assert watch.fired
        return watch


def test_run_retains_prefix_before_preview(tmp_path: Path) -> None:
    payload = b"PREFIX-TO-RECOVER\n" + b"middle\n" * 2000 + b"FINAL-LINE\n"
    watch = run_watch(payload, tmp_path)
    files = list(tmp_path.glob("tool-outputs/shell-jobs/*.stdout.live"))
    assert len(files) == 1, "watch discarded output instead of retaining a spool"
    assert files[0].read_bytes() == payload
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    event = watch.events[-1]
    assert str(files[0]) in event
    assert "FINAL-LINE" in event
    assert "PREFIX-TO-RECOVER" not in event
    assert len(event) < 2000
    receipt = json.loads(
        next(tmp_path.glob("tool-outputs/shell-jobs/*.receipt.json")).read_text()
    )
    assert receipt["capture_state"] == "complete"
    assert receipt["stdout"]["raw_observed_bytes"] == len(payload)


def test_run_without_logdir_reports_loss() -> None:
    watch = run_watch(b"prefix\n" + b"line\n" * 1000, None)
    assert "not retained" in watch.events[-1]
    assert "Full output" not in watch.events[-1]


def test_run_quota_reports_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(background, "_SPOOL_STREAM_QUOTA", 256)
    watch = run_watch(b"line\n" * 1000, tmp_path)
    assert "Partial output" in watch.events[-1]
    receipt = json.loads(
        next(tmp_path.glob("tool-outputs/shell-jobs/*.receipt.json")).read_text()
    )
    assert receipt["capture_state"] == "partial"
    assert (
        next(tmp_path.glob("tool-outputs/shell-jobs/*.stdout.live")).stat().st_size
        <= 256
    )


def test_run_conversations_and_reused_ids_do_not_collide(tmp_path: Path) -> None:
    for directory in (tmp_path / "a", tmp_path / "a", tmp_path / "b"):
        run_watch(b"line\n" * 1000, directory)
    assert (
        len(list((tmp_path / "a").glob("tool-outputs/shell-jobs/*.stdout.live"))) == 2
    )
    assert (
        len(list((tmp_path / "b").glob("tool-outputs/shell-jobs/*.stdout.live"))) == 1
    )


def test_run_sanitizes_before_tail_cut_and_storage(tmp_path: Path) -> None:
    secret = "sk-" + "Ab7xQ9mZ2nR5vT8k" * 80
    payload = (f"\x1b[31mAPI_KEY={secret}\x1b[0m\n" + "safe line\n" * 300).encode()
    watch = run_watch(payload, tmp_path)
    stored = next(tmp_path.glob("tool-outputs/shell-jobs/*.stdout.live")).read_text()
    assert secret not in stored
    assert "Ab7xQ9mZ2nR5vT8k" not in watch.events[-1]
    assert "\x1b" not in stored
    assert "safe line" in stored


def test_run_failed_storage_reports_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(background, "open_private_output_file", fail)
    watch = run_watch(b"line\n" * 1000, tmp_path)
    assert "not retained" in watch.events[-1]
    assert "Full output" not in watch.events[-1]


@pytest.mark.parametrize("finish", ["cancel", "timeout"])
def test_run_interruption_finalizes_partial_capture(
    tmp_path: Path, finish: str
) -> None:
    import threading

    with subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os,time; os.write(1,b'captured\\n'); time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    ) as proc:
        watch = watch_module.Watch(
            id="w-stop",
            kind="run",
            description="interrupt",
            created=time.time(),
            logdir=tmp_path,
            proc=proc,
            deadline=time.time() + 0.5 if finish == "timeout" else None,
        )
        worker = threading.Thread(target=watch_module._poll_run, args=(watch, proc))
        worker.start()
        if finish == "cancel":
            # Wait for capture creation rather than racing process startup.
            deadline = time.monotonic() + 5
            while not list(tmp_path.glob("tool-outputs/shell-jobs/*.stdout.live")):
                assert time.monotonic() < deadline
                time.sleep(0.01)
            watch.cancelled = True
            watch.cancel_event.set()
        worker.join(timeout=8)
        assert not worker.is_alive()
        assert proc.poll() is not None
        receipt = json.loads(
            next(tmp_path.glob("tool-outputs/shell-jobs/*.receipt.json")).read_text()
        )
        assert receipt["capture_state"] == "partial"
        assert ("cancelled" if finish == "cancel" else "deadline") in receipt[
            "partial_reasons"
        ]
        if finish == "cancel":
            assert not watch.fired
        else:
            assert watch.expired
            assert "Partial output" in watch.events[-1]


def test_run_worker_inherits_chat_redaction_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gptme.config import ChatConfig, Config, UserConfig, _config_var

    monkeypatch.delenv("GPTME_SHELL_REDACT_SECRETS", raising=False)
    monkeypatch.delenv("SHELL_REDACT_SECRETS", raising=False)
    user = UserConfig(env={"SHELL_REDACT_SECRETS": "0"})
    monkeypatch.setattr("gptme.config.core.Config", lambda: Config(user=user))
    token = _config_var.set(
        Config(user=user, chat=ChatConfig(env={"SHELL_REDACT_SECRETS": "1"}))
    )
    secret = "Zq8x7WmK2pL9vR4tY6uN3bH5jC1"
    with subprocess.Popen(
        [sys.executable, "-c", f"print('API_SECRET={secret}')"],
        stdout=subprocess.PIPE,
        text=True,
    ) as proc:
        watch = watch_module.Watch(
            id="w-context",
            kind="run",
            description="context",
            created=time.time(),
            logdir=tmp_path,
            proc=proc,
        )
        try:
            try:
                watch_module._spawn(watch, watch_module._poll_run, proc)
            finally:
                _config_var.reset(token)
            assert watch.thread is not None
            watch.thread.join(timeout=10)
            assert not watch.thread.is_alive()
            assert watch.fired
            saved = next(
                tmp_path.glob("tool-outputs/shell-jobs/*.stdout.live")
            ).read_text()
            assert "API_SECRET=" in saved
            assert secret not in saved
            assert secret not in watch.events[-1]
        finally:
            watch.cancelled = True
            watch.cancel_event.set()
            watch_module._kill_proc(proc)
            if watch.thread is not None:
                watch.thread.join(timeout=5)
            with watch_module._watches_lock:
                watch_module._watches.pop(watch.id, None)
            watch_module.take_queued_watch_events(tmp_path)


def test_run_capture_failure_is_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args, **kwargs):
        raise OSError("write failure")

    monkeypatch.setattr(background.BackgroundJob, "_write_spool_payload", fail)
    watch = run_watch(b"line\n" * 1000, tmp_path)
    assert "Partial output" in watch.events[-1]
    receipt = json.loads(
        next(tmp_path.glob("tool-outputs/shell-jobs/*.receipt.json")).read_text()
    )
    assert "write_error" in receipt["partial_reasons"]


def test_run_bounds_large_incomplete_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force eviction and a sanitation-window loss with a small fixture; both
    # limits are shared shell invariants rather than new watch-specific knobs.
    monkeypatch.setattr(background, "_MAX_BUFFER_SIZE", 2048)
    monkeypatch.setattr(background, "_SPOOL_PENDING_MAX", 1024)
    secret_line = b"API_SECRET=" + b"aB7z" * 2000
    watch = run_watch(secret_line + b"\nFINAL-LINE\n", tmp_path)
    event = watch.events[-1]
    assert "aB7z" not in event
    assert "FINAL-LINE" in event
    assert "Partial output" in event
    receipt = json.loads(
        next(tmp_path.glob("tool-outputs/shell-jobs/*.receipt.json")).read_text()
    )
    assert "sanitation_window" in receipt["partial_reasons"]
    assert receipt["stdout"]["raw_discarded_bytes"] > 0


def test_run_cutoff_keeps_incomplete_assignment_out_of_spool(tmp_path: Path) -> None:
    import threading

    with subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os,time; os.write(1,b'API_SECRET=short'); time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    ) as proc:
        watch = watch_module.Watch(
            id="w-cutoff",
            kind="run",
            description="cutoff",
            created=time.time(),
            deadline=time.time() + 0.5,
            logdir=tmp_path,
            proc=proc,
        )
        worker = threading.Thread(target=watch_module._poll_run, args=(watch, proc))
        worker.start()
        worker.join(timeout=8)
        assert not worker.is_alive()
        saved = next(tmp_path.glob("tool-outputs/shell-jobs/*.stdout.live")).read_text()
        assert "short" not in saved
        assert "short" not in watch.events[-1]
        receipt = json.loads(
            next(tmp_path.glob("tool-outputs/shell-jobs/*.receipt.json")).read_text()
        )
        assert receipt["capture_state"] == "partial"
