"""Tests for issue #408: Shell tool output mixing between commands.

This tests the fix where output from previous commands could leak into
subsequent commands due to incomplete output draining before delimiter detection.

The fix uses a start marker mechanism to cleanly separate each command's output.
"""

import pytest

from gptme.tools.shell import ShellSession


@pytest.fixture
def shell():
    """Create a shell session for testing."""
    shell = ShellSession()
    yield shell
    shell.close()


def test_start_marker_not_in_output(shell):
    """Test that the start marker itself does not appear in stdout.

    The fix echoes a START_OF_COMMAND_OUTPUT marker before each command,
    then filters it out. This verifies the marker is properly filtered.
    """
    ret, out, err = shell.run("echo 'hello world'")
    assert ret == 0
    # The actual output should be 'hello world', not the start marker
    assert "START_OF_COMMAND" not in out
    assert "hello world" in out


def test_consecutive_commands_no_mixing(shell):
    """Test that output from consecutive commands doesn't mix.

    This is the core issue #408: running multiple commands in sequence
    should not leak output between them.
    """
    # First command with slow output
    ret1, out1, err1 = shell.run("echo 'first command output'")
    assert ret1 == 0
    assert "first command output" in out1
    assert "second command output" not in out1

    # Second command - should not contain first command's output
    ret2, out2, err2 = shell.run("echo 'second command output'")
    assert ret2 == 0
    assert "second command output" in out2
    assert "first command output" not in out2


def test_rapid_command_sequence(shell):
    """Test rapid sequence of commands for output isolation."""
    outputs = []
    for i in range(5):
        ret, out, err = shell.run(f"echo 'output_{i}'")
        assert ret == 0
        outputs.append(out)

    # Each output should only contain its own marker
    for i, out in enumerate(outputs):
        assert f"output_{i}" in out
        # Should not contain other outputs
        for j in range(5):
            if i != j:
                assert f"output_{j}" not in out, f"Output {i} contains output {j}"


def test_multiline_output_no_mixing(shell):
    """Test multiline output doesn't mix with subsequent commands."""
    # First command produces multiple lines
    ret1, out1, err1 = shell.run("echo -e 'line1\\nline2\\nline3'")
    assert ret1 == 0
    assert "line1" in out1
    assert "line2" in out1
    assert "line3" in out1

    # Second command should have clean output
    ret2, out2, err2 = shell.run("echo 'clean output'")
    assert ret2 == 0
    assert "clean output" in out2
    assert "line1" not in out2
    assert "line2" not in out2
    assert "line3" not in out2


def test_stderr_not_affected_by_start_marker(shell):
    """Test that stderr output is also properly isolated."""
    # Command that outputs to stderr
    ret1, out1, err1 = shell.run("echo 'stderr message' >&2")
    assert ret1 == 0
    assert "stderr message" in err1

    # Next command should have clean stderr
    ret2, out2, err2 = shell.run("echo 'stdout only'")
    assert ret2 == 0
    assert "stdout only" in out2
    assert "stderr message" not in err2


def test_long_running_command_isolation(shell):
    """Test that output from longer commands is properly isolated."""
    # First command with slight delay
    ret1, out1, err1 = shell.run("sleep 0.1 && echo 'delayed output'")
    assert ret1 == 0
    assert "delayed output" in out1

    # Immediate second command
    ret2, out2, err2 = shell.run("echo 'immediate output'")
    assert ret2 == 0
    assert "immediate output" in out2
    assert "delayed output" not in out2
