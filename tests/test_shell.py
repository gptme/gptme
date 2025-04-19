import os
import signal
import tempfile
import threading
import time
from collections.abc import Generator

import pytest
from gptme.tools.shell import ShellSession, split_commands


@pytest.fixture
def shell() -> Generator[ShellSession, None, None]:
    orig_cwd = os.getcwd()

    shell = ShellSession()
    yield shell
    shell.close()

    # change back to the original directory
    os.chdir(orig_cwd)


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


def test_function():
    script = """
function hello() {
    echo "Hello, World!"
}
hello
"""
    shell = ShellSession()
    ret, out, err = shell.run(script)
    assert ret == 0
    assert out.strip() == "Hello, World!"


def test_pipeline():
    script = """
echo "Hello, World!" | wc -w
"""
    shell = ShellSession()
    ret, out, err = shell.run(script)
    assert ret == 0
    assert out.strip() == "2"


def test_interrupt():
    """Test that interrupting a command works correctly and captures output."""
    shell = ShellSession()

    # Create a script that:
    # 1. Prints a message
    # 2. Sleeps for 1 second
    # 3. Prints another message
    # 4. Sleeps for 2 seconds (during which we'll interrupt)
    script = """
echo "Starting..."
sleep 2
echo "Should not see this"
"""

    # Run in a separate thread so we can interrupt it
    output: tuple[int | None, str, str] | None = None
    thread_error = None

    def run_with_capture():
        nonlocal output, thread_error
        try:
            ret, out, err = shell.run(script)
            output = (ret, out, err)
        except Exception as e:
            thread_error = e

    thread = threading.Thread(target=run_with_capture)
    thread.start()

    # Wait for first message
    time.sleep(0.5)

    # Send SIGINT
    # TODO: does this adequately simulate what happens when the user presses Ctrl+C during tool execution?
    #       it does not, we can't send a Ctrl+C to the thread, which is what happens when the user presses Ctrl+C
    shell.process.send_signal(signal.SIGINT)

    # Wait for thread to finish with timeout
    # TODO: this timeout should really be shorter than the remaining sleep time (it should have been interrupted and terminated the thread)
    thread.join(timeout=2.0)
    assert not thread.is_alive(), "Thread should have terminated"

    # Check for thread errors
    if thread_error:
        raise thread_error

    # Check output
    assert output
    assert len(output) >= 2, "Should have return code and output"
    assert "Starting..." in output[1], f"Expected 'Starting...' in output: {output[1]}"
    assert (
        "Should not see this" not in output[1]
    ), f"Unexpected output after interrupt: {output[1]}"
    assert output[0] == 130, f"Expected return code 130, got {output[0]}"

    # Verify shell is still alive
    assert shell.process.poll() is None, "Shell should still be alive"

    # Verify we can still run commands
    ret, out, err = shell.run("echo 'Still working'")
    assert ret == 0
    assert out.strip() == "Still working"
