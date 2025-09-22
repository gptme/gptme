from gptme.tools.shell import _shorten_stdout
import tempfile
import os
from collections.abc import Generator

import pytest
from gptme.tools.shell import ShellSession, split_commands


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
    ret, out, err = shell.run("""
cat << EOF
Hello
World
EOF
""")
    assert err.strip() == ""
    assert out.strip() == "Hello\nWorld"
    assert ret == 0

    # Test stripped heredoc (<<-)
    ret, out, err = shell.run("""
cat <<- EOF
Hello
World
EOF
""")
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
    ret, out, err = shell.run("""
cat << OUTER
This is the outer heredoc
$(cat << INNER
This is the inner heredoc
INNER
)
OUTER
""")
    assert err.strip() == ""
    assert out.strip() == "This is the outer heredoc\nThis is the inner heredoc"
    assert ret == 0

    # Test heredoc with variable substitution
    ret, out, err = shell.run("""
NAME="World"
cat << EOF
Hello, $NAME!
EOF
""")
    assert err.strip() == ""
    assert out.strip() == "Hello, World!"
    assert ret == 0


def test_heredoc_quoted_delimiters(shell):
    # Test heredoc with single-quoted delimiter
    ret, out, err = shell.run("""cat <<'EOF'
some content with single quotes
EOF""")
    assert err.strip() == ""
    assert out.strip() == "some content with single quotes"
    assert ret == 0

    # Test heredoc with double-quoted delimiter
    ret, out, err = shell.run("""cat <<"EOF"
some content with double quotes
EOF""")
    assert err.strip() == ""
    assert out.strip() == "some content with double quotes"
    assert ret == 0

    # Test that quoted delimiters prevent variable expansion
    ret, out, err = shell.run("""
VAR="expanded"
cat <<'EOF'
This $VAR should not be expanded
EOF""")
    assert err.strip() == ""
    assert out.strip() == "This $VAR should not be expanded"
    assert ret == 0


def test_heredoc_quoted_delimiters_with_spaces(shell):
    # Test heredoc with space before single-quoted delimiter
    ret, out, err = shell.run("""cat > /tmp/test_space.sh << 'EOF'
#!/bin/bash
echo "This is a test with space before quoted delimiter"
EOF
cat /tmp/test_space.sh && rm /tmp/test_space.sh""")
    assert ret == 0
    assert "This is a test with space before quoted delimiter" in out

    # Test heredoc with space before double-quoted delimiter
    ret, out, err = shell.run("""cat > /tmp/test_space2.sh << "EOF"
#!/bin/bash
echo "This is a test with space before double-quoted delimiter"
EOF
cat /tmp/test_space2.sh && rm /tmp/test_space2.sh""")
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
    from gptme.tools.shell import is_denylisted

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
        is_denied, reason = is_denylisted(cmd)
        assert is_denied, f"Pattern-matching command should be denied: {cmd}"
        assert reason is not None, f"Should have reason for: {cmd}"


def test_is_denylisted_git_bulk_operations():
    """Test that git bulk operations are properly denied with correct reason."""
    from gptme.tools.shell import is_denylisted

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
        is_denied, reason = is_denylisted(cmd)
        assert is_denied, f"Command should be denied: {cmd}"
        assert reason == expected_reason, f"Wrong reason for: {cmd}"


def test_is_denylisted_destructive_file_operations():
    """Test that destructive file operations are properly denied with correct reason."""
    from gptme.tools.shell import is_denylisted

    dangerous_file_commands = [
        "rm -rf /",
        "sudo rm -rf /",
        "rm -rf *",
        "RM -RF /",  # case insensitive
    ]

    expected_reason = "Destructive file operations are blocked. Specify exact paths and avoid operations that could delete system files or entire directories."

    for cmd in dangerous_file_commands:
        is_denied, reason = is_denylisted(cmd)
        assert is_denied, f"Command should be denied: {cmd}"
        assert reason == expected_reason, f"Wrong reason for: {cmd}"


def test_is_denylisted_dangerous_permissions():
    """Test that dangerous permission operations are properly denied with correct reason."""
    from gptme.tools.shell import is_denylisted

    dangerous_chmod_commands = [
        "chmod 777",
        "chmod -R 777",
        "chmod 777 file.txt",
        "CHMOD 777",  # case insensitive
    ]

    expected_reason = "Overly permissive chmod operations are blocked. Use safer permissions like `chmod 755` or `chmod 644` and be specific about target files."

    for cmd in dangerous_chmod_commands:
        is_denied, reason = is_denylisted(cmd)
        assert is_denied, f"Command should be denied: {cmd}"
        assert reason == expected_reason, f"Wrong reason for: {cmd}"


def test_is_denylisted_safe_commands():
    """Test that safe commands are allowed through."""
    from gptme.tools.shell import is_denylisted

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
    ]

    for cmd in safe_commands:
        is_denied, reason = is_denylisted(cmd)
        assert not is_denied, f"Safe command should be allowed: {cmd}"
        assert reason is None, f"Safe command should have no reason: {cmd}"


def test_is_denylisted_edge_cases():
    """Test edge cases and boundary conditions."""
    from gptme.tools.shell import is_denylisted

    # Test that similar but safe variations are allowed
    safe_variations = [
        "git add file.py",  # specific file, not bulk
        "git add src/",  # specific directory, not all
        "chmod 755",  # safe permissions
        "rm -rf build/target/",  # specific path, not root
        "git commit --amend",  # different flag
    ]

    for cmd in safe_variations:
        is_denied, reason = is_denylisted(cmd)
        assert not is_denied, f"Safe variation should be allowed: {cmd}"
        assert reason is None, f"Safe variation should have no reason: {cmd}"
