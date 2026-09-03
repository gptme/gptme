"""Display-boundary tests for streamed shell and IPython output."""

from collections.abc import Generator
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from gptme.logmanager import LogManager
from gptme.message import Message, set_output_format
from gptme.tools import python as python_tool
from gptme.tools.python import execute_python
from gptme.tools.shell import execute_shell_impl


@pytest.fixture(autouse=True)
def reset_output_format() -> Generator[None, None, None]:
    """Reset terminal format and stop the lazily created IPython history thread."""
    yield
    set_output_format("text")
    if python_tool._ipython is not None:
        history_thread = python_tool._ipython.history_manager.save_thread
        history_thread.stop()
        history_thread.join(timeout=1)
        python_tool._ipython = None


@contextmanager
def _capture_tool_display(
    tmp_path: Path,
) -> Generator[tuple[StringIO, LogManager], None, None]:
    """Capture both live writes and LogManager's final-message rendering."""
    stdout = StringIO()
    stderr = StringIO()
    manager = LogManager(logdir=tmp_path / "conversation", lock=False)
    test_console = Console(file=stdout, force_terminal=False, color_system=None)
    with (
        patch("sys.stdout", stdout),
        patch("sys.stderr", stderr),
        patch("gptme.message.console", test_console),
    ):
        yield stdout, manager


def _append_results(manager: LogManager, messages: list[Message]) -> None:
    for message in messages:
        manager.append(message)


class TestShellOutputDedup:
    def test_streamed_stdout_is_printed_once(self, tmp_path: Path) -> None:
        with _capture_tool_display(tmp_path) as (stdout, manager):
            messages = list(execute_shell_impl("echo shell-dedup-marker", logdir=None))
            _append_results(manager, messages)

        assert stdout.getvalue().count("shell-dedup-marker") == 1
        assert "shell-dedup-marker" in messages[-1].content
        assert messages[-1].quiet is False

    def test_nonstreamed_failure_details_remain_visible(self, tmp_path: Path) -> None:
        with _capture_tool_display(tmp_path) as (stdout, manager):
            messages = list(execute_shell_impl("false", logdir=None))
            _append_results(manager, messages)

        output = stdout.getvalue()
        assert "Ran command: `false`" in output
        assert "Return code: 1" in output

    def test_nonstreamed_no_output_status_remains_visible(self, tmp_path: Path) -> None:
        with _capture_tool_display(tmp_path) as (stdout, manager):
            messages = list(execute_shell_impl("true", logdir=None))
            _append_results(manager, messages)

        assert "No output" in stdout.getvalue()

    def test_json_mode_retains_complete_result(self, tmp_path: Path) -> None:
        set_output_format("json")
        with _capture_tool_display(tmp_path) as (stdout, manager):
            messages = list(execute_shell_impl("echo shell-json-marker", logdir=None))
            _append_results(manager, messages)

        output = stdout.getvalue()
        assert '"type": "message"' in output
        assert "shell-json-marker" in output
        assert messages[-1].metadata == {"terminal_display_content": ""}


class TestIPythonOutputDedup:
    def test_streamed_stdout_is_printed_once(self, tmp_path: Path) -> None:
        with _capture_tool_display(tmp_path) as (stdout, manager):
            messages = list(execute_python("print('ipython-dedup-marker')", [], None))
            _append_results(manager, messages)

        assert stdout.getvalue().count("ipython-dedup-marker") == 1
        assert "ipython-dedup-marker" in messages[-1].content
        assert messages[-1].quiet is False

    def test_expression_result_remains_visible(self, tmp_path: Path) -> None:
        with _capture_tool_display(tmp_path) as (stdout, manager):
            messages = list(execute_python("6 * 7", [], None))
            _append_results(manager, messages)

        assert "Result:" in stdout.getvalue()
        assert "42" in stdout.getvalue()

    def test_synthesized_exception_remains_visible(self, tmp_path: Path) -> None:
        with _capture_tool_display(tmp_path) as (stdout, manager):
            messages = list(
                execute_python("raise RuntimeError('dedup-boom')", [], None)
            )
            _append_results(manager, messages)

        output = stdout.getvalue()
        assert "Exception during execution" in output
        assert "RuntimeError: dedup-boom" in output

    def test_json_mode_retains_complete_result(self, tmp_path: Path) -> None:
        set_output_format("json")
        with _capture_tool_display(tmp_path) as (stdout, manager):
            messages = list(execute_python("6 * 7", [], None))
            _append_results(manager, messages)

        output = stdout.getvalue()
        assert '"type": "message"' in output
        assert "Result:" in output
        assert "42" in output
        assert messages[-1].metadata == {
            "terminal_display_content": "Result:\n````\n42\n````"
        }
