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
    _strip_shell_return_marker,
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
# Unit tests for _strip_shell_return_marker
# ---------------------------------------------------------------------------

DELIM = "END_OF_COMMAND_OUTPUT"


def test_marker_stripped_when_same_line():
    """The single-line injected return marker is removed from byte accounting."""
    chunk = b"hello\nworld\nReturnCode:0 END_OF_COMMAND_OUTPUT\n"
    assert _strip_shell_return_marker(chunk, DELIM) == b"hello\nworld\n"


def test_marker_split_across_lines_not_stripped():
    """Command output with ReturnCode: and delimiter on SEPARATE lines counts fully.

    Regression for Greptile P1 (marker text bypasses cap): previously anything
    with ``ReturnCode:`` later followed by the delimiter in the same chunk was
    stripped from byte accounting, so a command emitting marker-like text on
    separate lines could undercount and evade the cap.
    """
    chunk = b"ReturnCode: 42\nsome output\nEND_OF_COMMAND_OUTPUT here\nmore\n"
    assert _strip_shell_return_marker(chunk, DELIM) == chunk


def test_marker_stripped_only_after_returncode_line():
    """Command output before the delimiter line is preserved."""
    chunk = b"data\nReturnCode:0\nEND_OF_COMMAND_OUTPUT\n"
    # 'ReturnCode:0\n' and 'END_OF_COMMAND_OUTPUT\n' are on separate lines, so
    # nothing is stripped: this is genuine (non-marker) output text.
    assert _strip_shell_return_marker(chunk, DELIM) == chunk


def test_no_marker_leaves_chunk_untouched():
    """A chunk without the marker is passed through unchanged."""
    chunk = b"just stdout data\nwith a ReturnCode: prefix but no delimiter\n"
    assert _strip_shell_return_marker(chunk, DELIM) == chunk


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
    """The total captured output stays within the theoretical maximum.

    Uses a 256 KiB cap and ``yes`` to generate infinite output.

    The theoretical maximum for captured stdout is:
      - up to ``cap`` bytes read before the cap was detected, PLUS
      - up to ``cap`` bytes drained by ``_kill_for_byte_cap`` (drain_budget),
        PLUS one read chunk that may have already been read.

    The resulting bound is ``2 * cap + chunk``.  This is a tight hermetic bound
    derived from the implementation, not from the OS pipe-buffer size
    (bob-ai-review P2: the prior bound was non-hermetic).
    """
    cap = 256 * 1024  # 256 KiB
    chunk = 2**16  # 64 KiB — the read chunk size

    with patch("gptme.tools.shell._get_max_output_bytes", return_value=cap):
        returncode, stdout, stderr = shell.run("yes", timeout=10)

    assert returncode == -125, f"Expected -125 (byte cap), got {returncode}"
    # Tight hermetic bound: pre-cap output + drain budget + one chunk + marker
    captured = len(stdout.encode("utf-8", errors="replace"))
    assert captured < 2 * cap + chunk + 1024, (
        f"Output ({captured} bytes) exceeds 2×cap+chunk bound ({2 * cap + chunk} bytes)"
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
def test_delimiter_bytes_do_not_trip_cap(shell):
    """The shell's injected return marker is not subprocess output."""
    command_output = "x" * 4096
    with patch(
        "gptme.tools.shell._get_max_output_bytes", return_value=len(command_output)
    ):
        returncode, stdout, stderr = shell.run(f"printf %s {command_output}")

    assert returncode == 0, f"stderr: {stderr}"
    assert stdout == command_output
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


@pytest.mark.skipif(os.name == "nt", reason="SIGTERM/SIGKILL are POSIX-only")
def test_cap_counts_bytes_not_characters(shell):
    """Multibyte UTF-8 output must be bounded by bytes, not characters.

    Regression for Greptile P1: the cap previously counted decoded Unicode
    characters, so 3-4 byte UTF-8 output could accumulate ~3-4x the cap before
    the child was killed. The counter now uses raw bytes read from the pipe.
    """
    cap = 64 * 1024  # 64 KiB
    marker = "\u4e2d" * 100  # 100 three-byte characters → 300 bytes per line
    # `yes` emits marker repeatedly; at 300 bytes/line the byte counter must
    # trip the cap at roughly the same point as ASCII-only output.
    with patch("gptme.tools.shell._get_max_output_bytes", return_value=cap):
        returncode, stdout, stderr = shell.run(f"yes '{marker}'", timeout=10)

    assert returncode == -125
    captured = len(stdout.encode("utf-8", errors="replace"))
    chunk = 2**16
    # Byte cap: output stays near cap (+ one chunk + marker), never ~3x the cap
    # which would be the case if the counter were counting characters.
    assert captured < cap + 2 * chunk + 1024, (
        f"Multibyte output ({captured} bytes) exceeds byte cap ({cap} bytes) "
        "by more than one chunk"
    )


def test_cap_drain_is_byte_bounded(shell, monkeypatch):
    """The post-kill drain must be byte-bounded (Greptile P1 Security).

    Regression for the unbounded-drain-after-termination finding: when a
    descendant retains an inherited output pipe and keeps writing, the drain
    loop must stop at a bounded number of bytes instead of growing memory
    without limit. We simulate persistent readability on the pipe and assert
    _kill_for_byte_cap returns promptly with total output bounded by the cap.
    """
    from gptme.tools import shell as shell_mod

    # Force the drain to always see readable data: the loop reads a chunk,
    # drains the "budget", and stops. Without the bound it would loop forever.
    monkeypatch.setattr(shell_mod, "_wait_readable", lambda fds, timeout: list(fds))
    # Never report EOF so only the byte/time bound can stop the loop.
    real_read = os.read
    monkeypatch.setattr(
        shell_mod.os,
        "read",
        lambda fd, n: (
            real_read(fd, n)
            if fd not in (shell.stdout_fd, shell.stderr_fd)
            else b"x" * n
        ),
    )

    cap = 32 * 1024  # 32 KiB — small for a fast unit test
    with patch("gptme.tools.shell._get_max_output_bytes", return_value=cap):
        returncode, stdout, stderr = shell._kill_for_byte_cap(
            [], [], output=False, max_output_bytes=cap
        )

    assert returncode == -125
    assert "[output truncated" in stdout
    marker = "[output truncated"
    marker_idx = stdout.find(marker)
    assert marker_idx >= 0
    # Everything after the marker was drained from the pipes. The drain must
    # actually have consumed data (proving the loop ran and was stopped by the
    # bound, not exited on first pass) AND the total drained output must stay
    # within the one-cap byte budget (proving it is not unbounded).
    drained = stdout[marker_idx + len(marker) :] + stderr
    drained_bytes = len(drained.encode("utf-8", errors="replace"))
    assert drained_bytes > 0, "Drain consumed no data — bound not exercised"
    # Bound: total drained output (both pipes, sharing one budget) is at most
    # one cap's worth. The pre-fix code had no limit and would drain forever.
    assert drained_bytes <= cap + 256, (
        f"Drain not bounded by budget: captured {drained_bytes} bytes with a "
        f"{cap} byte budget"
    )
