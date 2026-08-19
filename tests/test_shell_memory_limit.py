"""Integration tests for the opt-in shell memory ceiling (GPTME_SHELL_MEMORY_LIMIT).

Idea #1128: a per-shell RLIMIT_AS ceiling so a runaway command fails with an
allocation error instead of taking the session (or host) down with it.
"""

import os
from unittest.mock import patch

import pytest

from gptme.tools.shell import ShellSession, _get_memory_limit

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="ulimit -v is POSIX-only (no resource module on Windows)"
)


def _make_shell():
    return ShellSession()


def test_memory_limit_unset_keeps_behavior(monkeypatch):
    """With no limit set, a moderate allocation succeeds as before."""
    monkeypatch.delenv("GPTME_SHELL_MEMORY_LIMIT", raising=False)
    # Also block any [env] SHELL_MEMORY_LIMIT from config.toml so the test is
    # hermetic in environments that have that key set.
    from unittest.mock import MagicMock

    mock_cfg = MagicMock()
    mock_cfg.get_env.return_value = None
    monkeypatch.setattr("gptme.config.get_config", lambda: mock_cfg)
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


def test_integer_config_value_does_not_raise():
    """Integer TOML config values (e.g. SHELL_MEMORY_LIMIT = 536870912) must not
    raise AttributeError via value.strip() — they should be coerced to str first."""
    with (
        patch("gptme.config.get_config") as mock_cfg,
        patch("gptme.tools.shell.verify_memory_limit"),
    ):
        mock_env = mock_cfg.return_value
        mock_env.get_env.return_value = 536870912  # integer, not string
        result = _get_memory_limit()
    assert result == 536870912, f"Expected 512MiB in bytes, got {result}"


def test_unenforceable_limit_warns_and_returns_none():
    """When the system hard ulimit is below the configured ceiling,
    _get_memory_limit() must log a warning and return None rather than raise
    RuntimeError — a misconfiguration should not crash every shell invocation."""
    with (
        patch("gptme.config.get_config") as mock_cfg,
        patch(
            "gptme.tools.shell.verify_memory_limit",
            side_effect=ValueError("hard limit exceeded"),
        ),
    ):
        mock_env = mock_cfg.return_value
        mock_env.get_env.return_value = "4G"  # exceeds hypothetical hard limit
        result = _get_memory_limit()
    assert result is None
