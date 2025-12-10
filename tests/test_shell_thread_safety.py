"""Tests for shell tool thread safety.

These tests verify that the shell tool works correctly with multiple threads,
as used in gptme-server which runs a thread per session/conversation.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context

import pytest

from gptme.tools.shell import (
    ShellSession,
    _background_jobs_var,
    _get_background_jobs_state,
    _shell_var,
    cleanup_shell,
    get_background_job,
    get_shell,
    list_background_jobs,
    reset_background_jobs,
    set_shell,
    start_background_job,
)


def test_shell_context_isolation():
    """Test that each context gets its own shell session."""
    results = {}
    barrier = threading.Barrier(2)

    def run_in_context(name: str):
        # Get shell for this context
        shell = get_shell()
        results[f"{name}_shell_id"] = id(shell)

        # Run a command to verify the shell works
        ret, out, err = shell.run("echo 'hello from context'", output=False)
        results[f"{name}_output"] = out.strip()
        results[f"{name}_ret"] = ret

        # Wait for other thread
        barrier.wait()

        # Verify we still get the same shell
        shell2 = get_shell()
        results[f"{name}_shell_id_2"] = id(shell2)

        # Clean up
        cleanup_shell()

    # Run in two different contexts (threads)
    ctx1 = copy_context()
    ctx2 = copy_context()

    t1 = threading.Thread(target=lambda: ctx1.run(run_in_context, "thread1"))
    t2 = threading.Thread(target=lambda: ctx2.run(run_in_context, "thread2"))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Each thread should have gotten its own shell
    assert results["thread1_shell_id"] != results["thread2_shell_id"]
    # Each thread should have gotten the same shell on both calls
    assert results["thread1_shell_id"] == results["thread1_shell_id_2"]
    assert results["thread2_shell_id"] == results["thread2_shell_id_2"]
    # Both shells should work correctly
    assert results["thread1_output"] == "hello from context"
    assert results["thread2_output"] == "hello from context"
    assert results["thread1_ret"] == 0
    assert results["thread2_ret"] == 0


def test_shell_set_shell_context_isolation():
    """Test that set_shell affects only the current context."""
    results = {}

    def run_in_context(name: str):
        # Initially no shell
        initial_shell = _shell_var.get()
        results[f"{name}_initial"] = initial_shell

        # Create and set a shell
        shell = ShellSession()
        set_shell(shell)
        results[f"{name}_shell_id"] = id(shell)

        # Verify it's set
        got_shell = _shell_var.get()
        results[f"{name}_got_shell_id"] = id(got_shell) if got_shell else None

        # Clean up
        cleanup_shell()

    ctx1 = copy_context()
    ctx2 = copy_context()

    t1 = threading.Thread(target=lambda: ctx1.run(run_in_context, "thread1"))
    t2 = threading.Thread(target=lambda: ctx2.run(run_in_context, "thread2"))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Each context should have had its own shell
    assert results["thread1_shell_id"] != results["thread2_shell_id"]
    assert results["thread1_got_shell_id"] == results["thread1_shell_id"]
    assert results["thread2_got_shell_id"] == results["thread2_shell_id"]


def test_background_jobs_context_isolation():
    """Test that background jobs are isolated per context."""
    results = {}
    barrier = threading.Barrier(2)

    def run_in_context(name: str):
        # Reset to clean state
        reset_background_jobs()

        # Start a background job
        job = start_background_job(f"echo '{name}'")
        results[f"{name}_job_id"] = job.id

        # Verify we can see our job
        jobs = list_background_jobs()
        results[f"{name}_job_count"] = len(jobs)

        # Wait for other thread to create its job
        barrier.wait()

        # Should still only see our own jobs
        jobs_after = list_background_jobs()
        results[f"{name}_job_count_after"] = len(jobs_after)

        # Clean up
        reset_background_jobs()

    ctx1 = copy_context()
    ctx2 = copy_context()

    t1 = threading.Thread(target=lambda: ctx1.run(run_in_context, "thread1"))
    t2 = threading.Thread(target=lambda: ctx2.run(run_in_context, "thread2"))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Both threads should have job ID 1 (each has its own counter)
    assert results["thread1_job_id"] == 1
    assert results["thread2_job_id"] == 1
    # Each should see only 1 job (maybe 0 if finished quickly)
    assert results["thread1_job_count"] <= 1
    assert results["thread2_job_count"] <= 1
    assert results["thread1_job_count_after"] <= 1
    assert results["thread2_job_count_after"] <= 1


def test_background_jobs_state_initialization():
    """Test that background job state is properly initialized per context."""

    def run_in_context():
        # Get state - should be created automatically
        state = _get_background_jobs_state()
        assert state is not None
        assert state.jobs == {}
        assert state.next_job_id == 1

        # Start a job to verify it works
        job = start_background_job("echo 'test'")
        assert job.id == 1

        # Next job should have ID 2
        job2 = start_background_job("echo 'test2'")
        assert job2.id == 2

        # Clean up
        reset_background_jobs()

    ctx = copy_context()
    t = threading.Thread(target=lambda: ctx.run(run_in_context))
    t.start()
    t.join()


def test_cleanup_shell():
    """Test that cleanup_shell properly cleans up the shell."""

    def run_in_context():
        # Get a shell
        shell = get_shell()
        assert shell is not None
        shell_id = id(shell)

        # Run a command to ensure it works
        ret, out, err = shell.run("echo 'test'", output=False)
        assert ret == 0

        # Clean up
        cleanup_shell()

        # Shell should be None now
        assert _shell_var.get() is None

        # Getting a new shell should create a new one
        new_shell = get_shell()
        assert id(new_shell) != shell_id

        # Clean up again
        cleanup_shell()

    ctx = copy_context()
    t = threading.Thread(target=lambda: ctx.run(run_in_context))
    t.start()
    t.join()


def test_concurrent_shell_commands():
    """Test that concurrent shell commands in different contexts work correctly."""
    results = {}
    errors = []
    num_threads = 5

    def run_commands(thread_id: int):
        try:
            # Use copy_context to ensure each thread has its own context
            ctx = copy_context()

            def inner():
                shell = get_shell()

                # Run several commands
                for i in range(3):
                    ret, out, err = shell.run(
                        f"echo 'thread{thread_id}_cmd{i}'", output=False
                    )
                    if ret != 0:
                        errors.append(f"Thread {thread_id} cmd {i} failed: {err}")
                        return
                    expected = f"thread{thread_id}_cmd{i}"
                    if expected not in out:
                        errors.append(
                            f"Thread {thread_id} cmd {i} wrong output: {out}"
                        )
                        return

                results[thread_id] = "success"
                cleanup_shell()

            ctx.run(inner)
        except Exception as e:
            errors.append(f"Thread {thread_id} exception: {e}")

    threads = [threading.Thread(target=run_commands, args=(i,)) for i in range(num_threads)]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Check results
    assert len(errors) == 0, f"Errors: {errors}"
    assert len(results) == num_threads
    assert all(v == "success" for v in results.values())


def test_concurrent_background_jobs():
    """Test that concurrent background job operations in different contexts work."""
    results = {}
    num_threads = 3

    def run_jobs(thread_id: int):
        ctx = copy_context()

        def inner():
            reset_background_jobs()

            # Start a few jobs
            jobs = []
            for i in range(2):
                job = start_background_job(f"sleep 0.1 && echo 'thread{thread_id}_job{i}'")
                jobs.append(job)

            # Jobs should have sequential IDs starting from 1
            results[f"thread{thread_id}_job_ids"] = [j.id for j in jobs]

            # Wait for jobs to complete
            time.sleep(0.3)

            # Get output from each job
            outputs = []
            for job in jobs:
                stdout, _ = job.get_output()
                outputs.append(stdout.strip())
            results[f"thread{thread_id}_outputs"] = outputs

            reset_background_jobs()

        ctx.run(inner)

    threads = [threading.Thread(target=run_jobs, args=(i,)) for i in range(num_threads)]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Each thread should have job IDs [1, 2]
    for i in range(num_threads):
        assert results[f"thread{i}_job_ids"] == [1, 2]

    # Each thread should have its own output
    for i in range(num_threads):
        outputs = results[f"thread{i}_outputs"]
        assert len(outputs) == 2
        for j, out in enumerate(outputs):
            assert f"thread{i}_job{j}" in out


def test_shell_isolation_with_cwd():
    """Test that shell working directory is isolated between contexts."""
    import tempfile
    import os

    results = {}
    barrier = threading.Barrier(2)

    def run_in_context(name: str, target_dir: str):
        ctx = copy_context()

        def inner():
            shell = get_shell()

            # Change to our target directory
            ret, out, err = shell.run(f"cd {target_dir}", output=False)
            assert ret == 0

            # Wait for other thread to also change directory
            barrier.wait()

            # Verify we're still in our directory
            ret, out, err = shell.run("pwd", output=False)
            results[f"{name}_pwd"] = out.strip()

            cleanup_shell()

        ctx.run(inner)

    # Create two temp directories
    dir1 = tempfile.mkdtemp(prefix="shell_test_1_")
    dir2 = tempfile.mkdtemp(prefix="shell_test_2_")

    try:
        t1 = threading.Thread(target=run_in_context, args=("thread1", dir1))
        t2 = threading.Thread(target=run_in_context, args=("thread2", dir2))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Each thread should be in its own directory
        # Note: on some systems the temp dir might be a symlink
        assert os.path.realpath(results["thread1_pwd"]) == os.path.realpath(dir1)
        assert os.path.realpath(results["thread2_pwd"]) == os.path.realpath(dir2)
    finally:
        import shutil
        shutil.rmtree(dir1, ignore_errors=True)
        shutil.rmtree(dir2, ignore_errors=True)


def test_shell_env_isolation():
    """Test that shell environment variables are isolated between contexts."""
    results = {}
    barrier = threading.Barrier(2)

    def run_in_context(name: str, value: str):
        ctx = copy_context()

        def inner():
            shell = get_shell()

            # Set an environment variable
            ret, _, _ = shell.run(f"export TEST_VAR='{value}'", output=False)
            assert ret == 0

            # Wait for other thread
            barrier.wait()

            # Read back the variable
            ret, out, _ = shell.run("echo $TEST_VAR", output=False)
            results[f"{name}_var"] = out.strip()

            cleanup_shell()

        ctx.run(inner)

    t1 = threading.Thread(target=run_in_context, args=("thread1", "value_from_thread1"))
    t2 = threading.Thread(target=run_in_context, args=("thread2", "value_from_thread2"))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Each thread should see its own value
    assert results["thread1_var"] == "value_from_thread1"
    assert results["thread2_var"] == "value_from_thread2"


def test_thread_pool_executor():
    """Test that the shell tool works with ThreadPoolExecutor (common pattern in servers)."""
    results = []
    errors = []

    def task(task_id: int):
        ctx = copy_context()

        def inner():
            try:
                shell = get_shell()
                ret, out, err = shell.run(f"echo 'task_{task_id}'", output=False)
                if ret == 0 and f"task_{task_id}" in out:
                    return f"task_{task_id}_success"
                return f"task_{task_id}_failed: {out}, {err}"
            finally:
                cleanup_shell()

        return ctx.run(inner)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(task, i) for i in range(10)]
        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                errors.append(str(e))

    assert len(errors) == 0, f"Errors: {errors}"
    assert len(results) == 10
    assert all("success" in r for r in results)


def test_shell_process_independence():
    """Test that each shell has its own bash process."""
    results = {}

    def run_in_context(name: str):
        ctx = copy_context()

        def inner():
            shell = get_shell()
            # Get the PID of the bash process
            ret, out, err = shell.run("echo $$", output=False)
            results[f"{name}_pid"] = out.strip()
            cleanup_shell()

        ctx.run(inner)

    t1 = threading.Thread(target=run_in_context, args=("thread1",))
    t2 = threading.Thread(target=run_in_context, args=("thread2",))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Each thread should have a different bash process
    assert results["thread1_pid"] != results["thread2_pid"]


def test_no_shell_leaks():
    """Test that shells are properly cleaned up and don't leak."""
    from gptme.tools.shell import _all_shells, _all_shells_lock

    # Record initial shell count
    with _all_shells_lock:
        initial_count = len(_all_shells)

    def run_and_cleanup():
        ctx = copy_context()

        def inner():
            shell = get_shell()
            ret, out, err = shell.run("echo 'test'", output=False)
            assert ret == 0
            cleanup_shell()

        ctx.run(inner)

    # Run several threads
    threads = [threading.Thread(target=run_and_cleanup) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Give a moment for cleanup
    time.sleep(0.1)

    # Should be back to initial count (all shells cleaned up)
    with _all_shells_lock:
        final_count = len(_all_shells)

    assert final_count == initial_count, f"Shell leak: {initial_count} -> {final_count}"
