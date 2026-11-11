"""Regression tests for direct-job spool review findings (gptme/gptme#4826)."""

import os
import shlex
import subprocess
import sys
import time
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


def test_spool_quota_stops_persistence_keeps_draining(tmp_path, monkeypatch) -> None:
    """Quota exceeded closes both spools but reader keeps draining; child exits normally."""
    from gptme.tools import shell_background as bg

    # 1 KiB quota: triggers quickly without producing a large file.
    monkeypatch.setattr(bg, "_SPOOL_MAX_BYTES", 1024)
    # 4 MiB exceeds ordinary pipe capacity as well as the quota. A reader that
    # stops at quota must block the child rather than let this test pass.
    sentinel = "QUOTA-TEST-FINAL-TAIL"
    script = (
        "import sys; sys.stdout.write(('x' * 127 + '\\n') * 32768); "
        f"sys.stdout.write({sentinel!r} + '\\n'); sys.stdout.flush()"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    job = BackgroundJob(1, "quota-test", process, time.time(), log_dir=tmp_path)
    try:
        job.start_reader()
        assert process.wait(timeout=15) == 0
        assert job._reader_thread is not None
        job._reader_thread.join(timeout=5)
        assert not job._reader_thread.is_alive()

        # Quota must have been hit.
        assert job._capture_partial
        assert job._stdout_spool is None
        assert job._stderr_spool is None

        # The spool file exists and carries the quota-exceeded marker.
        assert job.log_path is not None
        saved = job.log_path.read_text()
        assert "quota exceeded" in saved

        # Retained bytes stay at or below the quota (marker bytes are excluded
        # from the total; the spool is closed on the first chunk that would
        # overflow, so actual payload <= quota).
        assert job._spool_bytes_total <= 1024

        # Preview must acknowledge partial capture; must not claim full output.
        preview = format_job_output(job, *job.get_output(), complete=True)
        assert "Full output" not in preview
        assert "Partial output" in preview

        # The in-memory RAM preview is unaffected: the stdout buffer still holds
        # the final tail of the output (the reader kept draining normally).
        stdout, _ = job.get_output()
        assert stdout.endswith(sentinel + "\n")
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()
        reset_background_jobs()


@pytest.mark.parametrize("is_stdout", [True, False])
@pytest.mark.parametrize("operation", ["write", "fsync"])
def test_quota_closes_both_spools_on_storage_error(
    tmp_path, monkeypatch, is_stdout: bool, operation: str
) -> None:
    from gptme.tools import shell_background as bg

    job = BackgroundJob(1, "quota-storage-error", Mock(), 0, log_dir=tmp_path)
    stdout = open_private_output_file(tmp_path, "shell-jobs", "quota.stdout.live")
    stderr = open_private_output_file(tmp_path, "shell-jobs", "quota.stderr.live")
    job._stdout_spool = stdout
    job._stderr_spool = stderr
    monkeypatch.setattr(bg, "_SPOOL_MAX_BYTES", 1)

    def fail(*args) -> None:
        raise OSError("simulated storage failure")

    monkeypatch.setattr(os, operation, fail)
    try:
        job._spool_text(is_stdout, "over quota\n", final=False)
        assert job._capture_partial
        assert job._stdout_spool is None
        assert job._stderr_spool is None
        for stream in (stdout, stderr):
            assert stream._closed
            with pytest.raises(OSError, match="Bad file descriptor"):
                os.fstat(stream._fd)
    finally:
        for stream in (stdout, stderr):
            try:
                stream.close()
            except OSError:
                pass


def test_spool_max_bytes_constant_is_32_mib() -> None:
    """Confirm the documented quota value so callers can rely on it."""
    assert _SPOOL_MAX_BYTES == 32 * 1024 * 1024
