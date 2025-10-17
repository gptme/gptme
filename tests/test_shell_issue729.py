"""Tests for shell tool issues reported in #729.

These test cases verify that the shell tool correctly handles:
1. File descriptor redirects (2>/dev/null, 2>&1, etc.)
2. Complex jq syntax with nested pipes and filters
3. Other edge cases in command parsing
"""

import json
import os
import tempfile
from collections.abc import Generator

import pytest

from gptme.tools.shell import ShellSession


@pytest.fixture
def shell() -> Generator[ShellSession, None, None]:
    shell = ShellSession()
    yield shell
    shell.close()


def test_stderr_redirect_with_pipe(shell):
    """Test that file descriptor redirects work with pipes.

    Issue: ls -la files* 2>/dev/null | head -20
    Error: ls: cannot access '2': No such file or directory

    The shell tool was treating '2>' as separate tokens instead of
    recognizing it as a stderr redirect operator.
    """
    code = "ls -la /tmp/nonexistent* 2>/dev/null | head -5"

    # Should complete without error (stderr redirected, stdout empty)
    returncode, stdout, stderr = shell.run(code)

    # Should not have "cannot access '2'" error
    assert "cannot access '2'" not in stderr.lower()
    assert "2: no such file" not in stderr.lower()

    # Return code should be 0 (success - stderr was redirected)
    # Note: This might be 141 (SIGPIPE) depending on head behavior, which is ok
    assert returncode in (0, 141)


def test_jq_complex_syntax_nested_pipe(shell):
    """Test jq with complex nested pipe and filter syntax.

    Issue: cat file.json | jq '.patterns[] | {type, description}'
    Error: bash: syntax error near unexpected token '('

    The shell tool was incorrectly parsing jq's filter syntax,
    treating internal pipes and parens as shell operators.
    """
    # Create a test JSON file
    test_data = {
        "patterns": [
            {"type": "test", "description": "test pattern", "frequency": 10},
            {"type": "demo", "description": "demo pattern", "frequency": 5},
        ]
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(test_data, f)
        temp_file = f.name

    try:
        # Test complex jq syntax with nested pipes and object construction
        code = f"cat {temp_file} | jq '.patterns[] | {{type, description, frequency}}'"

        returncode, stdout, stderr = shell.run(code)

        # Should not have syntax errors
        assert "syntax error" not in stderr.lower()
        assert "unexpected token" not in stderr.lower()

        # Should contain the data from the JSON
        assert "test" in stdout or "demo" in stdout
        assert returncode == 0
    finally:
        os.unlink(temp_file)


def test_jq_function_in_filter(shell):
    """Test jq with function calls in filter syntax.

    Issue: curl ... | jq ".data.result | length"
    Error: bash: length: command not found

    The shell tool was treating jq's 'length' function as a separate
    bash command instead of part of the jq filter.
    """
    test_data = {"data": {"result": [1, 2, 3, 4, 5]}}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(test_data, f)
        temp_file = f.name

    try:
        code = f"cat {temp_file} | jq '.data.result | length'"

        returncode, stdout, stderr = shell.run(code)

        # Should not treat 'length' as command
        assert "length: command not found" not in stderr.lower()
        assert "length: not found" not in stderr.lower()

        # Should output the length (5)
        assert "5" in stdout
        assert returncode == 0
    finally:
        os.unlink(temp_file)


def test_stdout_and_stderr_redirect(shell):
    """Test combined stdout and stderr redirect operators.

    Tests various forms:
    - 2>&1 (stderr to stdout)
    """
    code = "echo 'test' 2>&1 | cat"

    returncode, stdout, stderr = shell.run(code)

    # Should execute successfully
    assert "test" in stdout
    assert returncode == 0

    # Should not treat '2' or '1' as separate arguments
    assert "cannot access '2'" not in stderr.lower()
    assert "cannot access '1'" not in stderr.lower()


def test_fd_redirect_numbers(shell):
    """Test that file descriptor numbers aren't treated as arguments.

    Tests patterns like 3>&1, 4>&2, etc.
    """
    # Redirect fd 3 to stdout, write to fd 3
    code = "exec 3>&1; echo 'test' >&3"

    returncode, stdout, stderr = shell.run(code)

    # Should execute successfully
    assert returncode == 0

    # Should not treat '3' as argument
    assert "cannot access '3'" not in stderr.lower()


def test_ampersand_redirect(shell):
    """Test &> and &>> redirect operators (both stdout and stderr).

    These are bash-specific operators for redirecting both streams.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        temp_file = f.name

    try:
        # Test &> (redirect both stdout and stderr)
        code = f"(echo 'stdout'; echo 'stderr' >&2) &> {temp_file}"

        returncode, stdout, stderr = shell.run(code)

        # Should execute successfully
        assert returncode == 0

        # Check file contains both streams
        with open(temp_file) as f:
            content = f.read()
            assert "stdout" in content
            assert "stderr" in content
    finally:
        os.unlink(temp_file)
