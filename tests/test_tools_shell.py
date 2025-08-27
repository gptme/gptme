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
