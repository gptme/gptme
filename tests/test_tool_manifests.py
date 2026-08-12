from pathlib import Path

import pytest

from gptme.tool_manifests import load_task_manifest
from gptme.tools import _filter_mcp_tools_by_manifest
from gptme.tools.base import ToolSpec


def test_load_task_manifest_returns_dotted_tool_names(tmp_path: Path):
    manifest_path = tmp_path / "state" / "mcp-task-manifests.jsonl"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        '{"task_type":"research","tools":['
        '{"server_name":"github","tool_name":"search_code"},'
        '{"server_name":"time","tool_name":"get_current_time"},'
        '{"server_name":"github","tool_name":"search_code"}]}\n',
        encoding="utf-8",
    )

    manifest = load_task_manifest("research", tmp_path)

    assert manifest.task_type == "research"
    assert manifest.path == manifest_path
    assert manifest.tool_names == ("github.search_code", "time.get_current_time")


def test_load_task_manifest_unknown_type_lists_available(tmp_path: Path):
    manifest_path = tmp_path / "state" / "mcp-task-manifests.jsonl"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        '{"task_type":"research","tools":[{"server_name":"github","tool_name":"search_code"}]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Available task types: research"):
        load_task_manifest("debugging", tmp_path)


def test_load_task_manifest_invalid_tool_entry_is_usage_error(tmp_path: Path):
    manifest_path = tmp_path / "state" / "mcp-task-manifests.jsonl"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        '{"task_type":"research","tools":[{"server_name":"github"}]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="tool_name must be a non-empty string"):
        load_task_manifest("research", tmp_path)


def test_filter_mcp_tools_by_manifest_matches_dotted_tool_names():
    tools = [
        ToolSpec("github.search_code", "Search GitHub code", is_mcp=True),
        ToolSpec("github.issue_read", "Read GitHub issue", is_mcp=True),
        ToolSpec("time.get_current_time", "Get current time", is_mcp=True),
    ]
    manifest = {
        "task_type": "research",
        "tools": [
            {"server_name": "github", "tool_name": "search_code"},
            {"server_name": "time", "tool_name": "get_current_time"},
        ],
    }

    filtered = _filter_mcp_tools_by_manifest(tools, manifest)

    assert [tool.name for tool in filtered] == [
        "github.search_code",
        "time.get_current_time",
    ]
