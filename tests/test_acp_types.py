"""Tests for ACP types module.

Tests the data classes, enums, and utility functions in gptme.acp.types.
"""

import re

from gptme.acp.types import (
    PermissionKind,
    PermissionOption,
    ToolCall,
    ToolCallStatus,
    ToolKind,
    gptme_tool_to_acp_kind,
)


class TestToolKind:
    """Tests for ToolKind enum."""

    def test_all_values(self):
        expected = {
            "read",
            "edit",
            "delete",
            "move",
            "search",
            "execute",
            "think",
            "fetch",
            "other",
        }
        actual = {k.value for k in ToolKind}
        assert actual == expected

    def test_str_enum(self):
        """ToolKind is a str enum so it can be used directly in dicts."""
        assert isinstance(ToolKind.READ, str)
        assert ToolKind.READ == "read"


class TestToolCallStatus:
    """Tests for ToolCallStatus enum."""

    def test_all_values(self):
        expected = {"pending", "in_progress", "completed", "failed"}
        actual = {s.value for s in ToolCallStatus}
        assert actual == expected

    def test_str_enum(self):
        assert isinstance(ToolCallStatus.PENDING, str)
        assert ToolCallStatus.COMPLETED == "completed"


class TestPermissionKind:
    """Tests for PermissionKind enum."""

    def test_all_values(self):
        expected = {"allow_once", "allow_always", "reject_once", "reject_always"}
        actual = {p.value for p in PermissionKind}
        assert actual == expected


class TestToolCall:
    """Tests for ToolCall dataclass."""

    def test_create_with_defaults(self):
        tc = ToolCall(
            tool_call_id="call_abc123",
            title="Execute shell",
            kind=ToolKind.EXECUTE,
        )
        assert tc.tool_call_id == "call_abc123"
        assert tc.title == "Execute shell"
        assert tc.kind == ToolKind.EXECUTE
        assert tc.status == ToolCallStatus.PENDING
        assert tc.content is None
        assert tc.locations is None
        assert tc.raw_input is None
        assert tc.raw_output is None

    def test_create_with_all_fields(self):
        tc = ToolCall(
            tool_call_id="call_xyz",
            title="Save file",
            kind=ToolKind.EDIT,
            status=ToolCallStatus.IN_PROGRESS,
            content=[{"type": "text", "text": "file contents"}],
            locations=[{"path": "/tmp/test.py"}],
            raw_input={"tool": "save", "path": "/tmp/test.py"},
            raw_output={"success": True},
        )
        assert tc.status == ToolCallStatus.IN_PROGRESS
        assert tc.content == [{"type": "text", "text": "file contents"}]
        assert tc.locations == [{"path": "/tmp/test.py"}]

    def test_generate_id_format(self):
        """Generated IDs should match call_XXXXXXXXXXXX format."""
        id1 = ToolCall.generate_id()
        assert re.match(r"^call_[0-9a-f]{12}$", id1)

    def test_generate_id_unique(self):
        """Each generated ID should be unique."""
        ids = {ToolCall.generate_id() for _ in range(100)}
        assert len(ids) == 100

    def test_to_dict_minimal(self):
        tc = ToolCall(
            tool_call_id="call_abc",
            title="Run command",
            kind=ToolKind.EXECUTE,
        )
        d = tc.to_dict()
        assert d == {
            "sessionUpdate": "tool_call",
            "toolCallId": "call_abc",
            "title": "Run command",
            "kind": "execute",
            "status": "pending",
        }

    def test_to_dict_with_optional_fields(self):
        tc = ToolCall(
            tool_call_id="call_xyz",
            title="Read file",
            kind=ToolKind.READ,
            status=ToolCallStatus.COMPLETED,
            content=[{"type": "text", "text": "output"}],
            locations=[{"path": "/tmp/file.txt"}],
            raw_input={"path": "/tmp/file.txt"},
            raw_output={"content": "hello"},
        )
        d = tc.to_dict()
        assert d["content"] == [{"type": "text", "text": "output"}]
        assert d["locations"] == [{"path": "/tmp/file.txt"}]
        assert d["rawInput"] == {"path": "/tmp/file.txt"}
        assert d["rawOutput"] == {"content": "hello"}

    def test_to_update_dict(self):
        tc = ToolCall(
            tool_call_id="call_abc",
            title="Run command",
            kind=ToolKind.EXECUTE,
            status=ToolCallStatus.COMPLETED,
        )
        d = tc.to_update_dict()
        assert d == {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "call_abc",
            "status": "completed",
        }

    def test_to_update_dict_with_content(self):
        tc = ToolCall(
            tool_call_id="call_abc",
            title="Run",
            kind=ToolKind.EXECUTE,
            status=ToolCallStatus.FAILED,
            content=[{"type": "text", "text": "error message"}],
        )
        d = tc.to_update_dict()
        assert d["content"] == [{"type": "text", "text": "error message"}]
        assert d["status"] == "failed"


class TestPermissionOption:
    """Tests for PermissionOption dataclass."""

    def test_create(self):
        opt = PermissionOption(
            option_id="allow-once",
            name="Allow once",
            kind=PermissionKind.ALLOW_ONCE,
        )
        assert opt.option_id == "allow-once"
        assert opt.name == "Allow once"
        assert opt.kind == PermissionKind.ALLOW_ONCE

    def test_to_dict(self):
        opt = PermissionOption(
            option_id="reject-always",
            name="Reject always",
            kind=PermissionKind.REJECT_ALWAYS,
        )
        d = opt.to_dict()
        assert d == {
            "optionId": "reject-always",
            "name": "Reject always",
            "kind": "reject_always",
        }


class TestGptmeToolToAcpKind:
    """Tests for gptme_tool_to_acp_kind mapping function."""

    def test_read_tools(self):
        assert gptme_tool_to_acp_kind("read") == ToolKind.READ
        assert gptme_tool_to_acp_kind("cat") == ToolKind.READ

    def test_edit_tools(self):
        assert gptme_tool_to_acp_kind("save") == ToolKind.EDIT
        assert gptme_tool_to_acp_kind("append") == ToolKind.EDIT
        assert gptme_tool_to_acp_kind("patch") == ToolKind.EDIT

    def test_execute_tools(self):
        assert gptme_tool_to_acp_kind("shell") == ToolKind.EXECUTE
        assert gptme_tool_to_acp_kind("python") == ToolKind.EXECUTE
        assert gptme_tool_to_acp_kind("ipython") == ToolKind.EXECUTE
        assert gptme_tool_to_acp_kind("tmux") == ToolKind.EXECUTE

    def test_search_tools(self):
        assert gptme_tool_to_acp_kind("browser") == ToolKind.SEARCH
        assert gptme_tool_to_acp_kind("rag") == ToolKind.SEARCH

    def test_unknown_tools_return_other(self):
        assert gptme_tool_to_acp_kind("unknown_tool") == ToolKind.OTHER
        assert gptme_tool_to_acp_kind("") == ToolKind.OTHER
        assert gptme_tool_to_acp_kind("custom") == ToolKind.OTHER
