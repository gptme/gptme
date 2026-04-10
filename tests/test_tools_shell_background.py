"""Tests for the shell_background tool — background job management.

Tests cover:
- BackgroundJob: lifecycle, output capture, buffer limits, kill/terminate
- Module functions: start, get, list, cleanup, reset (thread-safe)
- Command handlers: bg, jobs, output, kill (Generator-based)
- Edge cases: empty commands, invalid IDs, denylisted commands, finished jobs
- Buffer management: size limits, truncation from front
- Thread safety: concurrent job ID generation
"""

import os
import re
import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from gptme.tools.shell_background import (
    _MAX_BUFFER_SIZE,
    BackgroundJob,
    _get_next_job_id,
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

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_jobs():
    """Reset global job state before and after each test."""
    reset_background_jobs()
    yield
    reset_background_jobs()


def _collect(gen):
    """Collect all messages from a generator."""
    return list(gen)


# ── BackgroundJob dataclass ──────────────────────────────────────────────


class TestBackgroundJobCreation:
    """Test BackgroundJob construction and basic attributes."""

    def test_fields_initialized(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = 0
        job = BackgroundJob(id=1, command="echo hi", process=proc, start_time=100.0)
        assert job.id == 1
        assert job.command == "echo hi"
        assert job.stdout_buffer == []
        assert job.stderr_buffer == []

    def test_is_running_true(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = None
        job = BackgroundJob(id=1, command="sleep 10", process=proc, start_time=0)
        assert job.is_running() is True

    def test_is_running_false(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = 0
        job = BackgroundJob(id=1, command="true", process=proc, start_time=0)
        assert job.is_running() is False

    def test_elapsed_time(self):
        now = time.time()
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = 0
        job = BackgroundJob(id=1, command="true", process=proc, start_time=now - 5.0)
        assert 4.5 < job.elapsed_time() < 6.0


class TestBackgroundJobOutput:
    """Test output capture and buffer management."""

    def test_get_output_empty(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = 0
        job = BackgroundJob(id=1, command="true", process=proc, start_time=0)
        stdout, stderr = job.get_output()
        assert stdout == ""
        assert stderr == ""

    def test_get_output_with_data(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = 0
        job = BackgroundJob(id=1, command="echo hi", process=proc, start_time=0)
        job.stdout_buffer = ["hello", " ", "world"]
        job.stderr_buffer = ["warn"]
        stdout, stderr = job.get_output()
        assert stdout == "hello world"
        assert stderr == "warn"

    def test_append_to_buffer_within_limit(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = 0
        job = BackgroundJob(id=1, command="true", process=proc, start_time=0)
        job._append_to_buffer(job.stdout_buffer, "a" * 100)
        job._append_to_buffer(job.stdout_buffer, "b" * 100)
        assert len(job.stdout_buffer) == 2
        assert job.stdout_buffer[0] == "a" * 100
        assert job.stdout_buffer[1] == "b" * 100

    def test_append_to_buffer_truncates_from_front(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = 0
        job = BackgroundJob(id=1, command="true", process=proc, start_time=0)
        # Fill buffer to near limit
        chunk_size = _MAX_BUFFER_SIZE // 2
        job._append_to_buffer(job.stdout_buffer, "A" * chunk_size)
        job._append_to_buffer(job.stdout_buffer, "B" * chunk_size)
        # This should push total over limit, causing front truncation
        job._append_to_buffer(job.stdout_buffer, "C" * (chunk_size + 1))
        total = sum(len(s) for s in job.stdout_buffer)
        assert total <= _MAX_BUFFER_SIZE + chunk_size + 1  # at most one chunk over
        # First chunk should have been removed
        assert "A" * chunk_size not in job.stdout_buffer

    def test_buffer_limit_constant(self):
        assert _MAX_BUFFER_SIZE == 1024 * 1024  # 1MB


class TestBackgroundJobKill:
    """Test job termination."""

    def test_kill_terminates_process(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = None
        proc.wait.return_value = 0
        job = BackgroundJob(id=1, command="sleep 60", process=proc, start_time=0)
        job.kill()
        proc.terminate.assert_called_once()
        proc.wait.assert_called()

    def test_kill_escalates_to_sigkill(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired("cmd", 2), None]
        job = BackgroundJob(id=1, command="sleep 60", process=proc, start_time=0)
        job.kill()
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

    def test_kill_sets_stop_event(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = None
        proc.wait.return_value = 0
        job = BackgroundJob(id=1, command="sleep 60", process=proc, start_time=0)
        assert not job._stop_event.is_set()
        job.kill()
        assert job._stop_event.is_set()

    def test_kill_joins_reader_thread(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = None
        proc.wait.return_value = 0
        thread = MagicMock(spec=threading.Thread)
        thread.is_alive.return_value = True
        job = BackgroundJob(id=1, command="sleep 60", process=proc, start_time=0)
        job._reader_thread = thread
        job.kill()
        thread.join.assert_called_once_with(timeout=1.0)


# ── Integration: real subprocess ─────────────────────────────────────────


class TestBackgroundJobIntegration:
    """Test with real subprocesses for end-to-end behavior."""

    def test_capture_stdout(self):
        job = start_background_job("echo hello && echo world")
        # Wait for completion
        job.process.wait(timeout=5)
        time.sleep(0.3)  # let reader thread catch up
        stdout, stderr = job.get_output()
        assert "hello" in stdout
        assert "world" in stdout

    def test_capture_stderr(self):
        job = start_background_job("echo error >&2")
        job.process.wait(timeout=5)
        time.sleep(0.3)
        stdout, stderr = job.get_output()
        assert "error" in stderr

    def test_long_running_job(self):
        job = start_background_job("sleep 0.1 && echo done")
        assert job.is_running()
        job.process.wait(timeout=5)
        time.sleep(0.3)
        assert not job.is_running()
        stdout, _ = job.get_output()
        assert "done" in stdout

    def test_kill_running_job(self):
        job = start_background_job("sleep 60")
        assert job.is_running()
        job.kill()
        assert not job.is_running()

    def test_multiline_output(self):
        job = start_background_job("for i in 1 2 3 4 5; do echo line$i; done")
        job.process.wait(timeout=5)
        time.sleep(0.3)
        stdout, _ = job.get_output()
        for i in range(1, 6):
            assert f"line{i}" in stdout

    def test_exit_code_captured(self):
        job = start_background_job("exit 42")
        job.process.wait(timeout=5)
        assert job.process.returncode == 42


# ── Module-level functions ───────────────────────────────────────────────


class TestJobIdGeneration:
    """Test thread-safe job ID generation."""

    def test_sequential_ids(self):
        id1 = _get_next_job_id()
        id2 = _get_next_job_id()
        assert id2 == id1 + 1

    def test_concurrent_id_generation(self):
        """Verify no duplicate IDs under concurrent access."""
        ids: list[int] = []
        lock = threading.Lock()

        def grab_ids(n: int):
            local = [_get_next_job_id() for _ in range(n)]
            with lock:
                ids.extend(local)

        threads = [threading.Thread(target=grab_ids, args=(50,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(ids) == 200
        assert len(set(ids)) == 200  # all unique


class TestStartBackgroundJob:
    """Test start_background_job."""

    def test_returns_job(self):
        job = start_background_job("echo test")
        assert isinstance(job, BackgroundJob)
        assert job.command == "echo test"
        assert job.id >= 1
        job.process.wait(timeout=5)

    def test_job_tracked(self):
        job = start_background_job("echo test")
        retrieved = get_background_job(job.id)
        assert retrieved is job
        job.process.wait(timeout=5)

    def test_reader_thread_started(self):
        job = start_background_job("echo test")
        assert job._reader_thread is not None
        assert job._reader_thread.is_alive() or not job.is_running()
        job.process.wait(timeout=5)


class TestGetBackgroundJob:
    """Test get_background_job."""

    def test_existing_job(self):
        job = start_background_job("true")
        assert get_background_job(job.id) is job
        job.process.wait(timeout=5)

    def test_nonexistent_job(self):
        assert get_background_job(99999) is None


class TestListBackgroundJobs:
    """Test list_background_jobs."""

    def test_empty_list(self):
        assert list_background_jobs() == []

    def test_lists_running_jobs(self):
        job1 = start_background_job("sleep 10")
        job2 = start_background_job("sleep 10")
        jobs = list_background_jobs()
        assert len(jobs) == 2
        job1.kill()
        job2.kill()

    def test_excludes_finished_jobs(self):
        job = start_background_job("true")
        job.process.wait(timeout=5)
        time.sleep(0.1)
        # list_background_jobs calls cleanup_finished_jobs
        jobs = list_background_jobs()
        assert len(jobs) == 0


class TestCleanupFinishedJobs:
    """Test cleanup_finished_jobs."""

    def test_removes_finished(self):
        job = start_background_job("true")
        job.process.wait(timeout=5)
        time.sleep(0.1)
        # Job still tracked before cleanup (start_background_job calls cleanup internally,
        # but only finished jobs from *previous* runs are cleaned; this job was just added)
        assert get_background_job(job.id) is not None
        cleanup_finished_jobs()
        assert get_background_job(job.id) is None

    def test_keeps_running(self):
        job = start_background_job("sleep 10")
        cleanup_finished_jobs()
        assert get_background_job(job.id) is not None
        job.kill()


class TestResetBackgroundJobs:
    """Test reset_background_jobs."""

    def test_kills_running_jobs(self):
        job = start_background_job("sleep 60")
        assert job.is_running()
        reset_background_jobs()
        assert not job.is_running()

    def test_clears_all_jobs(self):
        start_background_job("sleep 10")
        start_background_job("sleep 10")
        reset_background_jobs()
        assert list_background_jobs() == []

    def test_resets_id_counter(self):
        start_background_job("true").process.wait(timeout=5)
        reset_background_jobs()
        job = start_background_job("true")
        assert job.id == 1
        job.process.wait(timeout=5)


# ── Command handlers ─────────────────────────────────────────────────────


class TestExecuteBgCommand:
    """Test execute_bg_command."""

    def test_empty_command(self):
        msgs = _collect(execute_bg_command(""))
        assert len(msgs) == 1
        assert "Usage" in msgs[0].content

    def test_whitespace_command(self):
        msgs = _collect(execute_bg_command("   "))
        assert len(msgs) == 1
        assert "Usage" in msgs[0].content

    def test_starts_job(self):
        msgs = _collect(execute_bg_command("echo hello"))
        assert len(msgs) == 1
        assert "Started background job" in msgs[0].content
        assert "echo hello" in msgs[0].content

    def test_shows_management_commands(self):
        msgs = _collect(execute_bg_command("echo hi"))
        content = msgs[0].content
        assert "jobs" in content
        assert "output" in content
        assert "kill" in content

    def test_denylisted_command(self):
        with patch(
            "gptme.tools.shell_validation.is_denylisted",
            return_value=(True, "Dangerous command", "rm -rf /"),
        ):
            msgs = _collect(execute_bg_command("rm -rf /"))
            assert len(msgs) == 1
            assert "denied" in msgs[0].content.lower()
            assert "rm -rf /" in msgs[0].content

    def test_allowed_command(self):
        with patch(
            "gptme.tools.shell_validation.is_denylisted",
            return_value=(False, "", ""),
        ):
            msgs = _collect(execute_bg_command("npm run dev"))
            assert len(msgs) == 1
            assert "Started" in msgs[0].content


class TestExecuteJobsCommand:
    """Test execute_jobs_command."""

    def test_no_jobs(self):
        msgs = _collect(execute_jobs_command())
        assert len(msgs) == 1
        assert "No background jobs" in msgs[0].content

    def test_lists_running_job(self):
        job = start_background_job("sleep 10")
        msgs = _collect(execute_jobs_command())
        assert len(msgs) == 1
        assert f"#{job.id}" in msgs[0].content
        assert "Running" in msgs[0].content
        job.kill()

    def test_shows_command_truncated(self):
        long_cmd = "sleep 10 " + "#" + "x" * 100  # long command that stays running
        job = start_background_job(long_cmd)
        msgs = _collect(execute_jobs_command())
        content = msgs[0].content
        assert "..." in content  # truncated at 50 chars
        job.kill()

    def test_shows_elapsed_seconds(self):
        job = start_background_job("sleep 10")
        time.sleep(0.1)
        msgs = _collect(execute_jobs_command())
        content = msgs[0].content
        assert re.search(r"\d+\.\d+s", content), (
            f"Expected elapsed seconds format like '0.1s', got: {content!r}"
        )
        job.kill()


class TestExecuteOutputCommand:
    """Test execute_output_command."""

    def test_invalid_id_string(self):
        msgs = _collect(execute_output_command("abc"))
        assert len(msgs) == 1
        assert "Invalid job ID" in msgs[0].content

    def test_nonexistent_job(self):
        msgs = _collect(execute_output_command("99999"))
        assert len(msgs) == 1
        assert "No job with ID" in msgs[0].content

    def test_shows_output(self):
        job = start_background_job("echo hello_from_bg")
        job.process.wait(timeout=5)
        time.sleep(0.3)
        msgs = _collect(execute_output_command(str(job.id)))
        assert len(msgs) == 1
        content = msgs[0].content
        assert "hello_from_bg" in content
        assert f"Job #{job.id}" in content

    def test_shows_running_status(self):
        job = start_background_job("sleep 10")
        msgs = _collect(execute_output_command(str(job.id)))
        assert "Running" in msgs[0].content
        job.kill()

    def test_shows_finished_status(self):
        job = start_background_job("true")
        job.process.wait(timeout=5)
        time.sleep(0.1)
        msgs = _collect(execute_output_command(str(job.id)))
        assert "Finished" in msgs[0].content
        assert "exit code" in msgs[0].content

    def test_no_output_yet(self):
        # Use a command that produces no output
        job = start_background_job("sleep 10")
        msgs = _collect(execute_output_command(str(job.id)))
        assert "No output yet" in msgs[0].content
        job.kill()

    def test_truncates_long_stdout(self):
        job = start_background_job("echo hello_from_bg")
        job.process.wait(timeout=5)
        time.sleep(0.3)
        # Manually inject large output to test truncation
        with job._buffer_lock:
            job.stdout_buffer = ["x" * 10000]
        msgs = _collect(execute_output_command(str(job.id)))
        content = msgs[0].content
        assert "truncated" in content

    def test_truncates_long_stderr(self):
        job = start_background_job("true")
        job.process.wait(timeout=5)
        time.sleep(0.1)
        # Inject large stderr
        with job._buffer_lock:
            job.stderr_buffer = ["e" * 3000]
        msgs = _collect(execute_output_command(str(job.id)))
        content = msgs[0].content
        assert "truncated" in content


class TestExecuteKillCommand:
    """Test execute_kill_command."""

    def test_invalid_id_string(self):
        msgs = _collect(execute_kill_command("abc"))
        assert len(msgs) == 1
        assert "Invalid job ID" in msgs[0].content

    def test_nonexistent_job(self):
        msgs = _collect(execute_kill_command("99999"))
        assert len(msgs) == 1
        assert "No job with ID" in msgs[0].content

    def test_kill_running_job(self):
        job = start_background_job("sleep 60")
        msgs = _collect(execute_kill_command(str(job.id)))
        assert len(msgs) == 1
        assert "Terminated" in msgs[0].content
        assert not job.is_running()

    def test_kill_already_finished(self):
        job = start_background_job("true")
        job.process.wait(timeout=5)
        time.sleep(0.1)
        msgs = _collect(execute_kill_command(str(job.id)))
        assert "already finished" in msgs[0].content


# ── Edge cases ───────────────────────────────────────────────────────────


class TestEdgeCases:
    """Test edge cases and error paths."""

    def test_job_with_nonzero_exit(self):
        job = start_background_job("exit 1")
        job.process.wait(timeout=5)
        assert job.process.returncode == 1

    def test_job_with_mixed_output(self):
        job = start_background_job("echo out && echo err >&2")
        job.process.wait(timeout=5)
        time.sleep(0.3)
        stdout, stderr = job.get_output()
        assert "out" in stdout
        assert "err" in stderr

    def test_rapid_start_and_finish(self):
        """Start many short-lived jobs rapidly."""
        jobs = [start_background_job(f"echo job{i}") for i in range(10)]
        for job in jobs:
            job.process.wait(timeout=5)
        time.sleep(0.3)
        # All should have captured output
        for i, job in enumerate(jobs):
            stdout, _ = job.get_output()
            assert f"job{i}" in stdout

    def test_new_session_flag(self):
        """Verify jobs run in new process group (not killed by parent signals)."""
        job = start_background_job("sleep 5")
        pid = job.process.pid
        # On Linux, start_new_session=True makes the process its own session/group leader
        # so os.getpgid(pid) == pid
        try:
            pgid = os.getpgid(pid)
            assert pgid == pid, (
                f"Expected process {pid} to be its own group leader, got pgid={pgid}"
            )
        except ProcessLookupError:
            pytest.skip("Process exited before pgid could be checked")
        finally:
            job.kill()

    def test_stdin_devnull(self):
        """Verify stdin is /dev/null (no hang on input)."""
        job = start_background_job("cat")  # cat with no stdin should exit immediately
        job.process.wait(timeout=5)
        assert job.process.returncode == 0

    def test_unicode_output(self):
        job = start_background_job("echo '日本語テスト'")
        job.process.wait(timeout=5)
        time.sleep(0.3)
        stdout, _ = job.get_output()
        assert "日本語テスト" in stdout

    def test_elapsed_time_increases(self):
        job = start_background_job("sleep 10")
        t1 = job.elapsed_time()
        time.sleep(0.2)
        t2 = job.elapsed_time()
        assert t2 > t1
        job.kill()

    def test_output_command_shows_command_text(self):
        job = start_background_job("echo my_specific_command")
        job.process.wait(timeout=5)
        msgs = _collect(execute_output_command(str(job.id)))
        assert "my_specific_command" in msgs[0].content

    def test_jobs_formats_minutes(self):
        """Test that elapsed time shows minutes for long-running jobs."""
        job = start_background_job("sleep 10")
        # Override start_time to simulate 120s elapsed
        job.start_time = time.time() - 120
        msgs = _collect(execute_jobs_command())
        content = msgs[0].content
        assert re.search(r"\d+\.\d+m", content), (
            f"Expected elapsed minutes format like '2.0m', got: {content!r}"
        )
        job.kill()
