"""Regression test for TOOL_EXECUTE_POST hook messages incorrectly inheriting call_id.

Root cause: execute_msg used to call tool_response.replace(call_id=tooluse.call_id)
for every message yielded by tooluse.execute(), including post-execution hook
messages (e.g. the token-awareness warning).

A hook message with the tool's call_id is then converted by
_messages_to_responses_input() into a duplicate function_call_output item in the
Responses API input.  The duplicate confuses the API, producing a 400 error:
  "No tool output found for function call <call_id>"

Fix: call_id is now assigned in ToolUse.execute() when real tool results are
yielded.  Hook messages are emitted without a call_id so execute_msg passes them
through untouched.
"""

from __future__ import annotations

import pytest

from gptme.hooks import HookType, clear_hooks
from gptme.message import Message
from gptme.tools import execute_msg
from gptme.tools.base import ToolSpec, set_tool_format


@pytest.fixture(autouse=True)
def _reset_hooks():
    clear_hooks()
    yield
    clear_hooks()


@pytest.fixture(autouse=True)
def _set_tool_format():
    set_tool_format("tool")
    yield
    set_tool_format("markdown")


@pytest.fixture()
def fake_echo_tool(monkeypatch):
    """Register a fake 'echo' tool that yields one result message, then reload."""
    actual_output = Message("system", "echo: hello")

    def execute(code, args, kwargs):
        yield actual_output

    spec = ToolSpec(name="echo", desc="echo tool for tests", execute=execute)

    # get_tool is imported inside _execute_tool(); patch the canonical location.
    monkeypatch.setattr(
        "gptme.tools.get_tool", lambda name: spec if name == "echo" else None
    )
    return actual_output


def test_real_tool_result_gets_call_id(fake_echo_tool, monkeypatch):
    """The real tool result message must carry the tool's call_id."""
    monkeypatch.setattr("gptme.hooks.trigger_hook", lambda *a, **kw: [])

    call_id = "call-test-abc"
    msg = Message("assistant", f'@echo({call_id}): {{"text": "hello"}}')
    results = list(execute_msg(msg))

    real_results = [r for r in results if r.content == "echo: hello"]
    assert real_results, "Expected at least one real tool result"
    assert real_results[0].call_id == call_id, (
        f"Real tool result must carry call_id='{call_id}', got {real_results[0].call_id!r}"
    )


def test_post_hook_message_does_not_get_call_id(fake_echo_tool, monkeypatch):
    """TOOL_EXECUTE_POST hook messages must NOT inherit the tool's call_id.

    Before the fix, execute_msg applied .replace(call_id=tooluse.call_id) to
    every yielded message, causing hook side-effects to become duplicate
    function_call_output items in the Responses API input → 400 error.
    """
    hook_msg = Message(
        "system",
        "<system_warning>Token usage: 100/1000000; 999900 remaining</system_warning>",
        hide=True,
    )

    def fake_trigger(hook_type, data, **kwargs):
        if hook_type == HookType.TOOL_EXECUTE_POST:
            return [hook_msg]
        return []

    monkeypatch.setattr("gptme.hooks.trigger_hook", fake_trigger)

    call_id = "call-hook-test"
    msg = Message("assistant", f'@echo({call_id}): {{"text": "hello"}}')
    results = list(execute_msg(msg))

    hook_results = [r for r in results if "Token usage" in r.content]
    assert hook_results, "Expected the hook message to be yielded"
    assert hook_results[0].call_id is None, (
        f"Hook message must NOT carry call_id; got {hook_results[0].call_id!r}. "
        "A call_id on a hook message creates a duplicate function_call_output in "
        "the Responses API input, causing 400 errors."
    )


def test_pre_hook_message_does_not_get_call_id(fake_echo_tool, monkeypatch):
    """TOOL_EXECUTE_PRE hook messages must NOT inherit the tool's call_id either."""
    hook_msg = Message("system", "pre-hook notification")

    def fake_trigger(hook_type, data, **kwargs):
        if hook_type == HookType.TOOL_EXECUTE_PRE:
            return [hook_msg]
        return []

    monkeypatch.setattr("gptme.hooks.trigger_hook", fake_trigger)

    call_id = "call-pre-hook-test"
    msg = Message("assistant", f'@echo({call_id}): {{"text": "hello"}}')
    results = list(execute_msg(msg))

    pre_hook_results = [r for r in results if r.content == "pre-hook notification"]
    assert pre_hook_results, "Expected the pre-hook message to be yielded"
    assert pre_hook_results[0].call_id is None, (
        f"Pre-hook message must NOT carry call_id; got {pre_hook_results[0].call_id!r}"
    )


def test_post_hook_exception_error_has_no_call_id(fake_echo_tool, monkeypatch):
    """When a post-hook raises after the real result was yielded, the error must NOT get call_id.

    A real result with call_id is already emitted. If the catch-all exception handler
    also stamps call_id on the error message, both become function_call_output entries
    for one tool call — causing a Responses API 400.
    """

    def fake_trigger(hook_type, data, **kwargs):
        if hook_type == HookType.TOOL_EXECUTE_POST:
            raise RuntimeError("post-hook failed")
        return []

    monkeypatch.setattr("gptme.hooks.trigger_hook", fake_trigger)

    call_id = "call-post-hook-error"
    msg = Message("assistant", f'@echo({call_id}): {{"text": "hello"}}')
    results = list(execute_msg(msg))

    # The real result must still carry call_id.
    real_results = [r for r in results if r.content == "echo: hello"]
    assert real_results, "Expected the real tool result to be yielded"
    assert real_results[0].call_id == call_id, (
        f"Real result must keep call_id='{call_id}', got {real_results[0].call_id!r}"
    )

    # The error message must NOT carry call_id — the real result already claimed it.
    error_results = [r for r in results if "Error executing tool" in r.content]
    assert error_results, "Expected an error message from the failed post-hook"
    assert error_results[0].call_id is None, (
        f"Error after real result must NOT carry call_id; "
        f"got {error_results[0].call_id!r}. "
        "Two call_id-stamped messages for one tool call causes a Responses API 400."
    )


def test_multi_message_tool_only_last_gets_call_id(monkeypatch):
    """Only the last message from a multi-message tool execution must carry call_id.

    ToolUse.execute can yield multiple messages (e.g. a shellcheck warning
    followed by actual command output). Stamping call_id only on the last message
    ensures the actual tool output — not an earlier notice — becomes the
    function_call_output in the Responses API. Stamping every message creates
    one function_call_output per message → duplicate call_ids → 400 error.
    """
    msg1 = Message("system", "output chunk 1")
    msg2 = Message("system", "output chunk 2")
    msg3 = Message("system", "output chunk 3")

    def execute(code, args, kwargs):
        yield msg1
        yield msg2
        yield msg3

    spec = ToolSpec(name="multi", desc="multi-output tool for tests", execute=execute)
    monkeypatch.setattr(
        "gptme.tools.get_tool", lambda name: spec if name == "multi" else None
    )
    monkeypatch.setattr("gptme.hooks.trigger_hook", lambda *a, **kw: [])

    call_id = "call-multi-test"
    invoke_msg = Message("assistant", f'@multi({call_id}): {{"text": "test"}}')
    results = list(execute_msg(invoke_msg))

    output_results = [r for r in results if r.content.startswith("output chunk")]
    assert len(output_results) == 3, (
        f"Expected 3 output messages, got {len(output_results)}"
    )
    # Only the last message must carry call_id — it is the actual tool result.
    assert output_results[-1].call_id == call_id, (
        f"Last message must carry call_id='{call_id}', got {output_results[-1].call_id!r}"
    )
    assert output_results[0].call_id is None, (
        f"Earlier messages must NOT carry call_id; got {output_results[0].call_id!r} — "
        "an earlier warning/notice becoming function_call_output pushes the actual "
        "tool output into system instructions, causing wrong role and precedence."
    )
    stamped = [r for r in output_results if r.call_id is not None]
    assert len(stamped) == 1, (
        f"Exactly one output message must carry call_id; got {len(stamped)} — "
        "each stamped message becomes a duplicate function_call_output in the "
        "Responses API, causing a 400 error."
    )


def test_warning_before_output_last_gets_call_id(monkeypatch):
    """When a tool yields a warning then actual output, the LAST (actual output) gets call_id.

    Mirrors the shell tool pattern: shellcheck warning first, then command output.
    The warning must not become function_call_output; the actual result must.
    """
    warning = Message("system", "ShellCheck: SC2086 double-quote to prevent globbing")
    output = Message("system", "stdout: file1.txt file2.txt\nreturncode: 0")

    def execute(code, args, kwargs):
        yield warning
        yield output

    spec = ToolSpec(name="shell2", desc="shell-like tool", execute=execute)
    monkeypatch.setattr(
        "gptme.tools.get_tool", lambda name: spec if name == "shell2" else None
    )
    monkeypatch.setattr("gptme.hooks.trigger_hook", lambda *a, **kw: [])

    call_id = "call-shellcheck-test"
    invoke_msg = Message("assistant", f'@shell2({call_id}): {{"command": "ls /tmp"}}')
    results = list(execute_msg(invoke_msg))

    system_results = [r for r in results if r.role == "system"]
    assert len(system_results) == 2

    warn_r = next(r for r in system_results if "ShellCheck" in r.content)
    out_r = next(r for r in system_results if "returncode" in r.content)

    assert warn_r.call_id is None, (
        "Warning must not carry call_id — it must not become function_call_output"
    )
    assert out_r.call_id == call_id, (
        f"Actual output must carry call_id='{call_id}', got {out_r.call_id!r}"
    )


def test_keyboard_interrupt_forwards_partial_output(monkeypatch):
    """KeyboardInterrupt during generator buffering must still forward partial output.

    list(generator_result) aborts on KeyboardInterrupt before any messages are
    forwarded. This test verifies that partial output is still yielded and
    on_result_message is called even when the generator is interrupted mid-stream.
    """
    partial = Message("system", "partial output before interrupt")

    def execute(code, args, kwargs):
        yield partial
        raise KeyboardInterrupt

    spec = ToolSpec(
        name="interrupted", desc="tool that gets interrupted", execute=execute
    )
    monkeypatch.setattr(
        "gptme.tools.get_tool", lambda name: spec if name == "interrupted" else None
    )
    monkeypatch.setattr("gptme.hooks.trigger_hook", lambda *a, **kw: [])

    call_id = "call-interrupt-test"
    invoke_msg = Message("assistant", f'@interrupted({call_id}): {{"text": "test"}}')

    # execute_msg catches KeyboardInterrupt and converts it to INTERRUPT_CONTENT.
    # With our buffering fix, partial output is forwarded BEFORE the interrupt
    # message, so both should appear.
    received = list(execute_msg(invoke_msg))

    output = [r for r in received if r.content == "partial output before interrupt"]
    assert output, "Partial output must be forwarded even when generator is interrupted"
    assert output[0].call_id is None, (
        "Partial output must NOT carry call_id — execute_msg's INTERRUPT_CONTENT "
        "message is the canonical function_call_output; stamping call_id on the "
        "partial too creates a duplicate function_call_output → Responses API 400."
    )

    interrupt_msgs = [r for r in received if "Interrupted" in r.content]
    assert interrupt_msgs, "INTERRUPT_CONTENT message must follow the partial output"
    # The interrupt message is the canonical function_call_output for the interrupted call.
    assert interrupt_msgs[0].call_id == call_id
