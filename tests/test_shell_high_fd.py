"""Regression tests for shell fd handling above FD_SETSIZE.

`select.select()` is backed by `fd_set`, which cannot represent a descriptor
>= FD_SETSIZE (1024): it raises `ValueError: filedescriptor out of range in
select()` rather than degrading. Long-lived processes and parallel test runs
(`pytest -n 16`) push descriptors past that line, which surfaced as spurious
`test_tools_shell` / `test_shell_output_mixing_issue408` failures across every
PR in the repo. See gptme/gptme#3715.
"""

import os
import resource

import pytest

from gptme.tools.shell import _wait_readable

# Anything at or above FD_SETSIZE is unrepresentable in an fd_set.
FD_SETSIZE = 1024
HIGH_FD = 1100


@pytest.fixture
def high_fd_limit():
    """Raise the soft RLIMIT_NOFILE far enough to allocate HIGH_FD, then restore."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    needed = HIGH_FD + 16
    if soft < needed:
        if hard != resource.RLIM_INFINITY and hard < needed:
            pytest.skip(f"RLIMIT_NOFILE hard limit {hard} < {needed}")
        resource.setrlimit(resource.RLIMIT_NOFILE, (needed, hard))
    yield
    resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def test_wait_readable_handles_fd_above_fd_setsize(high_fd_limit):
    """A readable descriptor >= FD_SETSIZE must be reported, not raise."""
    read_fd, write_fd = os.pipe()
    high_read_fd = None
    try:
        os.dup2(read_fd, HIGH_FD)
        high_read_fd = HIGH_FD
        assert high_read_fd >= FD_SETSIZE

        os.write(write_fd, b"payload")
        # Pre-fix this raised ValueError: filedescriptor out of range in select()
        assert _wait_readable([high_read_fd], 1.0) == [high_read_fd]
        assert os.read(high_read_fd, 16) == b"payload"
    finally:
        for fd in (high_read_fd, read_fd, write_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


def test_wait_readable_reports_only_ready_fds():
    """An idle descriptor is not reported; a written-to one is."""
    idle_r, idle_w = os.pipe()
    ready_r, ready_w = os.pipe()
    try:
        os.write(ready_w, b"x")
        assert _wait_readable([idle_r, ready_r], 0.1) == [ready_r]
    finally:
        for fd in (idle_r, idle_w, ready_r, ready_w):
            os.close(fd)


def test_wait_readable_times_out_with_no_data():
    """No readable descriptors within the timeout returns an empty list."""
    read_fd, write_fd = os.pipe()
    try:
        assert _wait_readable([read_fd], 0.05) == []
    finally:
        os.close(read_fd)
        os.close(write_fd)
