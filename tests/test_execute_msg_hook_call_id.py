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
