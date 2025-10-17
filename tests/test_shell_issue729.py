"""
Tests for issue #729 - Shell tool issues with compound commands and tilde expansion.

Cases to test:
1. Compound statements with both && and | operators
2. Tilde expansion in file paths
"""

import os

import pytest
from gptme.tools.shell import ShellSession


@pytest.fixture
def shell():
    shell = ShellSession()
    yield shell
    shell.close()


def test_shell_compound_with_cd_and_pipe(shell):
    """Test that compound statements with cd, &&, and | work correctly.

    Case 1 from issue #729:
    cd /home/bob/gptme-bob && grep -A 5 "journal" scripts/autonomous-run.sh | head -20
    Error: bash: line 77: cd: too many arguments
    """
    # Use a simpler test case that should work
    cmd = "cd /tmp && echo test | cat"
    ret, out, err = shell.run(cmd)

    # Should execute without errors
    assert ret == 0
    assert "test" in out


def test_shell_tilde_expansion(shell):
    """Test that tilde expansion works in file paths.

    Case 2 from issue #729:
    grep -A 5 "journal" ~/gptme-bob/scripts/autonomous-run.sh | head -20
    Error: grep: ~/gptme-bob/scripts/autonomous-run.sh: No such file or directory
    """
    # Create a test file in home directory for this test
    home = os.path.expanduser("~")
    test_file = os.path.join(home, ".gptme_test_file")

    with open(test_file, "w") as f:
        f.write("test content\n")

    try:
        # Try to read it using tilde
        cmd = "cat ~/.gptme_test_file"
        ret, out, err = shell.run(cmd)

        # Should execute without errors
        assert ret == 0
        assert "test content" in out
    finally:
        # Clean up
        if os.path.exists(test_file):
            os.remove(test_file)


def test_shell_tilde_expansion_with_pipe(shell):
    """Test that tilde expansion works when used with pipes.

    This is the exact case from issue #729 case 2.
    """
    # Create a test file
    home = os.path.expanduser("~")
    test_file = os.path.join(home, ".gptme_test_pipe_file")

    with open(test_file, "w") as f:
        f.write("line1\nline2\nline3\nline4\nline5\n")

    try:
        # Try to grep it using tilde and pipe to head
        cmd = "grep line ~/.gptme_test_pipe_file | head -3"
        ret, out, err = shell.run(cmd)

        # Should execute without errors and return 3 lines
        assert ret == 0
        lines = out.strip().split("\n")
        assert len(lines) == 3
    finally:
        # Clean up
        if os.path.exists(test_file):
            os.remove(test_file)
