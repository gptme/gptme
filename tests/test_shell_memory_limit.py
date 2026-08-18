"""Integration tests for the opt-in shell memory ceiling (GPTME_SHELL_MEMORY_LIMIT).

Idea #1128: a per-shell RLIMIT_AS ceiling so a runaway command fails with an
allocation error instead of taking the session (or host) down with it.
"""

import os

import pytest

from gptme.tools.shell import ShellSession

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="ulimit -v is POSIX-only (no resource module on Windows)"
)


def _make_shell():
    return ShellSession()


def test_memory_limit_unset_keeps_behavior(monkeypatch):
    """With no limit set, a moderate allocation succeeds as before."""
    monkeypatch.delenv("GPTME_SHELL_MEMORY_LIMIT", raising=False)
    shell = _make_shell()
    try:
        code, stdout, stderr = shell.run("python3 -c 'bytearray(64 * 1024 * 1024)'")
        assert code == 0, f"stderr: {stderr}"
    finally:
        shell.close()


def test_memory_limit_blocks_overallocation(monkeypatch):
    """With a 256 MiB ceiling, a 1 GiB allocation fails with MemoryError."""
    monkeypatch.setenv("GPTME_SHELL_MEMORY_LIMIT", "256M")
    shell = _make_shell()
    try:
        code, stdout, stderr = shell.run("python3 -c 'bytearray(1024 * 1024 * 1024)'")
        assert code != 0, f"stdout: {stdout}\nstderr: {stderr}"
        assert "MemoryError" in stderr
    finally:
        shell.close()


def test_memory_limit_allows_small_allocation(monkeypatch):
    """The ceiling permits allocations comfortably under the limit."""
    monkeypatch.setenv("GPTME_SHELL_MEMORY_LIMIT", "256M")
    shell = _make_shell()
    try:
        code, stdout, stderr = shell.run("python3 -c 'bytearray(1024 * 1024)'")
        assert code == 0, f"stderr: {stderr}"
    finally:
        shell.close()
