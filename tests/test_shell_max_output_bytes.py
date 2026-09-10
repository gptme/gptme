"""Tests for GPTME_SHELL_MAX_OUTPUT_BYTES — bounded captured subprocess output.

Issue #3798: the shell tool accumulated subprocess output into an unbounded list
before applying token-level truncation. A ``cat`` of a 2.7 GiB log file pushed
the gptme process to 3.3 GiB RSS and drained the system swap.

The fix adds a byte cap (default 32 MiB) that kills the child and embeds a
truncation marker in the output when the cap is exceeded.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from gptme.tools.shell import (
    _DEFAULT_MAX_OUTPUT_BYTES,
    ShellSession,
    _get_max_output_bytes,
)

# ---------------------------------------------------------------------------
# Unit tests for _get_max_output_bytes
# ---------------------------------------------------------------------------


def test_default_when_env_unset(monkeypatch):
    """Without any config the default (32 MiB) is returned."""
    monkeypatch.delenv("GPTME_SHELL_MAX_OUTPUT_BYTES", raising=False)
    mock_cfg = MagicMock()
    mock_cfg.get_env.return_value = None
    with patch("gptme.config.get_config", return_value=mock_cfg):
        result = _get_max_output_bytes()
    assert result == _DEFAULT_MAX_OUTPUT_BYTES


def test_env_override_bytes(monkeypatch):
    """A plain integer env value is accepted."""
    mock_cfg = MagicMock()
    mock_cfg.get_env.return_value = "8388608"  # 8 MiB
    with patch("gptme.config.get_config", return_value=mock_cfg):
        result = _get_max_output_bytes()
    assert result == 8 * 1024 * 1024


def test_env_override_suffix(monkeypatch):
    """A suffixed value like '16M' is parsed correctly."""
    mock_cfg = MagicMock()
    mock_cfg.get_env.return_value = "16M"
    with patch("gptme.config.get_config", return_value=mock_cfg):
        result = _get_max_output_bytes()
    assert result == 16 * 1024 * 1024


def test_invalid_env_falls_back_to_default():
    """An unparseable value logs a warning and returns the default."""
    mock_cfg = MagicMock()
    mock_cfg.get_env.return_value = "not-a-number"
    with patch("gptme.config.get_config", return_value=mock_cfg):
        result = _get_max_output_bytes()
    assert result == _DEFAULT_MAX_OUTPUT_BYTES


def test_zero_env_falls_back_to_default():
    """A zero or negative value falls back to the default (doesn't disable cap)."""
    mock_cfg = MagicMock()
    mock_cfg.get_env.return_value = "0"
    with patch("gptme.config.get_config", return_value=mock_cfg):
        result = _get_max_output_bytes()
    assert result == _DEFAULT_MAX_OUTPUT_BYTES


# ---------------------------------------------------------------------------
# Integration tests — require a real ShellSession
# ---------------------------------------------------------------------------


@pytest.fixture()
def shell():
    """Provide a fresh ShellSession and close it after the test."""
    s = ShellSession()
    yield s
    s.close()


@pytest.mark.skipif(os.name == "nt", reason="SIGTERM/SIGKILL are POSIX-only")
def test_cap_kills_large_output(shell):
    """Output larger than the cap triggers kill and embeds the truncation marker.

    We set a tiny cap (64 KiB) and run ``yes`` — a program that produces
    infinite output without consuming memory itself. The shell should stop it
    quickly and return the marker.
    """
    tiny_cap = 64 * 1024  # 64 KiB
    with patch("gptme.tools.shell._get_max_output_bytes", return_value=tiny_cap):
        returncode, stdout, stderr = shell.run("yes", timeout=10)

    assert returncode == -125, f"Expected -125 (byte cap), got {returncode}"
    assert "[output truncated" in stdout, (
        f"Truncation marker missing from stdout: {stdout[:200]}"
    )
    assert "process killed" in stdout


@pytest.mark.skipif(os.name == "nt", reason="SIGTERM/SIGKILL are POSIX-only")
def test_cap_total_output_size_bounded(shell):
    """The total captured output must not exceed the cap by more than one read chunk.

    Uses a 256 KiB cap and ``yes`` to generate infinite output. The resulting
    stdout must be well under 1 MiB — proving the cap prevents memory growth.
    """
    cap = 256 * 1024  # 256 KiB
    chunk = 2**16  # 64 KiB — the read chunk size

    with patch("gptme.tools.shell._get_max_output_bytes", return_value=cap):
        returncode, stdout, stderr = shell.run("yes", timeout=10)

    assert returncode == -125, f"Expected -125 (byte cap), got {returncode}"
    # Output must be capped: at most cap + one extra chunk + truncation marker
    captured = len(stdout.encode("utf-8", errors="replace"))
    assert captured < cap + chunk + 512, (
        f"Output ({captured} bytes) exceeds cap ({cap} bytes) by more than one chunk"
    )


@pytest.mark.skipif(os.name == "nt", reason="SIGTERM/SIGKILL are POSIX-only")
def test_normal_output_below_cap_unaffected(shell):
    """Commands producing output well below the cap run normally (no -125)."""
    with patch(
        "gptme.tools.shell._get_max_output_bytes",
        return_value=_DEFAULT_MAX_OUTPUT_BYTES,
    ):
        returncode, stdout, stderr = shell.run("echo hello")

    assert returncode == 0, f"stderr: {stderr}"
    assert "hello" in stdout
    assert "[output truncated" not in stdout


@pytest.mark.skipif(os.name == "nt", reason="SIGTERM/SIGKILL are POSIX-only")
def test_cap_preserves_partial_output(shell):
    """Some output is captured before the cap fires; it must be in stdout."""
    cap = 32 * 1024  # 32 KiB

    with patch("gptme.tools.shell._get_max_output_bytes", return_value=cap):
        returncode, stdout, stderr = shell.run("yes", timeout=10)

    assert returncode == -125
    # There should be some actual content before the marker
    marker_pos = stdout.find("[output truncated")
    assert marker_pos > 0, "No content before truncation marker"
