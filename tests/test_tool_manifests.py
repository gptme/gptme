import json
from pathlib import Path

import pytest

from gptme.tool_manifests import load_task_manifest


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


def test_load_task_manifest_rejects_comma_in_server_name(tmp_path: Path):
    """Commas in server_name would corrupt the comma-joined allowlist string."""
    manifest_path = tmp_path / "state" / "mcp-task-manifests.jsonl"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        '{"task_type":"research","tools":[{"server_name":"a,b","tool_name":"tool"}]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must not contain commas"):
        load_task_manifest("research", tmp_path)


def test_load_task_manifest_rejects_comma_in_tool_name(tmp_path: Path):
    """Commas in tool_name would corrupt the comma-joined allowlist string."""
    manifest_path = tmp_path / "state" / "mcp-task-manifests.jsonl"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        '{"task_type":"research","tools":[{"server_name":"server","tool_name":"a,b"}]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must not contain commas"):
        load_task_manifest("research", tmp_path)


@pytest.mark.parametrize(
    ("server_name", "tool_name", "match"),
    [
        # Forward slash in server_name → "github/evil.search_code" has "/" → file path
        ("github/evil", "search_code", "forward slashes"),
        # Forward slash in tool_name → "github.path/to/evil.py" has "/" → file path
        ("github", "path/to/evil", "forward slashes"),
        # Backslash in tool_name → Windows path injection
        ("github", "path\\evil", "backslashes"),
        # Path traversal in server_name
        ("github..", "search_code", "path traversal"),
        # Path traversal in tool_name (pure ".." without slashes — slashes caught first otherwise)
        ("github", "..evil", "path traversal"),
        # .py suffix in tool_name → combined name "server.exploit.py" ends in ".py"
        ("server", "exploit.py", r"must not end with '\.py'"),
    ],
)
def test_load_task_manifest_rejects_path_injection(
    tmp_path: Path, server_name: str, tool_name: str, match: str
):
    """Path-separator characters in tool names allow arbitrary code execution.

    init_tools() treats any allowlist item containing "/" or "\\" or ending in ".py"
    as a file path and calls load_from_file() on it. A manifest with a crafted
    server_name or tool_name can inject a path that loads attacker-controlled Python.
    """
    manifest_path = tmp_path / "state" / "mcp-task-manifests.jsonl"
    manifest_path.parent.mkdir()
    record = {
        "task_type": "research",
        "tools": [{"server_name": server_name, "tool_name": tool_name}],
    }
    manifest_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        load_task_manifest("research", tmp_path)
