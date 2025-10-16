import os
import tempfile
from collections.abc import Generator

import pytest
from gptme.tools.shell import (
    ShellSession,
    _shorten_stdout,
    is_denylisted,
    split_commands,
)


@pytest.fixture
def shell() -> Generator[ShellSession, None, None]:
    shell = ShellSession()
    yield shell
    shell.close()
    # Don't change directories - let each test manage its own directory state


def test_echo(shell):
    ret, out, err = shell.run("echo 'Hello World!'")
    assert err.strip() == ""  # Expecting no stderr
    assert out.strip() == "Hello World!"  # Expecting stdout to be "Hello World!"
    assert ret == 0


def test_echo_multiline(shell):
    # Test multiline and trailing + leading whitespace
    ret, out, err = shell.run("echo 'Line 1  \n  Line 2'")
    assert err.strip() == ""
    assert out.strip() == "Line 1  \n  Line 2"
    assert ret == 0

    # Test basic heredoc (<<)
    ret, out, err = shell.run(
        """
cat << EOF
Hello
World
EOF
"""
    )
    assert err.strip() == ""
    assert out.strip() == "Hello\nWorld"
    assert ret == 0

    # Test stripped heredoc (<<-)
    ret, out, err = shell.run(
        """
cat <<- EOF
Hello
World
EOF
"""
    )
    assert err.strip() == ""
    assert out.strip() == "Hello\nWorld"
    assert ret == 0

    # Test here-string (<<<)
    ret, out, err = shell.run("cat <<< 'Hello World'")
    assert err.strip() == ""
    assert out.strip() == "Hello World"
    assert ret == 0


def test_cd(shell):
    # Run a cd command
    ret, out, err = shell.run("cd /tmp")
    assert err.strip() == ""  # Expecting no stderr
    assert ret == 0

    # Check the current directory
    ret, out, err = shell.run("pwd")
    assert err.strip() == ""  # Expecting no stderr
    assert out.strip() == "/tmp"  # Should be in /tmp now
    assert ret == 0


def test_shell_cd_chdir(shell):
    # make a tmp dir
    tmpdir = tempfile.TemporaryDirectory()
    # test that running cd in the shell changes the directory
    shell.run(f"cd {tmpdir.name}")
    _, output, _ = shell.run("pwd")
    try:
        cwd = os.getcwd()
        assert cwd == os.path.realpath(tmpdir.name)
        assert cwd == os.path.realpath(output.strip())
    finally:
        tmpdir.cleanup()


def test_split_commands():
    script = """
# This is a comment
ls -l
echo "Hello, World!"
echo "This is a
multiline command"
"""
    commands = split_commands(script)
    for command in commands:
        print(command)
    assert len(commands) == 3

    script_loop = "for i in {1..10}; do echo $i; done"
    commands = split_commands(script_loop)
    assert len(commands) == 1


def test_heredoc_complex(shell):
    # Test nested heredocs
    ret, out, err = shell.run(
        """
cat << OUTER
This is the outer heredoc
$(cat << INNER
This is the inner heredoc
INNER
)
OUTER
"""
    )
    assert err.strip() == ""
    assert out.strip() == "This is the outer heredoc\nThis is the inner heredoc"
    assert ret == 0

    # Test heredoc with variable substitution
    ret, out, err = shell.run(
        """
NAME="World"
cat << EOF
Hello, $NAME!
EOF
"""
    )
    assert err.strip() == ""
    assert out.strip() == "Hello, World!"
    assert ret == 0


def test_heredoc_quoted_delimiters(shell):
    # Test heredoc with single-quoted delimiter
    ret, out, err = shell.run(
        """cat <<'EOF'
some content with single quotes
EOF"""
    )
    assert err.strip() == ""
    assert out.strip() == "some content with single quotes"
    assert ret == 0

    # Test heredoc with double-quoted delimiter
    ret, out, err = shell.run(
        """cat <<"EOF"
some content with double quotes
EOF"""
    )
    assert err.strip() == ""
    assert out.strip() == "some content with double quotes"
    assert ret == 0

    # Test that quoted delimiters prevent variable expansion
    ret, out, err = shell.run(
        """
VAR="expanded"
cat <<'EOF'
This $VAR should not be expanded
EOF"""
    )
    assert err.strip() == ""
    assert out.strip() == "This $VAR should not be expanded"
    assert ret == 0


def test_heredoc_quoted_delimiters_with_spaces(shell):
    # Test heredoc with space before single-quoted delimiter
    ret, out, err = shell.run(
        """cat > /tmp/test_space.sh << 'EOF'
#!/bin/bash
echo "This is a test with space before quoted delimiter"
EOF
cat /tmp/test_space.sh && rm /tmp/test_space.sh"""
    )
    assert ret == 0
    assert "This is a test with space before quoted delimiter" in out

    # Test heredoc with space before double-quoted delimiter
    ret, out, err = shell.run(
        """cat > /tmp/test_space2.sh << "EOF"
#!/bin/bash
echo "This is a test with space before double-quoted delimiter"
EOF
cat /tmp/test_space2.sh && rm /tmp/test_space2.sh"""
    )
    assert ret == 0
    assert "This is a test with space before double-quoted delimiter" in out


def test_split_commands_heredoc_quoted():
    # Test that split_commands can handle quoted heredoc delimiters
    script_single = """cat <<'EOF'
content
EOF"""
    commands = split_commands(script_single)
    assert len(commands) == 1
    assert "<<'EOF'" in commands[0]

    script_double = """cat <<"EOF"
content
EOF"""
    commands = split_commands(script_double)
    assert len(commands) == 1
    assert '<<"EOF"' in commands[0]

    # Test mixed commands with quoted heredocs
    script_mixed = """echo "before"
cat <<'EOF'
heredoc content
EOF
echo "after" """
    commands = split_commands(script_mixed)
    assert len(commands) == 3
    assert any("<<'EOF'" in cmd for cmd in commands)


def test_split_commands_heredoc_quoted_with_spaces():
    # Test that split_commands can handle quoted heredoc delimiters with spaces
    script_space_single = """cat > /tmp/test.sh << 'EOF'
#!/bin/bash
content
EOF"""
    commands = split_commands(script_space_single)
    assert len(commands) == 1
    assert "<<'EOF'" in commands[0]

    script_space_double = """cat > /tmp/test.sh << "EOF"
#!/bin/bash
content
EOF"""
    commands = split_commands(script_space_double)
    assert len(commands) == 1
    assert '<<"EOF"' in commands[0]


def test_function(shell):
    script = """
function hello() {
    echo "Hello, World!"
}
hello
"""
    ret, out, err = shell.run(script)
    assert ret == 0
    assert out.strip() == "Hello, World!"


def test_pipeline(shell):
    script = """
echo "Hello, World!" | wc -w
"""
    ret, out, err = shell.run(script)
    assert ret == 0
    assert out.strip() == "2"


def test_shorten_stdout_timestamp():
    s = """2021-09-02T08:48:43.123Z
2021-09-02T08:48:43.123Z
"""
    assert _shorten_stdout(s, strip_dates=True) == "\n\n"


def test_shorten_stdout_common_prefix():
    s = """foo 1
foo 2
foo 3
foo 4
foo 5"""
    assert _shorten_stdout(s, strip_common_prefix_lines=5) == "1\n2\n3\n4\n5"


def test_shorten_stdout_indent():
    # check that indentation is preserved
    s = """
l1 without indent
    l2 with indent
""".strip()
    assert _shorten_stdout(s) == s


def test_shorten_stdout_blanklines():
    s = """l1

l2"""
    assert _shorten_stdout(s) == s


def test_is_denylisted_pattern_matches():
    """Test that commands matching the deny group patterns are properly handled."""

    # Test commands that should match the regex patterns in deny_groups
    pattern_matching_commands = [
        "git add .",
        "git add -A",
        "git add --all",
        "git commit -a",
        "git commit --all",
        "rm -rf /",
        "sudo rm -rf /",  # Fixed: this actually matches the pattern
        "rm -rf *",
        "chmod -R 777",
        "chmod 777",
    ]

    for cmd in pattern_matching_commands:
        is_denied, reason, matched_cmd = is_denylisted(cmd)
        assert is_denied, f"Pattern-matching command should be denied: {cmd}"
        assert reason is not None, f"Should have reason for: {cmd}"
        assert matched_cmd is not None, f"Should have matched command for: {cmd}"


def test_is_denylisted_git_bulk_operations():
    """Test that git bulk operations are properly denied with correct reason."""

    dangerous_git_commands = [
        "git add .",
        "git add -A",
        "git add --all",
        "git commit -a",
        "git commit --all",
        "Git Add .",  # case insensitive
        "  git   add   .  ",  # whitespace normalization
    ]

    expected_reason = "Instead of bulk git operations, use selective commands: `git add <specific-files>` to stage only intended files, then `git commit`."

    for cmd in dangerous_git_commands:
        is_denied, reason, matched_cmd = is_denylisted(cmd)
        assert is_denied, f"Command should be denied: {cmd}"
        assert reason == expected_reason, f"Wrong reason for: {cmd}"
        assert matched_cmd is not None, f"Should have matched command for: {cmd}"


def test_is_denylisted_destructive_file_operations():
    """Test that destructive file operations are properly denied with correct reason."""

    dangerous_file_commands = [
        "rm -rf /",
        "sudo rm -rf /",
        "rm -rf *",
        "RM -RF /",  # case insensitive
    ]

    expected_reason = "Destructive file operations are blocked. Specify exact paths and avoid operations that could delete system files or entire directories."

    for cmd in dangerous_file_commands:
        is_denied, reason, matched_cmd = is_denylisted(cmd)
        assert is_denied, f"Command should be denied: {cmd}"
        assert reason == expected_reason, f"Wrong reason for: {cmd}"
        assert matched_cmd is not None, f"Should have matched command for: {cmd}"


def test_is_denylisted_dangerous_permissions():
    """Test that dangerous permission operations are properly denied with correct reason."""

    dangerous_chmod_commands = [
        "chmod 777",
        "chmod -R 777",
        "chmod 777 file.txt",
        "CHMOD 777",  # case insensitive
    ]

    expected_reason = "Overly permissive chmod operations are blocked. Use safer permissions like `chmod 755` or `chmod 644` and be specific about target files."

    for cmd in dangerous_chmod_commands:
        is_denied, reason, matched_cmd = is_denylisted(cmd)
        assert is_denied, f"Command should be denied: {cmd}"
        assert reason == expected_reason, f"Wrong reason for: {cmd}"
        assert matched_cmd is not None, f"Should have matched command for: {cmd}"


def test_is_denylisted_safe_commands():
    """Test that safe commands are allowed through."""

    safe_commands = [
        "git add specific-file.py",
        "git add src/file.py tests/test.py",
        "git commit -m 'message'",
        "git status",
        "chmod 755 file.txt",
        "chmod 644 config.json",
        "rm specific-file.txt",
        "rm -rf build/",  # specific directory, not root
        "ls -la",
        "echo 'hello'",
        "git push --force-with-lease",  # force-with-lease is safer than --force
    ]

    for cmd in safe_commands:
        is_denied, reason, matched_cmd = is_denylisted(cmd)
        assert not is_denied, f"Safe command should be allowed: {cmd}"
        assert reason is None, f"Safe command should have no reason: {cmd}"
        assert (
            matched_cmd is None
        ), f"Safe command should have no matched command: {cmd}"


def test_is_denylisted_edge_cases():
    """Test edge cases and boundary conditions."""

    # Test that similar but safe variations are allowed
    safe_variations = [
        "git add file.py",  # specific file, not bulk
        "git add src/",  # specific directory, not all
        "git add .gitignore",  # dotfile, not current directory
        "git add .github/workflows/build.yml",  # dotfile in subdirectory
        "chmod 755",  # safe permissions
        "rm -rf build/target/",  # specific path, not root
        "git commit --amend",  # different flag
    ]

    for cmd in safe_variations:
        is_denied, reason, matched_cmd = is_denylisted(cmd)
        assert not is_denied, f"Safe variation should be allowed: {cmd}"
        assert reason is None, f"Safe variation should have no reason: {cmd}"
        assert (
            matched_cmd is None
        ), f"Safe variation should have no matched command: {cmd}"


def test_is_denylisted_quoted_content():
    """Test that dangerous patterns in quoted strings are allowed."""

    # Test single-quoted strings containing dangerous patterns
    safe_quoted_commands = [
        "echo 'git add .'",
        "echo 'rm -rf /'",
        "git commit -m 'Added git add . support'",
        'echo "chmod 777"',
        'echo "git commit -a"',
        "printf 'Avoid using git add -A\\n'",
        'echo "Never run rm -rf *"',
        "echo 'Command: git add --all'",
    ]

    for cmd in safe_quoted_commands:
        is_denied, reason, matched_cmd = is_denylisted(cmd)
        assert not is_denied, f"Quoted dangerous pattern should be allowed: {cmd}"
        assert reason is None, f"Should have no reason for quoted content: {cmd}"
        assert (
            matched_cmd is None
        ), f"Should have no matched command for quoted content: {cmd}"


def test_is_denylisted_mixed_quoted_and_actual():
    """Test commands that mix safe quoted content with actual dangerous commands."""

    # Commands that should still be denied despite having quotes elsewhere
    dangerous_with_quotes = [
        "echo 'safe' && git add .",
        'git add . && echo "safe"',
        "git add . # comment with 'quotes'",
    ]

    for cmd in dangerous_with_quotes:
        is_denied, reason, matched_cmd = is_denylisted(cmd)
        assert is_denied, f"Actual dangerous command should be denied: {cmd}"
        assert reason is not None, f"Should have reason for dangerous command: {cmd}"
        assert matched_cmd is not None, f"Should have matched command: {cmd}"


def test_is_denylisted_escaped_quotes():
    """Test handling of escaped quotes."""

    # Commands with escaped quotes should still work correctly
    safe_escaped = [
        r"echo 'It'\''s safe to say: git add .'",  # Single quote escape within single quotes
        r'echo "She said \"git add .\" is dangerous"',  # Escaped double quotes
    ]

    for cmd in safe_escaped:
        is_denied, reason, matched_cmd = is_denylisted(cmd)
        assert not is_denied, f"Escaped quoted content should be allowed: {cmd}"
        assert (
            reason is None
        ), f"Should have no reason for escaped quoted content: {cmd}"
        assert (
            matched_cmd is None
        ), f"Should have no matched command for escaped quoted content: {cmd}"


def test_is_denylisted_heredoc():
    """Test handling of heredoc syntax."""

    # Heredocs with various delimiter styles should not trigger on content
    safe_heredoc_commands = [
        # Basic heredoc
        """cat << EOF
git add .
rm -rf /
chmod 777
EOF""",
        # Single-quoted delimiter (literal)
        """cat << 'EOF'
git add .
rm -rf /
chmod 777
EOF""",
        # Double-quoted delimiter
        """cat << "EOF"
git add .
rm -rf /
chmod 777
EOF""",
        # Heredoc with leading tab strip
        """cat <<- EOF
git add .
rm -rf /
chmod 777
EOF""",
        # Heredoc in middle of command
        """echo "before" && cat << EOF
git add .
EOF
echo "after" """,
        # Multiple heredocs
        """cat << EOF1
git add .
EOF1
cat << EOF2
rm -rf /
EOF2""",
        # Real-world example: creating a script
        """cat > script.sh << 'EOF'
#!/bin/bash
# This script documents dangerous commands
# Never use: git add .
# Never use: rm -rf /
EOF""",
    ]

    for cmd in safe_heredoc_commands:
        is_denied, reason, matched_cmd = is_denylisted(cmd)
        assert not is_denied, f"Heredoc content should be allowed: {cmd[:50]}..."
        assert (
            reason is None
        ), f"Should have no reason for heredoc content: {cmd[:50]}..."
        assert (
            matched_cmd is None
        ), f"Should have no matched command for heredoc: {cmd[:50]}..."


def test_is_denylisted_heredoc_with_actual_command():
    """Test that actual dangerous commands before/after heredocs are still caught."""

    dangerous_with_heredoc = [
        # Dangerous command before heredoc
        """git add . && cat << EOF
This is safe content
EOF""",
        # Dangerous command after heredoc
        """cat << EOF
This is safe content
EOF
git add .""",
        # Dangerous command between heredocs
        """cat << EOF1
safe
EOF1
git add .
cat << EOF2
safe
EOF2""",
    ]

    for cmd in dangerous_with_heredoc:
        is_denied, reason, matched_cmd = is_denylisted(cmd)
        assert is_denied, f"Actual dangerous command should be denied: {cmd[:50]}..."
        assert (
            reason is not None
        ), f"Should have reason for dangerous command: {cmd[:50]}..."
        assert matched_cmd is not None, f"Should have matched command: {cmd[:50]}..."


def test_heredoc_in_compound_command(shell):
    """Test that heredocs work correctly in compound commands with &&."""
    # Issue #703: This should not get stuck
    ret, out, err = shell.run(
        """echo "test" && python3 <<'EOF'
print('0')
EOF"""
    )
    assert ret == 0
    assert "test" in out
    assert "0" in out
    # Commented out due to weird error in CI:
    # pytest-cov: Failed to setup subprocess coverage. Environ: {'COV_CORE_DATAFILE': ...} Exception: FileNotFoundError(2, 'No such file or directory')"
    # assert err.strip() == ""


def test_pipe_with_stdin_consuming_command(shell):
    """Test that piping commands that consume stdin doesn't hang (issue #684).

    Reproduces the specific failing case from Erik's comment:
    gptme "/shell gptme '/exit' | grep Assistant"

    The script simulates gptme's behavior:
    - Without stdin redirection: blocks reading from pipe stdin
    - With stdin redirected to /dev/null: prints "Assistant" immediately

    This test would hang without the fix that redirects stdin for the first
    command in a pipeline.
    """
    # Create test script that simulates gptme's stdin behavior
    test_script = """#!/usr/bin/env python3
import sys
import os

# Check if stdin is /dev/null
try:
    stdin_stat = os.fstat(sys.stdin.fileno())
    devnull_stat = os.stat('/dev/null')
    is_devnull = (stdin_stat.st_dev == devnull_stat.st_dev and
                  stdin_stat.st_ino == devnull_stat.st_ino)
except:
    is_devnull = False

if not is_devnull and not sys.stdin.isatty():
    # stdin is a pipe (not /dev/null, not terminal)
    # This would block forever without stdin redirection
    sys.stdin.read(1)
    print("blocked")
else:
    # stdin is /dev/null or terminal - works correctly
    print("Assistant")
"""

    # Write test script
    shell.run("cat > /tmp/test_stdin_block.py << 'EOF'\n" + test_script + "\nEOF")
    shell.run("chmod +x /tmp/test_stdin_block.py")

    # This is the actual failing case: command that blocks on stdin | grep
    # Without the fix, this would hang because the script waits for stdin
    # With the fix, stdin is redirected to /dev/null for the first command
    ret_code, stdout, stderr = shell.run(
        "python3 /tmp/test_stdin_block.py | grep Assistant",
        output=False,
        timeout=5.0,
    )

    assert ret_code == 0
    assert "Assistant" in stdout
