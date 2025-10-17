"""Integration tests for shell command validation in shell tool."""

import os

import pytest

from gptme.tools.shell import execute_shell


def dummy_confirm(msg: str) -> bool:
    """Dummy confirmation function for testing."""
    return True


@pytest.fixture
def clean_env():
    """Clean up environment variables before each test."""
    old_validate = os.environ.get("GPTME_SHELL_VALIDATE")
    yield
    if old_validate is not None:
        os.environ["GPTME_SHELL_VALIDATE"] = old_validate
    elif "GPTME_SHELL_VALIDATE" in os.environ:
        del os.environ["GPTME_SHELL_VALIDATE"]


def test_validation_warn_mode_default(clean_env):
    """Test that validation runs in warn mode by default."""
    # Command with bare variable (should trigger warning)
    cmd = "echo LLM_API_TIMEOUT"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should get a warning message
    assert len(messages) >= 1
    assert any("validation warnings" in msg.content.lower() for msg in messages)


def test_validation_strict_mode_blocks(clean_env):
    """Test that validation blocks execution in strict mode."""
    os.environ["GPTME_SHELL_VALIDATE"] = "strict"

    # Command with bare variable (should trigger warning and block)
    cmd = "echo LLM_API_TIMEOUT"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should get a blocking message
    assert len(messages) == 1
    assert "blocked" in messages[0].content.lower()
    assert "validation warnings" in messages[0].content.lower()


def test_validation_off_mode_skips(clean_env):
    """Test that validation is skipped in off mode."""
    os.environ["GPTME_SHELL_VALIDATE"] = "off"

    # Command with bare variable (should NOT trigger warning)
    cmd = "echo test"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should execute without validation warnings
    assert not any("validation" in msg.content.lower() for msg in messages)


def test_validation_with_python_invocation(clean_env):
    """Test validation catches python invocation issues."""
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Command using 'python' instead of 'python3'
    cmd = "python --version"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should get a warning about python invocation
    assert any(
        "python" in msg.content.lower() and "python3" in msg.content.lower()
        for msg in messages
    )


def test_validation_with_path_quoting(clean_env):
    """Test validation catches path quoting issues."""
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Command with unquoted path containing spaces
    cmd = "cd /path with spaces"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should get a warning about path quoting
    assert any(
        "path" in msg.content.lower() or "quote" in msg.content.lower()
        for msg in messages
    )


def test_validation_with_directory_path(clean_env):
    """Test validation catches incorrect directory paths."""
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Command with incorrect directory structure
    cmd = "cd /home/bob/Programming/gptme"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should get a warning about directory structure
    assert any(
        "directory" in msg.content.lower() or "programming" in msg.content.lower()
        for msg in messages
    )


def test_validation_clean_command_passes(clean_env):
    """Test that clean commands pass validation without warnings."""
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Clean command that should pass all validations
    cmd = "echo 'Hello World'"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should not get validation warnings (only normal output)
    # Check that no message contains validation-related keywords
    validation_messages = [
        msg
        for msg in messages
        if "validation" in msg.content.lower() or "warning" in msg.content.lower()
    ]
    assert len(validation_messages) == 0


def test_validation_with_multiple_issues(clean_env):
    """Test validation reports multiple issues in one command."""
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Command with multiple issues
    cmd = "python script.py && cd /path with spaces && echo LLM_TIMEOUT"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should get warnings for multiple issues
    warning_msgs = [msg for msg in messages if "warning" in msg.content.lower()]
    assert len(warning_msgs) > 0

    # Should mention multiple issues
    combined = " ".join(msg.content for msg in warning_msgs)
    assert "python" in combined.lower()


def test_validation_suggestions_included(clean_env):
    """Test that validation warnings include helpful suggestions."""
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Command with bare variable
    cmd = "echo LLM_API_TIMEOUT"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should include suggestion to use $VAR
    warning_msg = next(
        (msg for msg in messages if "warning" in msg.content.lower()), None
    )
    assert warning_msg is not None
    assert "suggestion" in warning_msg.content.lower() or "$" in warning_msg.content
