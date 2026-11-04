"""Complete in-process execution must distinguish user streams from displayhook IO."""

import io
import sys
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace

import pytest

from gptme.hooks import ConfirmAction
from gptme.message import Message
from gptme.tools import python as python_tool


@pytest.fixture(autouse=True)
def inprocess(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GPTME_SANDBOX", "none")
    monkeypatch.setattr(python_tool, "_resolve_kernel_backend", lambda: False)
    monkeypatch.setattr(python_tool, "_ipython", None)
    monkeypatch.setattr(python_tool, "registered_functions", {})
    monkeypatch.setattr(
        python_tool,
        "get_confirmation",
        lambda **kwargs: SimpleNamespace(action=ConfirmAction.CONFIRM),
    )
    try:
        yield
    finally:
        shell = python_tool._ipython
        if shell is not None and (history := shell.history_manager) is not None:
            history.end_session()
            if history.save_thread is not None:
                history.save_thread.stop()
                history.save_thread.join(timeout=2)


def execute(code: str) -> tuple[list[Message], str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        messages = list(python_tool.execute_python(code, [], None))
    return messages, out.getvalue(), err.getvalue()


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize(
    "writes",
    [
        ["Out[legitimate log]", "\n", "NEXT_LINE", "\n"],
        ["Out[1]: ", "user value", "\n", "NEXT_LINE\n"],
        ["Out[2]: user value\n", "NEXT_LINE\n"],
        ["Out[3]: first\nsecond\n", "NEXT_LINE\n"],
        ["Out[", "4]: split", "\nNEXT_LINE\n"],
        ["prefix\nOut[5]: embedded\n", "NEXT_LINE\n"],
        ["Out[6]: 𝄞 café\n", "NEXT_LINE\n"],
        ["Out[7]: no newline", "NEXT_LINE"],
    ],
)
def test_user_writes_preserved(stream: str, writes: list[str]) -> None:
    code = f"import sys\nfor part in {writes!r}:\n    sys.{stream}.write(part)"
    messages, out, err = execute(code)
    expected = "".join(writes)
    assert (out if stream == "stdout" else err) == expected
    assert (err if stream == "stdout" else out) == ""
    assert len(messages) == 1
    assert f"{stream}\n{expected.rstrip()}" in messages[0].content
    assert messages[0].terminal_display_content == "Executed code block."


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_user_print_preserved(stream: str) -> None:
    messages, out, err = execute(
        f'import sys\nprint("Out[1]: user data", file=sys.{stream})\n'
        f'print("NEXT_LINE", file=sys.{stream})'
    )
    expected = "Out[1]: user data\nNEXT_LINE\n"
    assert (out if stream == "stdout" else err) == expected
    assert expected.rstrip() in messages[0].content


@pytest.mark.parametrize("prefix", ["", 'print("Out[1]: USER"); '])
def test_expression_result_rendered_only_by_caller(prefix: str) -> None:
    messages, out, err = execute(prefix + "987654321")
    assert out == ("Out[1]: USER\n" if prefix else "")
    assert err == ""
    message = messages[0]
    assert message.content.count("987654321") == 1
    assert (message.terminal_display_content or "").count("987654321") == 1
    assert "Out[" not in message.content.replace("Out[1]: USER", "")
    assert "Out[" not in (message.terminal_display_content or "")
    # Displayhook must still populate the execution result on a reused shell.
    again, again_out, again_err = execute("123456789")
    assert "123456789" in again[0].content
    assert again_out == again_err == ""


def test_repr_side_effects_are_user_streams() -> None:
    messages, out, err = execute(
        "class Result:\n"
        "    def __repr__(self):\n"
        '        print("Out[1]: REPR_LOG")\n'
        '        return "RESULT_VALUE"\n'
        "    def __str__(self):\n"
        '        return "RESULT_VALUE"\n'
        "Result()"
    )
    assert out == "Out[1]: REPR_LOG\n"
    assert err == ""
    assert messages[0].content.count("REPR_LOG") == 1
    assert messages[0].content.count("RESULT_VALUE") == 1


@pytest.mark.parametrize("as_list", [False, True])
def test_registered_helper_message_delivery(as_list: bool) -> None:
    expected = Message("system", "HELPER_MESSAGE")

    def helper() -> Message | list[Message]:
        print("Out[1]: HELPER_LOG")
        return [expected] if as_list else expected

    python_tool.registered_functions["stream_helper"] = helper
    messages, out, err = execute("stream_helper()")
    assert messages == [expected]
    assert out == "Out[1]: HELPER_LOG\n"
    assert err == ""


def test_error_after_out_prefix_and_stream_restoration() -> None:
    original_stdout, original_stderr = sys.stdout, sys.stderr
    original_displayhook, original_excepthook = sys.displayhook, sys.excepthook
    messages, out, err = execute(
        'import sys\nprint("Out[1]: BEFORE_ERROR")\n'
        'print("Out[2]: ERROR_LOG", file=sys.stderr)\n'
        'raise ValueError("STREAM_FAILURE")'
    )
    assert out.startswith("Out[1]: BEFORE_ERROR\n")
    assert err == "Out[2]: ERROR_LOG\n"
    assert "BEFORE_ERROR" in messages[0].content
    assert "ERROR_LOG" in messages[0].content
    assert "ValueError: STREAM_FAILURE" in messages[0].content
    assert sys.stdout is original_stdout
    assert sys.stderr is original_stderr
    assert sys.displayhook is original_displayhook
    assert sys.excepthook is original_excepthook
    following, out, err = execute('print("AFTER_ERROR")')
    assert out == "AFTER_ERROR\n"
    assert err == ""
    assert "AFTER_ERROR" in following[0].content


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_out_prefix_capture_limit_keeps_live_stream(
    stream: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(python_tool, "_DEFAULT_MAX_OUTPUT_BYTES", 1024)
    payload = "Out[1]: " + "𝄞" * 400 + "\n"
    messages, out, err = execute(f"import sys\nsys.{stream}.write({payload!r}); None")
    assert (out if stream == "stdout" else err) == payload
    content = messages[0].content
    assert "Out[1]: " in content
    assert "bytes omitted" in content
    assert content.count("capture limit reached") == 1
    assert "�" not in content
    assert len(content.encode("utf-8")) < 1200


def test_displayhook_namespace_and_explicit_display() -> None:
    messages, out, err = execute("12345")
    assert out == err == ""
    assert "12345" in messages[0].content
    messages, out, err = execute("67890")
    assert out == err == ""
    messages, out, err = execute("(_, __)")
    assert "(67890, 12345)" in messages[0].content
    assert out == err == ""
    messages, out, err = execute(
        'from IPython.display import display\ndisplay("EXPLICIT_DISPLAY")'
    )
    assert "EXPLICIT_DISPLAY" in out
    assert "EXPLICIT_DISPLAY" in messages[0].content
    assert "Out[" not in out
    assert err == ""


def test_formatter_diagnostics_remain_visible() -> None:
    messages, out, err = execute(
        "class BrokenRepr:\n"
        "    def __repr__(self):\n"
        '        raise ValueError("FORMATTER_FAILURE")\n'
        "    def __str__(self):\n"
        '        return "RESULT_AFTER_FORMATTER_FAILURE"\n'
        "BrokenRepr()"
    )
    assert "FORMATTER_FAILURE" in out + err
    assert "FORMATTER_FAILURE" in messages[0].content
    assert "RESULT_AFTER_FORMATTER_FAILURE" in messages[0].content
