"""Tests for MCP manifest-based tool filtering (Phase 3.3b)"""

import json
import tempfile
from pathlib import Path

from gptme.tools import _filter_mcp_tools_by_manifest, _load_task_manifest
from gptme.tools.base import ToolSpec


def test_load_task_manifest():
    """Test loading manifest for a specific task type"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        # Write test manifest
        manifest = {
            "task_type": "code_review",
            "tools": [{"server_name": "gptme-shell"}, {"server_name": "gptme-browser"}],
        }
        f.write(json.dumps(manifest) + "\n")
        f.write(json.dumps({"task_type": "research", "tools": []}) + "\n")
        f.flush()

        try:
            # Test loading code_review manifest
            result = _load_task_manifest("code_review", f.name)
            assert result is not None
            assert result["task_type"] == "code_review"
            assert len(result["tools"]) == 2

            # Test non-existent task type
            result = _load_task_manifest("nonexistent", f.name)
            assert result is None

            # Test non-existent file
            result = _load_task_manifest("code_review", "nonexistent.jsonl")
            assert result is None
        finally:
            Path(f.name).unlink()


def test_filter_mcp_tools_by_manifest():
    """Test filtering tools based on manifest"""

    # Create mock tools with server attribute
    class MockTool(ToolSpec):
        def __init__(self, name, server):
            self.name = name
            self.server = server

    tools = [
        MockTool("shell-cmd", "gptme-shell"),
        MockTool("browser-open", "gptme-browser"),
        MockTool("read-file", "gptme-shell"),
        MockTool("python-exec", "gptme-python"),
    ]

    manifest = {
        "task_type": "code_review",
        "tools": [{"server_name": "gptme-shell"}, {"server_name": "gptme-browser"}],
    }

    # Filter tools
    filtered = _filter_mcp_tools_by_manifest(tools, manifest)

    # Should only include shell and browser tools
    assert len(filtered) == 3  # shell-cmd, read-file, browser-open
    assert all(t.server in ["gptme-shell", "gptme-browser"] for t in filtered)


def test_filter_mcp_tools_empty_manifest():
    """Test filtering with empty manifest"""

    class MockTool(ToolSpec):
        def __init__(self, name, server):
            self.name = name
            self.server = server

    tools = [MockTool("test", "gptme-test")]

    # No manifest - should return all tools
    result = _filter_mcp_tools_by_manifest(tools, None)
    assert result == tools

    # Empty manifest - should return all tools
    result = _filter_mcp_tools_by_manifest(tools, {})
    assert result == tools
