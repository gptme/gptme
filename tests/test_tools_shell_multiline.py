from gptme.tools.shell import split_commands


def test_split_commands_multiline_for_loop():
    """Test that multiline for loops are kept as a single command.

    This reproduces issue #724 where multiline for loops were being
    incorrectly split into separate commands.
    """
    # This is the example from issue #724
    script_multiline_for = """for pr in 723 722 721 720 719; do
  echo "=== PR #$pr ==="
  gh pr checks "$pr" --json name,conclusion,workflowName --jq '.[] | select(.conclusion != null) | "\\(.workflowName): \\(.conclusion)"' | head -5
done"""
    commands = split_commands(script_multiline_for)
    # Should be kept as a single command, not split apart
    assert len(commands) == 1
    assert "for pr in" in commands[0]
    assert "done" in commands[0]


def test_split_commands_multiline_while_loop():
    """Test that multiline while loops are kept as a single command."""
    script_while = """while read line; do
  echo "Processing: $line"
  process_line "$line"
done < input.txt"""
    commands = split_commands(script_while)
    assert len(commands) == 1
    assert "while read" in commands[0]
    assert "done" in commands[0]


def test_split_commands_multiline_if_statement():
    """Test that multiline if statements are kept as a single command."""
    script_if = """if [ -f myfile.txt ]; then
  echo "File exists"
  cat myfile.txt
else
  echo "File does not exist"
fi"""
    commands = split_commands(script_if)
    assert len(commands) == 1
    assert "if [" in commands[0]
    assert "fi" in commands[0]
