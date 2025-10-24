"""Tests for shell tool issues reported in #772.

These test cases verify that the shell tool correctly handles:
1. Logical OR operators (||)
2. Combinations of || with pipe operators (|)
3. Complex command chaining with mixed operators
"""

import tempfile
from collections.abc import Generator

import pytest

from gptme.tools.shell import ShellSession


@pytest.fixture
def shell() -> Generator[ShellSession, None, None]:
    shell = ShellSession()
    yield shell
    shell.close()


def test_logical_or_with_pipe(shell):
    """Test that logical OR (||) followed by pipe (|) works correctly.

    Issue: cat .env.local 2>/dev/null || cat .env.example | head -20
    Error: bash: line 33: syntax error near unexpected token `|'
           bash: line 33: `cat .env.local 2>/dev/null < /dev/null | | cat ...`

    The shell tool was treating the first | in || as a pipe operator,
    causing it to split the command incorrectly and create double pipes.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("line1\nline2\nline3\nline4\nline5\n")
        temp_file = f.name

    try:
        # Test || followed by | in the same command
        code = f"cat /nonexistent 2>/dev/null || cat {temp_file} | head -3"

        returncode, stdout, stderr = shell.run(code)

        # Should not have syntax errors about pipes
        assert "syntax error near unexpected token" not in stderr.lower()
        assert "| |" not in stderr  # No double pipes

        # Should output first 3 lines from the fallback file
        lines = stdout.strip().split("\n")
        assert len(lines) == 3
        assert "line1" in stdout
        assert returncode == 0
    finally:
        import os

        os.unlink(temp_file)


def test_logical_or_simple(shell):
    """Test simple logical OR operator without pipes.

    Verify that || operator works correctly in basic cases.
    """
    code = "false || echo 'fallback'"

    returncode, stdout, stderr = shell.run(code)

    # Should execute fallback command
    assert "fallback" in stdout
    assert returncode == 0


def test_logical_or_with_file_redirect(shell):
    """Test logical OR with stderr redirection.

    This is the core case from Issue #772: checking if a file exists
    with stderr redirected, with a fallback command.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("fallback content\n")
        temp_file = f.name

    try:
        # Try to read non-existent file, fallback to existing file
        code = f"cat /nonexistent 2>/dev/null || cat {temp_file}"

        returncode, stdout, stderr = shell.run(code)

        # Should execute fallback and read temp file
        assert "fallback content" in stdout
        assert returncode == 0

        # Should not have stderr about /nonexistent (redirected)
        assert "nonexistent" not in stderr.lower()
    finally:
        import os

        os.unlink(temp_file)


def test_multiple_logical_or(shell):
    """Test multiple || operators in sequence.

    Verify that chains of || operators are handled correctly.
    """
    code = "false || false || echo 'third try'"

    returncode, stdout, stderr = shell.run(code)

    assert "third try" in stdout
    assert returncode == 0


def test_logical_or_and_logical_and(shell):
    """Test combination of || and && operators.

    Verify that both logical operators can coexist in the same command.
    """
    code = "true && echo 'success' || echo 'failure'"

    returncode, stdout, stderr = shell.run(code)

    # Should execute first echo (true && echo)
    assert "success" in stdout
    # Should not execute fallback
    assert "failure" not in stdout
    assert returncode == 0


def test_pipe_after_logical_or_with_stderr(shell):
    """Test the exact problematic pattern from Issue #772.

    cat file1 2>/dev/null || cat file2 | head -20

    This combines:
    - stderr redirect (2>/dev/null)
    - logical OR (||)
    - pipe operator (|)
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        # Create file with many lines
        for i in range(30):
            f.write(f"line{i}\n")
        temp_file = f.name

    try:
        code = f"cat /nonexistent 2>/dev/null || cat {temp_file} | head -5"

        returncode, stdout, stderr = shell.run(code)

        # Should not have syntax errors
        assert "syntax error" not in stderr.lower()
        assert "unexpected token" not in stderr.lower()

        # Should output first 5 lines
        lines = stdout.strip().split("\n")
        assert len(lines) == 5
        assert "line0" in stdout
        assert "line4" in stdout
        # Should NOT have line5 or beyond (limited by head -5)
        assert "line5" not in stdout

        # Return code should be 0 or 141 (SIGPIPE from head)
        assert returncode in (0, 141)
    finally:
        import os

        os.unlink(temp_file)
