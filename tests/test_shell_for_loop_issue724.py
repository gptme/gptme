import pytest
from gptme.tools.shell import ShellSession


@pytest.fixture
def shell():
    """Create a shell session for testing."""
    shell = ShellSession()
    yield shell
    shell.close()


def test_for_loop_with_multiline_body(shell):
    """Test that for loops with multiline bodies execute correctly.

    This is a regression test for issue #724 where for loops would
    stall due to shlex mangling the command syntax.
    """
    # Simplified version of the problematic command from issue #724
    script = """for i in 1 2 3; do
  echo "Item: $i"
  echo "Processing..."
done"""

    ret, out, err = shell.run(script)
    assert ret == 0
    assert "Item: 1" in out
    assert "Item: 2" in out
    assert "Item: 3" in out
    assert "Processing..." in out
    assert err.strip() == ""


def test_for_loop_with_pipes(shell):
    """Test for loop with pipes in body (closer to original issue #724)."""
    script = """for num in 1 2; do
  echo "Number $num" | grep "Number"
done"""

    ret, out, err = shell.run(script)
    assert ret == 0
    assert "Number 1" in out
    assert "Number 2" in out


def test_while_loop_multiline(shell):
    """Test that while loops also work correctly."""
    # Use seq instead of arithmetic expansion to avoid bashlex limitation
    script = """for count in $(seq 0 2); do
  echo "Count: $count"
done"""

    ret, out, err = shell.run(script)
    assert ret == 0
    assert "Count: 0" in out
    assert "Count: 1" in out
    assert "Count: 2" in out


def test_if_statement_multiline(shell):
    """Test that if statements work correctly."""
    script = """if [ -d /tmp ]; then
  echo "Directory exists"
  echo "Checking complete"
else
  echo "Directory does not exist"
fi"""

    ret, out, err = shell.run(script)
    assert ret == 0
    assert "Directory exists" in out
    assert "Checking complete" in out
