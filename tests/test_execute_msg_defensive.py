"""Regression tests for execute_msg defensive behavior (issue #554 / PR #3072).

When a structured (API tool-format) tool_use with a call_id is not runnable,
execute_msg must yield an error tool_result instead of silently dropping it.
Silently dropping a structured tool_use leaves a dangling tool_use in the
conversation — the Anthropic API rejects the next request with HTTP 400.

Markdown-format tool_uses (call_id is None) are NOT API tool_uses; they are
intentionally left unpaired when the tool is unavailable.
"""

from gptme.message import Message
from gptme.tools import execute_msg
from gptme.tools.base import set_tool_format


class TestExecuteMsgDefensive:
    """Regression: defensive error tool_result for non-runnable structured tool_use."""

    def test_nonrunnable_structured_tooluse_yields_error_result(self):
        """Structured tool_use (call_id present) for unknown tool → error tool_result.

        This prevents the dangling tool_use → API 400 crash fixed in PR #3072.
        The fake tool name is intentionally absent from the loaded tool registry.
        """
        set_tool_format("tool")
        call_id = "call-abc123"
        content = f'@nonexistent_tool({call_id}): {{"arg": "value"}}'
        msg = Message("assistant", content)

        results = list(execute_msg(msg))

        assert len(results) == 1, (
            "Expected exactly one error tool_result for the non-runnable structured tool_use"
        )
        result = results[0]
        assert result.call_id == call_id, (
            f"Error result call_id must match the tool_use call_id '{call_id}'"
        )
        assert "nonexistent_tool" in result.content, (
            "Error message should identify the tool that was unavailable"
        )

    def test_nonrunnable_markdown_tooluse_yields_nothing(self):
        """Markdown code block tool_use (no call_id) for unknown tool → no output.

        Markdown blocks are not API tool_uses; they must NOT produce a paired
        tool_result (there is no call_id to pair with).
        """
        set_tool_format("markdown")
        content = "```nonexistent_tool\nsome content\n```"
        msg = Message("assistant", content)

        results = list(execute_msg(msg))

        assert results == [], (
            "Markdown-format tool_use with no call_id must produce no output when tool unavailable"
        )

    def test_error_result_role_is_system(self):
        """Error tool_result must use 'system' role (not 'tool' or 'assistant')."""
        set_tool_format("tool")
        call_id = "call-role-check"
        content = f'@missing_tool({call_id}): {{"x": 1}}'
        msg = Message("assistant", content)

        results = list(execute_msg(msg))

        assert len(results) == 1
        assert results[0].role == "system", (
            f"Expected role='system', got '{results[0].role}'"
        )

    def test_multiple_structured_tooluses_all_get_error_results(self):
        """Multiple non-runnable structured tool_uses each get an error tool_result."""
        set_tool_format("tool")
        content = '@tool_one(call-001): {"a": 1}\n@tool_two(call-002): {"b": 2}'
        msg = Message("assistant", content)

        results = list(execute_msg(msg))

        call_ids = {r.call_id for r in results}
        assert "call-001" in call_ids, "tool_one call_id must appear in results"
        assert "call-002" in call_ids, "tool_two call_id must appear in results"
        assert len(results) == 2, (
            "Each non-runnable structured tool_use needs one error result"
        )
