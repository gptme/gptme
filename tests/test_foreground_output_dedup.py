"""Tests for foreground-output deduplication.

When shell/IPython tools execute, they stream output to the terminal in
real time.  manager.append() then calls print_msg() on the tool-result
Message, which would print the same content a second time.

Fix: mark the tool-result Message quiet=True in non-JSON mode when stdout
is a real TTY (indicating live output streaming).  When stdout is not a TTY
(e.g., captured output in tests), quiet=False so the output appears in
result.output. The TUI ignores quiet and renders via its own widget path,
so TUI display is unaffected.

See: gptme/gptme#3707 and ErikBjare/bob#1207.
"""

import sys

import pytest

from gptme.message import set_output_format
from gptme.tools.shell import execute_shell_impl


@pytest.fixture(autouse=True)
def reset_output_format():
    """Ensure output format is text after each test."""
    yield
    set_output_format("text")


def _run_shell(cmd: str) -> list:
    """Collect all messages from execute_shell_impl."""
    return list(execute_shell_impl(cmd, logdir=None))


class TestShellOutputDedup:
    def test_tool_result_is_quiet_in_text_mode(self):
        """Shell tool result is quiet in text mode when stdout is a TTY."""
        msgs = _run_shell("echo hello")
        # In text mode, the message should be quiet=True only if stdout is a real TTY
        # (indicating live streaming). In tests, stdout is not a TTY, so quiet=False.
        # This ensures output appears in test result.output while still suppressing
        # duplicate prints in real terminal contexts.
        if sys.stdout.isatty():
            assert any(m.quiet for m in msgs), (
                "Expected at least one quiet=True message when stdout is a TTY; "
                "got: " + repr([(m.quiet, m.content[:60]) for m in msgs])
            )
        else:
            # In test contexts (non-TTY stdout), output should be printed so it appears in result.output
            assert any(not m.quiet for m in msgs), (
                "Expected at least one quiet=False message when stdout is not a TTY; "
                "got: " + repr([(m.quiet, m.content[:60]) for m in msgs])
            )

    def test_tool_result_quiet_false_in_json_mode(self):
        """Shell tool result must NOT be quiet in JSON mode (consumer needs the event)."""
        set_output_format("json")
        msgs = _run_shell("echo hello")
        # In JSON mode, none of the primary result messages should be quiet
        assert not any(m.quiet for m in msgs), (
            "Expected quiet=False in JSON mode so the structured event is emitted; "
            "got: " + repr([(m.quiet, m.content[:60]) for m in msgs])
        )

    def test_content_preserved_when_quiet(self):
        """quiet=True must NOT affect message content — only suppresses print_msg()."""
        msgs = _run_shell("echo 'dedup-marker-xyz'")
        content = " ".join(m.content for m in msgs)
        assert "dedup-marker-xyz" in content, (
            "Output content must be preserved in the message even when quiet=True"
        )

    def test_multiline_output_preserved_when_quiet(self):
        """Multi-line output must be fully preserved in the quiet message."""
        msgs = _run_shell("printf 'line1\\nline2\\nline3\\n'")
        content = " ".join(m.content for m in msgs)
        assert "line1" in content and "line3" in content, (
            "All output lines must be preserved in the quiet message"
        )


class TestIPythonOutputDedup:
    def test_tool_result_is_quiet_in_text_mode(self):
        """IPython tool result is quiet in text mode when stdout is a TTY."""
        from gptme.tools.python import execute_python

        msgs = list(execute_python("print('ipython-dedup-marker')", [], None))
        # At least the final 'Executed code block' message should be quiet if stdout is a TTY
        executed_msgs = [m for m in msgs if "Executed code block" in m.content]
        assert executed_msgs, "Expected 'Executed code block' message from IPython tool"
        if sys.stdout.isatty():
            assert all(m.quiet for m in executed_msgs), (
                "Expected quiet=True on 'Executed code block' message when stdout is a TTY; "
                "got: " + repr([(m.quiet, m.content[:60]) for m in executed_msgs])
            )
        else:
            # In test contexts (non-TTY stdout), output should be printed
            assert any(not m.quiet for m in executed_msgs), (
                "Expected quiet=False on 'Executed code block' message when stdout is not a TTY; "
                "got: " + repr([(m.quiet, m.content[:60]) for m in executed_msgs])
            )

    def test_tool_result_quiet_false_in_json_mode(self):
        """IPython tool result must NOT be quiet in JSON mode."""
        from gptme.tools.python import execute_python

        set_output_format("json")
        msgs = list(execute_python("print('ipython-json-mode')", [], None))
        executed_msgs = [m for m in msgs if "Executed code block" in m.content]
        assert executed_msgs, "Expected 'Executed code block' message from IPython tool"
        assert not any(m.quiet for m in executed_msgs), (
            "Expected quiet=False in JSON mode; "
            "got: " + repr([(m.quiet, m.content[:60]) for m in executed_msgs])
        )

    def test_content_preserved_when_quiet(self):
        """quiet=True must NOT affect message content for IPython results."""
        from gptme.tools.python import execute_python

        msgs = list(execute_python("x = 42\nprint(f'result={x}')", [], None))
        content = " ".join(m.content for m in msgs)
        assert "result=42" in content, (
            "Output content must be preserved in the quiet IPython message"
        )
