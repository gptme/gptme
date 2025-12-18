"""ACP types for tool call support.

This module defines types for ACP Phase 2 tool execution support.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import uuid4


class ToolKind(str, Enum):
    """Tool kinds for ACP tool calls."""

    READ = "read"
    EDIT = "edit"
    DELETE = "delete"
    MOVE = "move"
    SEARCH = "search"
    EXECUTE = "execute"
    THINK = "think"
    FETCH = "fetch"
    OTHER = "other"


class ToolCallStatus(str, Enum):
    """Status of a tool call."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class PermissionKind(str, Enum):
    """Permission option kinds."""

    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    REJECT_ONCE = "reject_once"
    REJECT_ALWAYS = "reject_always"


@dataclass
class ToolCall:
    """Represents an ACP tool call."""

    tool_call_id: str
    title: str
    kind: ToolKind
    status: ToolCallStatus = ToolCallStatus.PENDING
    content: list[dict[str, Any]] | None = None
    locations: list[dict[str, Any]] | None = None
    raw_input: dict[str, Any] | None = None
    raw_output: dict[str, Any] | None = None

    @staticmethod
    def generate_id() -> str:
        """Generate a unique tool call ID."""
        return f"call_{uuid4().hex[:12]}"

    def to_dict(self) -> dict[str, Any]:
        """Convert to ACP-compatible dictionary."""
        result: dict[str, Any] = {
            "sessionUpdate": "tool_call",
            "toolCallId": self.tool_call_id,
            "title": self.title,
            "kind": self.kind.value,
            "status": self.status.value,
        }
        if self.content:
            result["content"] = self.content
        if self.locations:
            result["locations"] = self.locations
        if self.raw_input:
            result["rawInput"] = self.raw_input
        if self.raw_output:
            result["rawOutput"] = self.raw_output
        return result

    def to_update_dict(self) -> dict[str, Any]:
        """Convert to ACP tool_call_update dictionary."""
        result: dict[str, Any] = {
            "sessionUpdate": "tool_call_update",
            "toolCallId": self.tool_call_id,
        }
        if self.status:
            result["status"] = self.status.value
        if self.content:
            result["content"] = self.content
        return result


@dataclass
class PermissionOption:
    """A permission option for tool execution."""

    option_id: str
    name: str
    kind: PermissionKind

    def to_dict(self) -> dict[str, Any]:
        """Convert to ACP-compatible dictionary."""
        return {
            "optionId": self.option_id,
            "name": self.name,
            "kind": self.kind.value,
        }


def gptme_tool_to_acp_kind(tool_name: str) -> ToolKind:
    """Map gptme tool names to ACP tool kinds."""
    read_tools = {"read", "cat"}
    edit_tools = {"save", "append", "patch"}
    execute_tools = {"shell", "python", "ipython", "tmux"}
    search_tools = {"browser", "rag"}

    if tool_name in read_tools:
        return ToolKind.READ
    elif tool_name in edit_tools:
        return ToolKind.EDIT
    elif tool_name in execute_tools:
        return ToolKind.EXECUTE
    elif tool_name in search_tools:
        return ToolKind.SEARCH
    else:
        return ToolKind.OTHER
