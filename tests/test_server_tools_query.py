"""
Tests for the HTTP QUERY method on /api/v2/tools.

Verifies filtered introspection, field projection, error handling, and that
filtered responses are smaller than the full GET response.
"""

import json
import re

import pytest

flask = pytest.importorskip("flask", reason="flask not installed")

from flask.testing import FlaskClient  # fmt: skip


def _query(client: FlaskClient, body: dict | None = None):
    """Send a QUERY request to /api/v2/tools."""
    return client.open(
        "/api/v2/tools",
        method="QUERY",
        content_type="application/json",
        data=json.dumps(body or {}),
    )


def test_query_no_filters_returns_all_tools(client: FlaskClient):
    """Empty QUERY body returns the same tools as GET."""
    get_resp = client.get("/api/v2/tools")
    query_resp = _query(client, {})

    assert get_resp.status_code == 200
    assert query_resp.status_code == 200

    get_data = get_resp.get_json()
    query_data = query_resp.get_json()

    assert "tools" in query_data
    assert len(query_data["tools"]) == len(get_data["tools"])


def test_query_filter_by_name_eq(client: FlaskClient):
    """Filter by exact name match returns only that tool."""
    # First, find a real tool name via GET
    all_tools = client.get("/api/v2/tools").get_json()["tools"]
    assert all_tools, "No tools registered"
    target_name = all_tools[0]["name"]

    resp = _query(
        client, {"filters": [{"field": "name", "op": "eq", "value": target_name}]}
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "tools" in data
    assert len(data["tools"]) == 1
    assert data["tools"][0]["name"] == target_name


def test_query_filter_by_name_contains(client: FlaskClient):
    """Filter by name contains returns subset."""
    all_tools = client.get("/api/v2/tools").get_json()["tools"]
    assert all_tools, "No tools registered"
    # Use first char of first tool name as a broad filter
    prefix = all_tools[0]["name"][0].lower()

    resp = _query(
        client, {"filters": [{"field": "name", "op": "contains", "value": prefix}]}
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "tools" in data
    # All returned tools should match
    for t in data["tools"]:
        assert prefix in t["name"].lower()


def test_query_filter_by_is_available(client: FlaskClient):
    """Filter is_available=true returns only available tools."""
    resp = _query(
        client, {"filters": [{"field": "is_available", "op": "eq", "value": True}]}
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "tools" in data
    for t in data["tools"]:
        assert t["is_available"] is True


def test_query_filter_by_is_mcp(client: FlaskClient):
    """Filter is_mcp=false returns only non-MCP tools."""
    resp = _query(
        client, {"filters": [{"field": "is_mcp", "op": "eq", "value": False}]}
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "tools" in data
    for t in data["tools"]:
        assert t["is_mcp"] is False


def test_query_field_projection(client: FlaskClient):
    """Field projection returns only requested fields."""
    resp = _query(client, {"fields": ["name", "desc"]})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "tools" in data
    assert data["tools"], "Expected at least one tool"
    for t in data["tools"]:
        assert set(t.keys()) == {"name", "desc"}


def test_query_projection_reduces_payload_size(client: FlaskClient):
    """Projected QUERY response is smaller than full GET response."""
    get_resp = client.get("/api/v2/tools")
    query_resp = _query(client, {"fields": ["name"]})

    assert get_resp.status_code == 200
    assert query_resp.status_code == 200

    get_size = len(get_resp.get_data())
    query_size = len(query_resp.get_data())

    # name-only projection must be meaningfully smaller
    assert query_size < get_size, (
        f"Projected response ({query_size}B) should be smaller than full GET ({get_size}B)"
    )


def test_query_filter_and_projection_combined(client: FlaskClient):
    """Filter + projection together: correct tools, correct fields."""
    all_tools = client.get("/api/v2/tools").get_json()["tools"]
    assert all_tools, "No tools registered"
    target_name = all_tools[0]["name"]

    resp = _query(
        client,
        {
            "filters": [{"field": "name", "op": "eq", "value": target_name}],
            "fields": ["name", "block_types"],
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["tools"]) == 1
    assert set(data["tools"][0].keys()) == {"name", "block_types"}
    assert data["tools"][0]["name"] == target_name


def test_query_no_match_returns_empty_list(client: FlaskClient):
    """Filter with no matches returns empty tools list, not an error."""
    resp = _query(
        client,
        {"filters": [{"field": "name", "op": "eq", "value": "__no_such_tool_xyz__"}]},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["tools"] == []


def test_query_invalid_filter_body_returns_400(client: FlaskClient):
    """Malformed filter body returns 400."""
    resp = _query(
        client, {"filters": [{"field": "name", "op": "UNKNOWN_OP", "value": "x"}]}
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "error" in data


def test_query_name_regex_filter(client: FlaskClient):
    """Regex filter on name field works."""
    all_tools = client.get("/api/v2/tools").get_json()["tools"]
    assert all_tools, "No tools registered"
    first_name = all_tools[0]["name"]
    # Build a regex that matches the first tool's name
    pattern = f"^{re.escape(first_name)}$"

    resp = _query(
        client, {"filters": [{"field": "name", "op": "regex", "value": pattern}]}
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["tools"]) == 1
    assert data["tools"][0]["name"] == first_name


def test_query_unknown_filter_field_returns_400(client: FlaskClient):
    """Unknown filter field name returns 400, not silent empty list."""
    resp = _query(
        client,
        {"filters": [{"field": "description", "op": "eq", "value": "x"}]},
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "error" in data
    assert "Unknown filter field" in data["error"]


def test_query_bool_field_with_string_value_returns_400(client: FlaskClient):
    """String value for bool field returns 400, not inverted results."""
    resp = _query(
        client,
        {"filters": [{"field": "is_available", "op": "eq", "value": "false"}]},
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "error" in data
    assert "boolean" in data["error"].lower()


def test_query_regex_pattern_length_limit(client: FlaskClient):
    """Overly long regex pattern is rejected safely (no hang)."""
    resp = _query(
        client,
        {"filters": [{"field": "name", "op": "regex", "value": "a" * 300}]},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    # Pattern > 200 chars should match nothing (safely rejected)
    assert data["tools"] == []


def test_query_unknown_projection_field_returns_400(client: FlaskClient):
    """Unknown projection field name returns 400, not empty dicts."""
    resp = _query(
        client,
        {"fields": ["schema", "tags"]},
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "error" in data
    assert "Unknown projection field" in data["error"]
