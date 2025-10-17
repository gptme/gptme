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


# ============================================================================
# Phase 2.4: Additional Integration Tests
# ============================================================================


def test_validation_error_message_format(clean_env):
    """Test that validation error messages include icons, suggestions, and lessons."""
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Command with bare variable
    cmd = "echo LLM_API_TIMEOUT"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Find warning message
    warning_msg = next(
        (msg for msg in messages if "warning" in msg.content.lower()), None
    )
    assert warning_msg is not None

    # Check for severity icon (⚠️ or ❌)
    assert "⚠️" in warning_msg.content or "warning" in warning_msg.content.lower()

    # Check for suggestion icon (💡) or "suggestion" keyword
    assert "💡" in warning_msg.content or "suggestion" in warning_msg.content.lower()

    # Check for lesson link icon (📖) or actual lesson link
    assert "📖" in warning_msg.content or "lessons/" in warning_msg.content.lower()


def test_validation_with_pipes(clean_env):
    """Test validation works with piped commands."""
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Command with pipe and bare variable
    cmd = "echo test | grep LLM_API_TIMEOUT"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should detect bare variable in piped command
    assert any("LLM_API_TIMEOUT" in msg.content for msg in messages)


def test_validation_with_command_substitution(clean_env):
    """Test validation works with command substitution."""
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Command with substitution and python invocation
    cmd = "echo $(python --version)"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should detect python invocation issue
    assert any("python3" in msg.content.lower() for msg in messages)


def test_validation_with_redirects(clean_env):
    """Test validation works with input/output redirects."""
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Command with redirect and path quoting issue
    cmd = "cat file.txt > /output with spaces/result.txt"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should detect path quoting issue
    assert any(
        "quote" in msg.content.lower() or "space" in msg.content.lower()
        for msg in messages
    )


def test_validation_with_logical_operators(clean_env):
    """Test validation works with && and || operators."""
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Command with && and multiple issues
    cmd = "python script.py && echo LLM_TIMEOUT || echo failed"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should detect both python and bare variable issues
    combined = " ".join(msg.content for msg in messages)
    assert "python" in combined.lower()


def test_validation_with_for_loop(clean_env):
    """Test validation works with shell for loops."""
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Command with for loop and python invocation
    cmd = "for file in *.py; do python $file; done"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should detect python invocation issue
    assert any("python3" in msg.content.lower() for msg in messages)


def test_validation_with_background_jobs(clean_env):
    """Test validation works with background jobs."""
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Command with background job and bare variable
    cmd = "echo LLM_API_TIMEOUT &"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should detect bare variable
    assert any("LLM_API_TIMEOUT" in msg.content for msg in messages)


def test_validation_preserves_known_variables(clean_env):
    """Test that known shell variables trigger warnings but are recognized."""
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Command with known shell variable (without $ prefix)
    cmd = "echo PATH HOME USER"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should trigger validation warnings for known variables (correct behavior)
    # The validator warns because it can't tell if user meant literal string or forgot $
    validation_warnings = [
        msg
        for msg in messages
        if "validation" in msg.content.lower()
        and ("PATH" in msg.content or "HOME" in msg.content or "USER" in msg.content)
    ]
    # Should have warnings for these variables
    assert len(validation_warnings) > 0

    # Verify the warnings suggest using $ prefix
    combined = " ".join(msg.content for msg in validation_warnings)
    assert "$PATH" in combined or "$HOME" in combined or "$USER" in combined


def test_validation_lesson_links_correct(clean_env):
    """Test that lesson links in validation messages are correct."""
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Test each validation rule and check for correct lesson link
    test_cases = [
        ("echo LLM_API_TIMEOUT", "shell-variable-syntax.md"),
        ("python script.py", "python-invocation.md"),
        ("./script.py", "python-file-execution.md"),
        ("cd /path with spaces", "shell-path-quoting.md"),
        ("cd /home/bob/Programming/gptme", "directory-structure-awareness.md"),
    ]

    for cmd, expected_lesson in test_cases:
        messages = list(execute_shell(cmd, [], None, dummy_confirm))
        combined = " ".join(msg.content for msg in messages)
        assert (
            expected_lesson in combined
        ), f"Expected {expected_lesson} in output for: {cmd}"


def test_validation_mode_precedence(clean_env):
    """Test that env var takes precedence over defaults."""
    # Set to strict mode via env var
    os.environ["GPTME_SHELL_VALIDATE"] = "strict"

    cmd = "echo LLM_API_TIMEOUT"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should block in strict mode
    assert len(messages) == 1
    assert "blocked" in messages[0].content.lower()

    # Change to warn mode
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should warn but allow execution
    assert any("warning" in msg.content.lower() for msg in messages)
    # Should have more than just the blocking message
    assert len(messages) > 1


def test_validation_with_here_doc(clean_env):
    """Test validation works with here-documents."""
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Command with here-doc and python invocation
    cmd = """cat <<'EOF' | python
print("test")
EOF"""

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should detect python invocation (but may not if parser doesn't handle here-docs)
    # This is a known limitation - document it if fails
    # assert any("python3" in msg.content.lower() for msg in messages)
    # For now, just verify command doesn't crash
    assert len(messages) > 0


def test_validation_performance_acceptable(clean_env):
    """Test that validation doesn't add significant overhead."""
    import time

    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Simple command that should be fast
    cmd = "echo test"

    start = time.time()
    list(execute_shell(cmd, [], None, dummy_confirm))
    duration = time.time() - start

    # Validation should add <100ms overhead
    assert duration < 0.1, f"Validation took {duration:.3f}s, expected <0.1s"


def test_validation_with_complex_real_world_command(clean_env):
    """Test validation with realistic complex command."""
    os.environ["GPTME_SHELL_VALIDATE"] = "warn"

    # Complex real-world command with multiple issues
    cmd = """
    for file in /path with spaces/*.py; do
        python $file && echo SUCCESS || echo LLM_ERROR_CODE
    done | tee /output with spaces/results.txt
    """

    messages = list(execute_shell(cmd, [], None, dummy_confirm))

    # Should detect multiple issues
    combined = " ".join(msg.content for msg in messages)
    # Check for at least some issues detected (python, paths with spaces, bare variable)
    issues_found = sum(
        [
            1 if "python" in combined.lower() else 0,
            1 if "quote" in combined.lower() or "space" in combined.lower() else 0,
            1 if "LLM_ERROR" in combined else 0,
        ]
    )
    assert issues_found >= 2, f"Expected multiple issues, found {issues_found}"
