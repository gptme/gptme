"""Regression tests for direct-job spool review findings (gptme/gptme#4826)."""

import json
import os
import queue as queue_mod
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from gptme.tools.shell_background import (
    _SPOOL_MAX_BYTES,
    _SPOOL_PENDING_MAX,
    BackgroundJob,
    _write_job_log,
    complete_background_job,
    format_job_output,
    register_background_job,
    reset_background_jobs,
    start_background_job,
)
from gptme.util.output_storage import open_private_output_file


@pytest.mark.parametrize("is_stdout", [True, False])
@pytest.mark.parametrize("ending", ["", "\nSAFE-NEXT-LINE\n"])
@pytest.mark.parametrize("prefix", ["", "normal line\n"])
def test_overflow_discards_secret_suffix_until_newline(
    tmp_path, is_stdout: bool, ending: str, prefix: str
) -> None:
    job = BackgroundJob(1, "synthetic", Mock(), 0)
    stream = open_private_output_file(tmp_path, "shell-jobs", "overflow.live")
    if is_stdout:
        job._stdout_spool = stream
    else:
        job._stderr_spool = stream
    try:
        job._spool_text(
            is_stdout, prefix + "API_SECRET=" + "x" * _SPOOL_PENDING_MAX, final=False
        )
        job._spool_text(is_stdout, "secret-suffix" + ending, final=False)
        job._spool_text(is_stdout, "", final=True)
        saved = stream.path.read_text()
        assert "secret-suffix" not in saved
        assert "pending sanitation window exceeded" in saved
        assert ("SAFE-NEXT-LINE" in saved) == bool(ending)
    finally:
        stream.close()


@pytest.fixture
def direct_job(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gptme.tools.shell_background._current_log_dir", lambda: tmp_path
    )
    script = "import sys; sys.stderr.write('ERROR-BEGIN\\n' + 'error line\\n' * 110000 + 'ERROR-END\\n'); sys.exit(2)"
    job = start_background_job(
        f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
    )
    try:
        assert job.process.wait(timeout=15) == 2
        assert job._reader_thread is not None
        job._reader_thread.join(timeout=5)
        assert not job._reader_thread.is_alive()
        yield job
    finally:
        reset_background_jobs()


def test_preview_names_both_stream_files(direct_job) -> None:
    preview = format_job_output(direct_job, *direct_job.get_output(), complete=True)
    assert direct_job._stderr_spool is not None
    stderr_path = direct_job._stderr_spool.path
    assert f"stderr: `{stderr_path}`" in preview
    assert f"stdout: `{direct_job.log_path}`" in preview
    assert "ERROR-BEGIN" in stderr_path.read_text()
    assert "ERROR-END" in stderr_path.read_text()
    assert direct_job._stderr_buffer_start > 0


def test_reader_closes_files_while_job_remains_reachable(direct_job) -> None:
    for stream in (direct_job._stdout_spool, direct_job._stderr_spool):
        assert stream is not None
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(stream._fd)
        assert stream.path.exists()
    # Rendering a completed job must not reopen or rewrite its closed spools.
    assert "Full output" in format_job_output(
        direct_job, *direct_job.get_output(), complete=True
    )


def test_final_snapshot_closes_descriptor_and_stays_readable(tmp_path) -> None:
    job = BackgroundJob(1, "promoted", Mock(), time.time(), log_dir=tmp_path)
    complete_background_job(job, "line\n" * 3000, "error\n")
    assert job._snapshot_stream is not None
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(job._snapshot_stream._fd)
    assert job.log_path is not None
    saved = job.log_path.read_text()
    assert "error" in saved
    assert _write_job_log(job, "replacement", "", final=False) == job.log_path
    assert job.log_path.read_text() == saved


@pytest.mark.parametrize("final", [False, True])
def test_partial_spool_never_claims_full_output(tmp_path, final: bool) -> None:
    job = BackgroundJob(1, "synthetic", Mock(), 0, log_dir=tmp_path)
    stream = open_private_output_file(tmp_path, "shell-jobs", "partial.live")
    job._stdout_spool = stream
    job.log_path = stream.path
    job._spool_from_reader = True
    try:
        job._spool_text(True, "x" * (_SPOOL_PENDING_MAX + 1), final=False)
        job._spool_text(True, "", final=True)
        assert job._stdout_buffer_start == 0
        if final:
            _write_job_log(job, "", "", final=True)
        preview = format_job_output(job, "", "", complete=True)
        assert "Full output" not in preview
        assert "Partial output" in preview
    finally:
        stream.close()


@pytest.mark.parametrize("operation", ["write", "fsync"])
@pytest.mark.parametrize("evicted", [False, True])
def test_failed_final_snapshot_does_not_break_later_output(
    tmp_path, monkeypatch, operation: str, evicted: bool
) -> None:
    job = BackgroundJob(1, "promoted", Mock(), time.time(), log_dir=tmp_path)
    real_operation = getattr(os, operation)

    def fail(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(os, operation, fail)
    complete_background_job(
        job, "retained line\n" * (100000 if evicted else 3000), "error\n"
    )
    assert not job._final_log_written
    # Repeated disk failures must not turn into a closed-file ValueError.
    preview = format_job_output(job, *job.get_output(), complete=True)
    assert "retained line" in preview
    assert "Full output" not in preview
    monkeypatch.setattr(os, operation, real_operation)
    preview = format_job_output(job, *job.get_output(), complete=True)
    assert ("Full output" in preview) != evicted
    assert ("were not retained" in preview) == evicted
    assert job.log_path is not None
    assert "retained line" in job.log_path.read_text()
    assert job._snapshot_stream is not None
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(job._snapshot_stream._fd)
    saved = job.log_path.read_text()
    # Completion retries must finalize once, not rewrite on every preview.
    assert _write_job_log(job, "replacement", "", final=False) == job.log_path
    assert job.log_path.read_text() == saved


def test_registered_pipe_process_preserves_supplied_final_output(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "gptme.tools.shell_background._current_log_dir", lambda: tmp_path
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "print('BEGIN'); print('line\\n' * 300000); print('END')",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        job = register_background_job("external reader", process)
        stdout, stderr = process.communicate(timeout=15)
        complete_background_job(job, stdout.decode(), stderr.decode())
        assert job.log_path is not None
        saved = job.log_path.read_text()
        assert "BEGIN" in saved
        assert "END" in saved
        assert job._stdout_buffer_start > 0
        assert "Full output" in format_job_output(job, *job.get_output(), complete=True)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()
        reset_background_jobs()


def test_spool_open_failure_also_tolerates_cleanup_fsync_failure(
    tmp_path, monkeypatch
) -> None:
    process = Mock()
    process.stdout.fileno.return_value = 10
    job = BackgroundJob(1, "synthetic", process, 0)
    job.log_dir = tmp_path
    stream = open_private_output_file(tmp_path, "shell-jobs", "cleanup.live")
    opens = 0

    def open_stream(*args):
        nonlocal opens
        opens += 1
        if opens == 1:
            return stream
        raise OSError("cannot create stderr spool")

    def fail_fsync(fd):
        raise OSError("cannot flush cleanup")

    monkeypatch.setattr(
        "gptme.tools.shell_background.open_private_output_file", open_stream
    )
    monkeypatch.setattr(os, "fsync", fail_fsync)
    job._open_spools()
    assert not job._spool_from_reader
    assert job._stdout_spool is None
    assert job._stderr_spool is None
    assert stream._closed
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(stream._fd)


def test_live_spool_with_pending_line_does_not_claim_full_output(tmp_path) -> None:
    job = BackgroundJob(1, "synthetic", Mock(), 0, log_dir=tmp_path)
    stream = open_private_output_file(tmp_path, "shell-jobs", "pending.live")
    job._stdout_spool = stream
    job.log_path = stream.path
    job._spool_from_reader = True
    try:
        job._spool_text(True, "x" * 10000, final=False)
        preview = format_job_output(job, "x" * 10000, "")
        assert "Full output" not in preview
        assert "pending sanitation" in preview
        job._spool_text(True, "", final=True)
        assert "Full output" in format_job_output(job, "x" * 10000, "", complete=True)
    finally:
        stream.close()


@pytest.mark.parametrize("preview_while_running", [False, True])
@pytest.mark.parametrize("evicted", [False, True])
def test_fallback_snapshot_closes_without_hiding_loss(
    tmp_path, monkeypatch, preview_while_running: bool, evicted: bool
) -> None:
    import threading

    from gptme.tools import shell_background as bg

    process = Mock()
    process.stdout.fileno.return_value = 10
    process.stderr = None
    process.poll.return_value = 0
    job = BackgroundJob(1, "fallback", process, time.time(), log_dir=tmp_path)
    real_open = bg.open_private_output_file

    def open_stream(log_dir, category, name):
        if name.endswith(".stderr.live"):
            raise OSError("temporary stderr spool failure")
        return real_open(log_dir, category, name)

    monkeypatch.setattr(bg, "open_private_output_file", open_stream)
    job._open_spools()
    assert not job._spool_from_reader
    job.stdout_buffer = ["retained line\n" * 3000]
    job._stdout_buffer_start = 123 if evicted else 0
    # Keep a live thread reference, as completion can render before it returns.
    job._reader_thread = threading.current_thread()
    if preview_while_running:
        format_job_output(job, *job.get_output())
        assert job._snapshot_stream is not None
        assert not job._snapshot_stream._closed
    monkeypatch.setattr(bg, "_wait_readable", lambda fds, timeout: fds)
    monkeypatch.setattr(os, "read", lambda fd, size: b"")
    try:
        job._read_output()
        if preview_while_running:
            assert job._snapshot_stream is not None
            assert job._snapshot_stream._closed
        for _ in range(2):
            preview = bg._completion_message(job).content
            assert ("were not retained" in preview) == evicted
            assert ("Full output" in preview) != evicted
            assert job._snapshot_stream is not None
            assert job._snapshot_stream._closed
            assert job.log_path is not None
            assert "retained line" in job.log_path.read_text()
        assert len(list(tmp_path.glob("tool-outputs/shell-jobs/*.log"))) == 1
    finally:
        if job._snapshot_stream is not None:
            job._snapshot_stream.close()


@pytest.mark.parametrize("is_stdout", [True, False])
def test_reader_spool_inherits_chat_redaction_policy(tmp_path, monkeypatch, is_stdout):
    from gptme.config import ChatConfig, Config, UserConfig, _config_var

    monkeypatch.delenv("GPTME_SHELL_REDACT_SECRETS", raising=False)
    monkeypatch.delenv("SHELL_REDACT_SECRETS", raising=False)
    user = UserConfig(env={"SHELL_REDACT_SECRETS": "0"})
    monkeypatch.setattr("gptme.config.core.Config", lambda: Config(user=user))
    config = Config(user=user, chat=ChatConfig(env={"SHELL_REDACT_SECRETS": "1"}))
    token = _config_var.set(config)
    stream_name = "stdout" if is_stdout else "stderr"
    secret = "Zq8" + "x7WmK2pL9vR4tY6uN3bH5jC1"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"import sys; sys.{stream_name}.write('API_SECRET={secret}\\n')",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    job = BackgroundJob(1, "synthetic", process, time.time(), log_dir=tmp_path)
    try:
        job.start_reader()
        assert process.wait(timeout=15) == 0
        assert job._reader_thread is not None
        job._reader_thread.join(timeout=5)
        assert not job._reader_thread.is_alive()
        path = job.log_path if is_stdout else job.stderr_log_path
        assert path is not None
        saved = path.read_text()
        assert "API_SECRET=" in saved
        assert secret not in saved
    finally:
        _config_var.reset(token)
        if process.poll() is None:
            process.kill()
        process.communicate()


def test_writer_queue_full_does_not_block_reader(tmp_path) -> None:
    job = BackgroundJob(1, "synthetic", Mock(), 0, log_dir=tmp_path)
    stream = open_private_output_file(tmp_path, "shell-jobs", "queued.live")
    job._stdout_spool = stream
    job._spool_from_reader = True
    job._spool_queue = queue_mod.Queue(maxsize=1)
    job._spool_queue.put_nowait((stream, True, b"held\n"))
    try:
        t0 = time.monotonic()
        job._commit_spool_bytes(True, b"dropped because the writer is busy\n")
        assert time.monotonic() - t0 < 0.2
        assert job._capture_partial
        assert "queue_full" in job._partial_reasons
        assert job._stdout_discarded_bytes > 0
        assert not job._spool_persist_stdout
    finally:
        stream.close()


def test_stream_quota_stops_persistence_and_marks_partial(
    tmp_path, monkeypatch
) -> None:
    import gptme.tools.shell_background as bg

    monkeypatch.setattr(bg, "_SPOOL_STREAM_QUOTA", 64)
    job = BackgroundJob(1, "synthetic", Mock(), 0, log_dir=tmp_path)
    stream = open_private_output_file(tmp_path, "shell-jobs", "quota.live")
    job._stdout_spool = stream
    try:
        job._spool_text(True, "keep-me\n" + ("x" * 200) + "\n", final=False)
        saved = stream.path.read_bytes()
        assert b"keep-me" in saved
        assert b"retained-stream quota exceeded" in saved
        assert job._capture_partial
        assert "quota" in job._partial_reasons
        assert len(saved) <= 64
        assert job._stdout_discarded_bytes > 0
        job._spool_text(True, "after-quota\n", final=False)
        assert b"after-quota" not in stream.path.read_bytes()
    finally:
        stream.close()


def test_receipt_published_after_direct_job_finalize(direct_job) -> None:
    assert direct_job.receipt_path is not None
    receipt = json.loads(direct_job.receipt_path.read_text())
    assert receipt["job_id"] == direct_job.id
    assert receipt["capture_state"] in {"complete", "partial"}
    assert receipt["exit_code"] == 2
    assert receipt["stderr"]["committed_bytes"] > 0
    assert receipt["stderr"]["path"] == str(direct_job.stderr_log_path)
    assert receipt["stdout"]["path"] == str(direct_job.log_path)
    assert "ERROR-BEGIN" in Path(receipt["stderr"]["path"]).read_text()


def test_byte_cursors_are_independent_of_ram_offsets(direct_job) -> None:
    assert direct_job._stderr_buffer_start > 0
    first_out, first_err = direct_job.get_output(incremental=True)
    assert "ERROR-BEGIN" in first_err or "ERROR-END" in first_err
    assert isinstance(first_out, str)
    assert direct_job._stderr_byte_cursor == direct_job._stderr_committed_bytes
    assert direct_job._stderr_byte_cursor != direct_job._stderr_read_offset
    second_out, second_err = direct_job.get_output(incremental=True)
    assert second_out == ""
    assert second_err == ""


def test_conversation_quota_refuses_snapshot_fallback(tmp_path, monkeypatch) -> None:
    import gptme.tools.shell_background as bg

    monkeypatch.setattr(bg, "_SPOOL_CONVERSATION_QUOTA", 1)
    bg._conversation_spool_bytes[None] = 1
    bg._conversation_spool_seeded.add(None)
    process = Mock()
    process.stdout.fileno.return_value = 10
    process.stderr = None
    job = BackgroundJob(1, "quota-refused", process, time.time(), log_dir=tmp_path)
    try:
        job._open_spools()
        assert job._spool_quota_refused
        assert not job._spool_from_reader
        job.stdout_buffer = ["retained line\n" * 50]
        path = _write_job_log(job, *job.get_output(), final=True, complete=True)
        assert path is None
        assert list(tmp_path.glob("tool-outputs/shell-jobs/*")) == []
    finally:
        reset_background_jobs()


def test_conversation_quota_seeds_from_existing_files(tmp_path, monkeypatch) -> None:
    import gptme.tools.shell_background as bg

    jobs_dir = tmp_path / "tool-outputs" / "shell-jobs"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "old.stdout.live").write_bytes(b"x" * 1000)
    monkeypatch.setattr(bg, "_SPOOL_CONVERSATION_QUOTA", 1000)
    process = Mock()
    process.stdout.fileno.return_value = 10
    job = BackgroundJob(1, "seed-disk", process, time.time(), log_dir=tmp_path)
    try:
        job._open_spools()
        assert job._spool_quota_refused
        assert not job._spool_from_reader
        assert "conversation_quota" in job._partial_reasons
    finally:
        reset_background_jobs()


def test_queue_full_unreserves_queued_bytes(tmp_path) -> None:
    job = BackgroundJob(1, "synthetic", Mock(), 0, log_dir=tmp_path)
    stream = open_private_output_file(tmp_path, "shell-jobs", "queued.live")
    job._stdout_spool = stream
    job._spool_from_reader = True
    job._spool_queue = queue_mod.Queue(maxsize=1)
    job._spool_queue.put_nowait((stream, True, b"held\n"))
    try:
        job._commit_spool_bytes(True, b"dropped because the writer is busy\n")
        assert job._spool_queued_bytes == 0
        assert "queue_full" in job._partial_reasons
    finally:
        stream.close()


def test_finalize_stop_signal_closes_writer_and_publishes(tmp_path) -> None:
    process = Mock()
    process.stdout.fileno.return_value = 10
    process.stderr = None
    process.returncode = 0
    job = BackgroundJob(1, "writer-stop", process, time.time(), log_dir=tmp_path)
    try:
        job._open_spools()
        assert job._spool_writer_thread is not None
        job._finalize_spools()
        assert not job._spool_writer_thread.is_alive()
        assert job.receipt_path is not None
        assert job.receipt_path.is_file()
    finally:
        reset_background_jobs()


def test_failed_writer_waits_for_reader_before_publishing_receipt(
    tmp_path, monkeypatch
) -> None:
    import gptme.tools.shell_background as bg

    process = Mock(returncode=None)
    stream = open_private_output_file(tmp_path, "shell-jobs", "failed.live")
    job = BackgroundJob(1, "writer-error", process, 0, log_dir=tmp_path)
    job._stdout_spool = stream
    job.log_path = stream.path
    job._spool_from_reader = True
    job._spool_queue = queue_mod.Queue()
    job._commit_spool_bytes(True, b"before failure\n")

    def fail_write(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(job, "_write_spool_payload", fail_write)
    try:
        job._spool_writer_loop()
        assert stream._closed
        assert job.receipt_path is None
        # The pipe reader keeps draining after disk persistence fails.
        job._commit_spool_bytes(True, b"after failure\n")
        process.returncode = 7
        job._finalize_spools()
        assert job.receipt_path is not None
        receipt = json.loads(job.receipt_path.read_text())
        assert receipt["exit_code"] == 7
        assert receipt["capture_state"] == "partial"
        assert receipt["stdout"]["discarded_bytes"] >= len(b"after failure\n")
        assert receipt["partial_reasons"] == ["write_error"]
    finally:
        stream.close()
        bg._conversation_spool_bytes.clear()


def test_full_stop_queue_that_drains_does_not_report_timeout(tmp_path) -> None:
    import threading

    process = Mock(returncode=0)
    stream = open_private_output_file(tmp_path, "shell-jobs", "draining.live")
    job = BackgroundJob(1, "full-stop-queue", process, 0, log_dir=tmp_path)
    job._stdout_spool = stream
    job.log_path = stream.path
    job._spool_from_reader = True
    job._spool_queue = queue_mod.Queue(maxsize=1)
    job._commit_spool_bytes(True, b"retained\n")

    # Model a writer scheduled only when finalize reaches join: the stop
    # sentinel cannot fit, but the stop event reliably ends the drained loop.
    writer = Mock()
    writer.is_alive.return_value = False

    def drain_on_join(*, timeout):
        thread = threading.Thread(target=job._spool_writer_loop)
        thread.start()
        thread.join(timeout=timeout)
        assert not thread.is_alive()

    writer.join.side_effect = drain_on_join
    job._spool_writer_thread = writer
    try:
        job._finalize_spools()
        assert stream.path.read_bytes() == b"retained\n"
        assert job.receipt_path is not None
        receipt = json.loads(job.receipt_path.read_text())
        assert receipt["capture_state"] == "complete"
        assert receipt["partial_reasons"] == []
        assert receipt["stdout"]["committed_bytes"] == len(b"retained\n")
    finally:
        stream.close()


def test_receipt_labels_representation_counts_and_unknown_raw_counts(
    direct_job,
) -> None:
    assert direct_job.receipt_path is not None
    receipt = json.loads(direct_job.receipt_path.read_text())
    assert receipt["byte_count_basis"] == "sanitized_utf8_representation"
    for name in ("stdout", "stderr"):
        assert receipt[name]["raw_observed_bytes"] is None
        assert receipt[name]["raw_accepted_bytes"] is None
        assert receipt[name]["raw_pending_bytes"] is None
        assert receipt[name]["raw_discarded_bytes"] is None


def test_writer_failure_counts_unwritten_and_drained_bytes_as_discarded(
    tmp_path, monkeypatch
) -> None:
    import gptme.tools.shell_background as bg

    stream = open_private_output_file(tmp_path, "shell-jobs", "lost.live")
    job = BackgroundJob(1, "writer-lost", Mock(returncode=0), 0, log_dir=tmp_path)
    job._stdout_spool = stream
    job.log_path = stream.path
    job._spool_from_reader = True
    job._spool_queue = queue_mod.Queue()
    job._commit_spool_bytes(True, b"before failure\n")
    job._commit_spool_bytes(True, b"drained leftover\n")

    def fail_write(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(job, "_write_spool_payload", fail_write)
    try:
        job._spool_writer_loop()
        job._finalize_spools()
        assert job.receipt_path is not None
        receipt = json.loads(job.receipt_path.read_text())
        lost = len(b"before failure\n") + len(b"drained leftover\n")
        assert receipt["stdout"]["committed_bytes"] == 0
        assert receipt["stdout"]["discarded_bytes"] >= lost
    finally:
        stream.close()
        bg._conversation_spool_bytes.clear()


def test_incremental_read_continues_from_ram_after_persistence_stops(
    tmp_path, monkeypatch
) -> None:
    import gptme.tools.shell_background as bg

    monkeypatch.setattr(bg, "_SPOOL_STREAM_QUOTA", 64)
    process = Mock(returncode=None)
    process.stdout.fileno.return_value = 10
    stream = open_private_output_file(tmp_path, "shell-jobs", "late.live")
    job = BackgroundJob(1, "late-output", process, 0, log_dir=tmp_path)
    job._stdout_spool = stream
    job.log_path = stream.path
    job._spool_from_reader = True
    try:
        job._append_output(10, 10, b"keep-me\n" + b"x" * 200 + b"\n")
        assert not job._spool_persist_stdout
        first_out, _ = job.get_output(incremental=True)
        assert "keep-me" in first_out
        # Exactly one delivery of "keep-me" — committed bytes must not be
        # duplicated by the RAM tail (resume is set after the boundary chunk).
        assert first_out.count("keep-me") == 1
        # The unsaved tail of the boundary chunk (the x's after the quota cut)
        # must be served from RAM — previously, resume pointed past the whole
        # chunk and the tail was silently dropped.
        assert first_out.count("x") >= 185
        # Output after the quota stopped persistence must still be served.
        job._append_output(10, 10, b"BUILD FAILED\n")
        new_out, _ = job.get_output(incremental=True)
        assert new_out == "BUILD FAILED\n"
        again_out, _ = job.get_output(incremental=True)
        assert again_out == ""
        # A trailing partial line must not be replayed while nothing new arrived.
        job._append_output(10, 10, b"partial-no-newline")
        partial_out, _ = job.get_output(incremental=True)
        assert partial_out == "partial-no-newline"
        idle_out, _ = job.get_output(incremental=True)
        assert idle_out == ""
    finally:
        stream.close()
        bg._conversation_spool_bytes.clear()


def test_ram_resume_counts_characters_not_bytes_for_non_ascii(
    tmp_path, monkeypatch
) -> None:
    """A quota cut inside non-ASCII output must resume at the right character."""
    import gptme.tools.shell_background as bg

    monkeypatch.setattr(bg, "_SPOOL_STREAM_QUOTA", 256)
    process = Mock(returncode=None)
    process.stdout.fileno.return_value = 10
    stream = open_private_output_file(tmp_path, "shell-jobs", "utf8.live")
    job = BackgroundJob(1, "utf8-output", process, 0, log_dir=tmp_path)
    job._stdout_spool = stream
    job.log_path = stream.path
    job._spool_from_reader = True
    try:
        # 3-byte characters: bytes committed at the cut exceed characters.
        text = "é" * 200 + "\nFAILURE-LINE\n"
        job._append_output(10, 10, text.encode("utf-8"))
        assert not job._spool_persist_stdout
        resume = job._stdout_ram_resume
        assert resume is not None
        # Resume is a character offset into the decoded stream: it must not
        # pass the committed prefix and must land before the failure line.
        # 256-byte quota / 2-byte chars => at most ~128 characters committed.
        assert resume <= 130
        out, _ = job.get_output(incremental=True)
        assert "FAILURE-LINE" in out
    finally:
        stream.close()
        bg._conversation_spool_bytes.clear()


def test_close_spool_files_waits_for_cursor_reader(tmp_path) -> None:
    """Closing live spool fds is ordered after an in-flight cursor read."""
    process = Mock(returncode=None)
    stream = open_private_output_file(tmp_path, "shell-jobs", "close.live")
    job = BackgroundJob(1, "close-order", process, 0, log_dir=tmp_path)
    job._stdout_spool = stream
    done = threading.Event()

    def close_then_signal() -> None:
        job._close_spool_files()
        done.set()

    with job._cursor_lock:
        t = threading.Thread(target=close_then_signal)
        t.start()
        assert not done.wait(0.2)
        assert not stream._closed
    t.join(timeout=5)
    assert done.is_set()
    assert stream._closed
