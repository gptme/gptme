"""Tests for gptme/tools/shell_background.py — background job management.

Covers:
- BackgroundJob dataclass (buffer mgmt, lifecycle, thread-safe output)
- Module-level job registry (start, get, list, cleanup, reset)
- Command handlers (bg, jobs, output, kill)
"""

import re
import subprocess
import sys
import time

import pytest

from gptme.tools.shell_background import (
    _MAX_BUFFER_SIZE,
    BackgroundJob,
    cleanup_finished_jobs,
    execute_bg_command,
    execute_jobs_command,
    execute_kill_command,
    execute_output_command,
    get_background_job,
    list_background_jobs,
    reset_background_jobs,
    start_background_job,
)


@pytest.fixture(autouse=True)
def _clean_jobs():
    """Reset global job state before and after each test."""
    reset_background_jobs()
    yield
    reset_background_jobs()


# ---------------------------------------------------------------------------
# BackgroundJob dataclass — buffer management
# ---------------------------------------------------------------------------


class TestAppendToBuffer:
    """Test _append_to_buffer overflow protection."""

    def test_append_small(self):
        job = _make_job()
        job._append_to_buffer(job.stdout_buffer, "hello")
        assert job.stdout_buffer == ["hello"]

    def test_append_multiple(self):
        job = _make_job()
        job._append_to_buffer(job.stdout_buffer, "a")
        job._append_to_buffer(job.stdout_buffer, "b")
        assert job.stdout_buffer == ["a", "b"]

    def test_overflow_truncates_from_front(self):
        """When buffer exceeds _MAX_BUFFER_SIZE, oldest entries are dropped."""
        job = _make_job()
        # Fill with chunks that total exactly _MAX_BUFFER_SIZE
        chunk = "x" * (_MAX_BUFFER_SIZE // 2)
        job._append_to_buffer(job.stdout_buffer, chunk)
        job._append_to_buffer(job.stdout_buffer, chunk)
        # Buffer at limit — next append should evict the oldest
        job._append_to_buffer(job.stdout_buffer, "overflow")
        total = sum(len(s) for s in job.stdout_buffer)
        assert total <= _MAX_BUFFER_SIZE + len("overflow")
        # The first chunk should have been removed
        assert job.stdout_buffer[0] == chunk  # second chunk kept
        assert job.stdout_buffer[-1] == "overflow"
        assert len(job.stdout_buffer) == 2

    def test_single_entry_never_evicted(self):
        """A single buffer entry should never be removed even if oversized."""
        job = _make_job()
        huge = "x" * (_MAX_BUFFER_SIZE + 100)
        job._append_to_buffer(job.stdout_buffer, huge)
        assert len(job.stdout_buffer) == 1
        assert job.stdout_buffer[0] == huge


# ---------------------------------------------------------------------------
# BackgroundJob — get_output
# ---------------------------------------------------------------------------


class TestGetOutput:
    def test_empty_output(self):
        job = _make_job()
        stdout, stderr = job.get_output()
        assert stdout == ""
        assert stderr == ""

    def test_with_data(self):
        job = _make_job()
        job.stdout_buffer.extend(["hello ", "world"])
        job.stderr_buffer.append("err")
        stdout, stderr = job.get_output()
        assert stdout == "hello world"
        assert stderr == "err"


# ---------------------------------------------------------------------------
# BackgroundJob — lifecycle
# ---------------------------------------------------------------------------


class TestJobLifecycle:
    def test_is_running_for_live_process(self):
        job = start_background_job("sleep 60")
        assert job.is_running()
        job.kill()

    def test_is_running_for_finished_process(self):
        job = start_background_job("true")
        job.process.wait(timeout=5)
        # Allow reader thread to detect exit
        time.sleep(0.3)
        assert not job.is_running()

    def test_elapsed_time_increases(self):
        job = _make_job()
        t1 = job.elapsed_time()
        time.sleep(0.05)
        t2 = job.elapsed_time()
        assert t2 > t1

    def test_kill_terminates_process(self):
        job = start_background_job("sleep 60")
        assert job.is_running()
        job.kill()
        assert not job.is_running()
        assert job.process.returncode is not None

    def test_kill_already_finished(self):
        """Killing a finished job should not raise."""
        job = start_background_job("true")
        job.process.wait(timeout=5)
        time.sleep(0.3)
        job.kill()  # should not raise
        assert not job.is_running()


# ---------------------------------------------------------------------------
# BackgroundJob — output capture via reader thread
# ---------------------------------------------------------------------------


class TestOutputCapture:
    def test_stdout_captured(self):
        job = start_background_job("echo hello_stdout")
        job.process.wait(timeout=5)
        # Give reader thread time to flush
        time.sleep(0.5)
        stdout, _ = job.get_output()
        assert "hello_stdout" in stdout

    def test_stderr_captured(self):
        job = start_background_job("echo hello_stderr >&2")
        job.process.wait(timeout=5)
        time.sleep(0.5)
        _, stderr = job.get_output()
        assert "hello_stderr" in stderr

    def test_both_streams(self):
        job = start_background_job("echo out1; echo err1 >&2")
        job.process.wait(timeout=5)
        time.sleep(0.5)
        stdout, stderr = job.get_output()
        assert "out1" in stdout
        assert "err1" in stderr

    def test_multiline_output(self):
        job = start_background_job("seq 1 5")
        job.process.wait(timeout=5)
        time.sleep(0.5)
        stdout, _ = job.get_output()
        for n in range(1, 6):
            assert str(n) in stdout

    def test_large_output_buffered(self):
        """Generate output near buffer limit to test overflow handling."""
        # Generate ~100KB of output (well under 1MB limit)
        job = start_background_job(f"{sys.executable} -c \"print('A' * 100_000)\"")
        job.process.wait(timeout=10)
        time.sleep(0.5)
        stdout, _ = job.get_output()
        assert len(stdout) >= 100_000


# ---------------------------------------------------------------------------
# Module-level job registry
# ---------------------------------------------------------------------------


class TestJobRegistry:
    def test_start_assigns_sequential_ids(self):
        j1 = start_background_job("true")
        j2 = start_background_job("true")
        assert j2.id == j1.id + 1
        j1.process.wait(timeout=5)
        j2.process.wait(timeout=5)

    def test_get_existing(self):
        job = start_background_job("sleep 60")
        found = get_background_job(job.id)
        assert found is job
        job.kill()

    def test_get_nonexistent(self):
        assert get_background_job(9999) is None

    def test_list_returns_all(self):
        j1 = start_background_job("sleep 60")
        j2 = start_background_job("sleep 60")
        jobs = list_background_jobs()
        ids = {j.id for j in jobs}
        assert j1.id in ids
        assert j2.id in ids
        j1.kill()
        j2.kill()

    def test_list_empty(self):
        assert list_background_jobs() == []

    def test_cleanup_removes_finished(self):
        job = start_background_job("true")
        job.process.wait(timeout=5)
        time.sleep(0.3)
        cleanup_finished_jobs()
        assert get_background_job(job.id) is None

    def test_cleanup_keeps_running(self):
        job = start_background_job("sleep 60")
        cleanup_finished_jobs()
        assert get_background_job(job.id) is not None
        job.kill()

    def test_reset_kills_and_clears(self):
        j1 = start_background_job("sleep 60")
        j2 = start_background_job("sleep 60")
        reset_background_jobs()
        assert not j1.is_running()
        assert not j2.is_running()
        assert list_background_jobs() == []

    def test_reset_resets_id_counter(self):
        start_background_job("true").process.wait(timeout=5)
        reset_background_jobs()
        j = start_background_job("true")
        assert j.id == 1
        j.process.wait(timeout=5)


# ---------------------------------------------------------------------------
# Command handlers — execute_bg_command
# ---------------------------------------------------------------------------


class TestExecuteBgCommand:
    def test_empty_command(self):
        msgs = list(execute_bg_command(""))
        assert len(msgs) == 1
        assert "Usage" in msgs[0].content

    def test_whitespace_command(self):
        msgs = list(execute_bg_command("   "))
        assert len(msgs) == 1
        assert "Usage" in msgs[0].content

    def test_valid_command(self):
        msgs = list(execute_bg_command("echo test_bg"))
        assert len(msgs) == 1
        assert "Started background job" in msgs[0].content
        assert "#" in msgs[0].content

    def test_denylisted_command(self):
        """Commands on the deny list should be rejected."""
        msgs = list(execute_bg_command("rm -rf /"))
        assert len(msgs) == 1
        assert "denied" in msgs[0].content.lower()


# ---------------------------------------------------------------------------
# Command handlers — execute_jobs_command
# ---------------------------------------------------------------------------


class TestExecuteJobsCommand:
    def test_no_jobs(self):
        msgs = list(execute_jobs_command())
        assert len(msgs) == 1
        assert "No background jobs" in msgs[0].content

    def test_with_running_job(self):
        start_background_job("sleep 60")
        msgs = list(execute_jobs_command())
        assert len(msgs) == 1
        assert "Running" in msgs[0].content

    def test_format_short_elapsed(self):
        """Jobs running <60s show seconds."""
        start_background_job("sleep 60")
        msgs = list(execute_jobs_command())
        assert re.search(r"\(\d+\.\d+s\):", msgs[0].content), msgs[0].content

    def test_long_command_truncated(self):
        # Keep the job alive long enough to survive cleanup in execute_jobs_command().
        long_cmd = "sleep 60 # " + "x" * 100
        start_background_job(long_cmd)
        msgs = list(execute_jobs_command())
        assert "..." in msgs[0].content


# ---------------------------------------------------------------------------
# Command handlers — execute_output_command
# ---------------------------------------------------------------------------


class TestExecuteOutputCommand:
    def test_invalid_id_string(self):
        msgs = list(execute_output_command("abc"))
        assert len(msgs) == 1
        assert "Invalid job ID" in msgs[0].content

    def test_nonexistent_id(self):
        msgs = list(execute_output_command("999"))
        assert len(msgs) == 1
        assert "No job with ID" in msgs[0].content

    def test_running_job_output(self):
        job = start_background_job("sleep 60")
        msgs = list(execute_output_command(str(job.id)))
        assert len(msgs) == 1
        assert "Running" in msgs[0].content
        assert "No output yet" in msgs[0].content
        assert "stdout" not in msgs[0].content
        job.kill()

    def test_finished_job_output(self):
        job = start_background_job("echo done_marker")
        job.process.wait(timeout=5)
        time.sleep(0.5)
        msgs = list(execute_output_command(str(job.id)))
        assert len(msgs) == 1
        assert "done_marker" in msgs[0].content
        assert "Finished" in msgs[0].content

    def test_no_output_message(self):
        """A finished job with no output still shows the placeholder message."""
        job = start_background_job("true")
        job.process.wait(timeout=5)
        time.sleep(0.3)
        msgs = list(execute_output_command(str(job.id)))
        assert "Finished" in msgs[0].content
        assert "No output yet" in msgs[0].content

    def test_long_stdout_truncated(self):
        """Stdout longer than 8000 chars should be truncated."""
        # Generate >8000 chars
        job = start_background_job(f"{sys.executable} -c \"print('A' * 10000)\"")
        job.process.wait(timeout=10)
        time.sleep(0.5)
        msgs = list(execute_output_command(str(job.id)))
        assert "truncated" in msgs[0].content

    def test_long_stderr_truncated(self):
        """Stderr longer than 2000 chars should be truncated."""
        job = start_background_job(
            f"{sys.executable} -c \"import sys; sys.stderr.write('E' * 3000)\""
        )
        job.process.wait(timeout=10)
        time.sleep(0.5)
        msgs = list(execute_output_command(str(job.id)))
        assert "truncated" in msgs[0].content


# ---------------------------------------------------------------------------
# Command handlers — execute_kill_command
# ---------------------------------------------------------------------------


class TestExecuteKillCommand:
    def test_invalid_id_string(self):
        msgs = list(execute_kill_command("xyz"))
        assert len(msgs) == 1
        assert "Invalid job ID" in msgs[0].content

    def test_nonexistent_id(self):
        msgs = list(execute_kill_command("888"))
        assert len(msgs) == 1
        assert "No job with ID" in msgs[0].content

    def test_kill_running_job(self):
        job = start_background_job("sleep 60")
        msgs = list(execute_kill_command(str(job.id)))
        assert len(msgs) == 1
        assert "Terminated" in msgs[0].content
        assert not job.is_running()

    def test_kill_finished_job(self):
        job = start_background_job("true")
        job.process.wait(timeout=5)
        time.sleep(0.3)
        msgs = list(execute_kill_command(str(job.id)))
        assert "already finished" in msgs[0].content


# ---------------------------------------------------------------------------
# Thread-safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_start(self):
        """Multiple threads starting jobs concurrently should not collide."""
        import threading

        jobs: list[BackgroundJob] = []
        lock = threading.Lock()

        def _start():
            j = start_background_job("true")
            with lock:
                jobs.append(j)

        threads = [threading.Thread(target=_start) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # All 10 jobs should have unique IDs
        ids = {j.id for j in jobs}
        assert len(ids) == 10

        # Wait for all to finish
        for j in jobs:
            j.process.wait(timeout=5)

    def test_concurrent_get_output(self):
        """Reading output while writer thread is active should not crash."""
        job = start_background_job(
            f'{sys.executable} -c "import time; [print(i) or time.sleep(0.01) for i in range(50)]"'
        )
        # Poll output several times while the process is running
        results = []
        for _ in range(5):
            stdout, stderr = job.get_output()
            results.append(stdout)
            time.sleep(0.05)

        job.process.wait(timeout=10)
        time.sleep(0.5)
        final_stdout, _ = job.get_output()
        # Final output should contain all numbers
        assert "49" in final_stdout


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_process_crash_detected(self):
        """A job whose process exits with error should report finished."""
        job = start_background_job("exit 42")
        job.process.wait(timeout=5)
        time.sleep(0.3)
        assert not job.is_running()
        assert job.process.returncode == 42

    def test_binary_output_handled(self):
        """Non-UTF-8 output should be handled via errors='replace'."""
        job = start_background_job(
            f"{sys.executable} -c \"import sys; sys.stdout.buffer.write(b'\\xff\\xfe\\x00hello')\""
        )
        job.process.wait(timeout=5)
        time.sleep(0.5)
        stdout, _ = job.get_output()
        assert "hello" in stdout  # valid portion preserved

    def test_rapid_start_and_kill(self):
        """Starting and immediately killing should not hang or raise."""
        for _ in range(5):
            job = start_background_job("sleep 60")
            job.kill()
            assert not job.is_running()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job() -> BackgroundJob:
    """Create a BackgroundJob with a dummy no-op process for unit testing."""
    proc = subprocess.Popen(
        ["true"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    return BackgroundJob(
        id=0,
        command="true",
        process=proc,
        start_time=time.time(),
    )
