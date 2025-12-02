"""Tests for shell quiet mode (Issue #44)."""

from gptme.tools.shell import _format_shell_output


class TestShellQuietMode:
    """Test the quiet parameter for shell output suppression."""

    def test_quiet_suppresses_stdout(self):
        """Test that quiet mode suppresses stdout output."""
        output = _format_shell_output(
            cmd="echo hello",
            stdout="hello\n",
            stderr="",
            returncode=0,
            interrupted=False,
            allowlisted=True,
            quiet=True,
        )
        assert "hello" not in output  # stdout should be suppressed
        assert "quiet mode" in output.lower()
        assert "echo hello" in output  # command should still be shown

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
