"""Tests for shell quiet mode (Issue #44)."""

import tempfile
from pathlib import Path

from gptme.tools.shell import _format_shell_output


class TestShellQuietMode:
    """Test the quiet parameter for shell output suppression."""

    def test_quiet_suppresses_stdout(self):
        """Test that quiet mode suppresses stdout output."""
        output = _format_shell_output(
            cmd="echo test_output_value",
            stdout="test_output_value\n",
            stderr="",
            returncode=0,
            interrupted=False,
            allowlisted=True,
            quiet=True,
        )
        # The output line (stdout) should not appear separately as ```stdout block
        assert "```stdout" not in output  # stdout block should be suppressed
        assert "quiet mode" in output.lower()
        assert "echo test_output_value" in output  # command should still be shown

    def test_quiet_saves_output_to_file(self):
        """Test that quiet mode saves output to file when logdir is provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logdir = Path(tmpdir)
            output = _format_shell_output(
                cmd="echo hello",
                stdout="hello\n",
                stderr="",
                returncode=0,
                interrupted=False,
                allowlisted=True,
                quiet=True,
                logdir=logdir,
            )
            # Should indicate output was saved
            assert "saved to" in output.lower()
            assert "quiet mode" in output.lower()
            # Should not include actual stdout
            assert "```stdout" not in output

            # Verify file was created
            output_dir = logdir / "tool-outputs" / "shell-quiet"
            assert output_dir.exists()
            output_files = list(output_dir.glob("*.txt"))
            assert len(output_files) == 1

            # Verify file content
            content = output_files[0].read_text()
            assert "hello" in content
            assert "stdout" in content.lower()

    def test_quiet_saves_stderr_to_file(self):
        """Test that quiet mode saves stderr to file when logdir is provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logdir = Path(tmpdir)
            output = _format_shell_output(
                cmd="cat nonexistent",
                stdout="",
                stderr="cat: nonexistent: No such file or directory\n",
                returncode=1,
                interrupted=False,
                allowlisted=True,
                quiet=True,
                logdir=logdir,
            )
            # Should indicate output was saved and show return code
            assert "saved to" in output.lower()
            assert "Return code: 1" in output
            # Should not include actual stderr
            assert "No such file or directory" not in output

            # Verify file contains stderr
            output_dir = logdir / "tool-outputs" / "shell-quiet"
            output_files = list(output_dir.glob("*.txt"))
            content = output_files[0].read_text()
            assert "No such file or directory" in content
            assert "stderr" in content.lower()

    def test_quiet_shows_return_code_on_error(self):
        """Test that quiet mode still shows return code on errors."""
        output = _format_shell_output(
            cmd="false",
            stdout="",
            stderr="error occurred",
            returncode=1,
            interrupted=False,
            allowlisted=False,
            quiet=True,
        )
        assert "Return code: 1" in output
        assert "error occurred" not in output  # stderr should be suppressed

    def test_quiet_shows_timeout_info(self):
        """Test that quiet mode shows timeout information."""
        output = _format_shell_output(
            cmd="sleep 100",
            stdout="",
            stderr="",
            returncode=-124,
            interrupted=False,
            allowlisted=True,
            timed_out=True,
            timeout_value=5.0,
            quiet=True,
        )
        assert "timed out" in output.lower()
        assert "5" in output  # should show timeout value

    def test_quiet_shows_interrupt_info(self):
        """Test that quiet mode shows interrupt information."""
        output = _format_shell_output(
            cmd="cat",
            stdout="partial",
            stderr="",
            returncode=None,
            interrupted=True,
            allowlisted=True,
            quiet=True,
        )
        assert "interrupt" in output.lower()
        assert "partial" not in output  # stdout should be suppressed

    def test_quiet_no_logdir_fallback(self):
        """Test that quiet mode falls back to simple message without logdir."""
        output = _format_shell_output(
            cmd="echo hello",
            stdout="hello\n",
            stderr="",
            returncode=0,
            interrupted=False,
            allowlisted=True,
            quiet=True,
            logdir=None,  # No logdir
        )
        # Should show suppressed message but not saved to
        assert "quiet mode" in output.lower()
        assert "saved to" not in output.lower()
        assert "hello" not in output or "echo hello" in output  # command OK, output not

    def test_non_quiet_includes_output(self):
        """Test that non-quiet mode includes full output."""
        output = _format_shell_output(
            cmd="echo hello",
            stdout="hello\n",
            stderr="",
            returncode=0,
            interrupted=False,
            allowlisted=True,
            quiet=False,
        )
        assert "hello" in output  # stdout should be included
