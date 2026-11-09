"""Regression tests for direct-job spool review findings (gptme/gptme#4826)."""

import os
import shlex
import sys
import time
from unittest.mock import Mock

import pytest

from gptme.tools.shell_background import (
    _SPOOL_PENDING_MAX,
    BackgroundJob,
    _write_job_log,
    complete_background_job,
    format_job_output,
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
