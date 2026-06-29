"""
Tools registry API — list available tools and their metadata.

Exposes ``GET /api/v2/tools`` so the webui can render a FunctionBrowser panel
that shows the agent's current tool palette, descriptions, block types, and
parameter schemas.

Also exposes ``QUERY /api/v2/tools`` (HTTP QUERY method per
draft-ietf-httpbis-safe-method-w-body) for filtered introspection — safe,
idempotent, and body-capable, allowing agents to request specific tool subsets
without downloading the full catalog.
"""

import concurrent.futures
import functools
import logging
import re
from typing import Any, Literal

import flask
from pydantic import BaseModel, Field, ValidationError

from ..tools import get_available_tools
from .auth import require_auth
from .openapi_docs import ErrorResponse, api_doc_simple

logger = logging.getLogger(__name__)

tools_api = flask.Blueprint("tools_api", __name__)


class ToolParameterOut(BaseModel):
    name: str = Field(..., description="Parameter name")
    type: str = Field("string", description="Python type annotation as string")
    description: str = Field("", description="Parameter description if available")
    required: bool = Field(True, description="Whether the parameter is required")


class ToolOut(BaseModel):
    name: str = Field(..., description="Tool name (also used as the block type prefix)")
    desc: str = Field("", description="One-line description of what the tool does")
    instructions: str = Field(
        "", description="Full usage instructions shown to the agent"
    )
    block_types: list[str] = Field(
        default_factory=list,
        description="Code-block type tags this tool handles (e.g. ['shell', 'bash'])",
    )
    is_mcp: bool = Field(False, description="Whether this is an MCP-provided tool")
    is_available: bool = Field(True, description="Whether the tool is currently usable")
    disabled_by_default: bool = Field(
        False, description="Whether the tool is excluded from default sessions"
    )
    parameters: list[ToolParameterOut] = Field(
        default_factory=list,
        description="Callable parameters when the tool exposes Python functions",
    )


class ToolListResponse(BaseModel):
    tools: list[ToolOut] = Field(..., description="All available tool descriptors")


# ---------------------------------------------------------------------------
# QUERY method models
# ---------------------------------------------------------------------------

_FILTERABLE_STR_FIELDS = {"name", "desc", "instructions"}
_FILTERABLE_BOOL_FIELDS = {"is_mcp", "is_available", "disabled_by_default"}
_FILTERABLE_LIST_FIELDS = {"block_types"}
_ALL_FILTERABLE = (
    _FILTERABLE_STR_FIELDS | _FILTERABLE_BOOL_FIELDS | _FILTERABLE_LIST_FIELDS
)

# Regex safety limits — prevents ReDoS in filter queries
_MAX_REGEX_LEN = 200
_REGEX_TIMEOUT = 2.0


@functools.lru_cache(maxsize=1)
def _get_regex_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Return a cached thread pool for regex searches with timeout."""
    return concurrent.futures.ThreadPoolExecutor(max_workers=4)


def _safe_regex_search(pattern: str, string: str) -> bool:
    """Run a regex search with length limit and wall-clock timeout.

    Prevents ReDoS attacks through the QUERY filter endpoint.
    """
    if len(pattern) > _MAX_REGEX_LEN:
        return False
    executor = _get_regex_executor()
    future = executor.submit(re.search, pattern, string)
    try:
        return bool(future.result(timeout=_REGEX_TIMEOUT))
    except concurrent.futures.TimeoutError:
        return False
    except re.error:
        return False


class ToolQueryFilter(BaseModel):
    field: str = Field(..., description="Tool field to filter on")
    op: Literal["eq", "neq", "contains", "in", "regex"] = Field(
        ..., description="Filter operation"
    )
    value: str | bool | list[str] = Field(..., description="Value to compare against")


class ToolQueryRequest(BaseModel):
    filters: list[ToolQueryFilter] = Field(
        default_factory=list,
        description="Filters to apply; all filters are ANDed together",
    )
    fields: list[str] | None = Field(
        None, description="Fields to include in each result; None means all fields"
    )


def _match_filter(tool: ToolOut, f: ToolQueryFilter) -> bool:
    """Return True if tool passes the given filter."""
    if f.field not in _ALL_FILTERABLE:
        return False

    raw: Any = getattr(tool, f.field)

    if f.field in _FILTERABLE_BOOL_FIELDS:
        # bool fields: only eq/neq make sense
        if not isinstance(f.value, bool):
            return False
        if f.op == "eq":
            return raw == f.value
        if f.op == "neq":
            return raw != f.value
        return False

    if f.field in _FILTERABLE_LIST_FIELDS:
        # list fields (e.g. block_types): "contains" = value is an element
        lst: list[str] = raw
        if f.op == "contains":
            return str(f.value) in lst
        if f.op == "eq":
            return lst == list(f.value) if isinstance(f.value, list) else False
        if f.op == "in":
            vals = f.value if isinstance(f.value, list) else [str(f.value)]
            return any(v in lst for v in vals)
        return False

    # str fields
    s: str = raw
    sv = str(f.value)
    if f.op == "eq":
        return s == sv
    if f.op == "neq":
        return s != sv
    if f.op == "contains":
        return sv.lower() in s.lower()
    if f.op == "in":
        vals = f.value if isinstance(f.value, list) else [sv]
        return s in vals
    if f.op == "regex":
        return _safe_regex_search(sv, s)
    return False


def _apply_filters(
    tools: list[ToolOut], filters: list[ToolQueryFilter]
) -> list[ToolOut]:
    if not filters:
        return tools
    # Validate all filter field names before applying — unknown fields
    # should return 400, not silently exclude all tools.
    invalid = [f.field for f in filters if f.field not in _ALL_FILTERABLE]
    if invalid:
        raise ValueError(
            f"Unknown filter field(s): {invalid}. "
            f"Valid fields: {sorted(_ALL_FILTERABLE)}"
        )
    # Bool fields must receive actual bool values, not strings like "false"
    for f in filters:
        if f.field in _FILTERABLE_BOOL_FIELDS and not isinstance(f.value, bool):
            raise ValueError(
                f"Field '{f.field}' requires a boolean value, got '{type(f.value).__name__}'"
            )
    return [t for t in tools if all(_match_filter(t, f) for f in filters)]


def _project_fields(tools: list[ToolOut], fields: list[str]) -> list[dict]:
    valid = {f for f in fields if f in ToolOut.model_fields}
    invalid = [f for f in fields if f not in valid]
    if invalid:
        raise ValueError(
            f"Unknown projection field(s): {invalid}. "
            f"Valid fields: {sorted(ToolOut.model_fields.keys())}"
        )
    result = []
    for t in tools:
        d = t.model_dump()
        result.append({k: v for k, v in d.items() if k in valid})
    return result


def _serialize_tool(tool) -> ToolOut:
    from ..tools.base import Parameter

    params: list[ToolParameterOut] = []
    for p in tool.parameters or []:
        if not isinstance(p, Parameter):
            continue
        params.append(
            ToolParameterOut(
                name=p.name,
                type=str(p.type or "string"),
                description=p.description or "",
                required=bool(getattr(p, "required", False)),
            )
        )

    return ToolOut(
        name=tool.name,
        desc=tool.desc or "",
        instructions=tool.instructions or "",
        block_types=list(tool.block_types or []),
        is_mcp=bool(tool.is_mcp),
        is_available=bool(tool.is_available),
        disabled_by_default=bool(tool.disabled_by_default),
        parameters=params,
    )


@tools_api.route("/api/v2/tools")
@require_auth
@api_doc_simple(
    responses={
        200: ToolListResponse,
        500: ErrorResponse,
    },
    tags=["tools"],
)
def list_tools():
    """List all available tools and their metadata.

    Returns every tool that is registered in this gptme instance, including
    MCP tools, with description, block types, availability status, and
    parameter schemas. The webui uses this to render a searchable
    FunctionBrowser panel in the right sidebar.
    """
    try:
        tools = get_available_tools(include_mcp=True)
        return flask.jsonify(
            {"tools": [_serialize_tool(t).model_dump() for t in tools]}
        )
    except Exception as e:
        logger.exception("Error listing tools")
        return flask.jsonify({"error": str(e)}), 500


@tools_api.route("/api/v2/tools", methods=["QUERY"])
@require_auth
def query_tools():
    """Filter tools via the HTTP QUERY method (safe, idempotent, body-capable).

    Accepts a JSON body with optional ``filters`` and ``fields`` keys.
    All filters are ANDed together. ``fields`` projects the response to a
    subset of tool attributes, reducing response size for targeted queries.

    Example — find all MCP tools::

        QUERY /api/v2/tools
        {"filters": [{"field": "is_mcp", "op": "eq", "value": true}]}

    Example — get only name and block_types for shell-related tools::

        QUERY /api/v2/tools
        {
          "filters": [{"field": "block_types", "op": "contains", "value": "shell"}],
          "fields": ["name", "block_types"]
        }
    """
    body = flask.request.get_json(silent=True) or {}
    try:
        query = ToolQueryRequest(**body)
    except ValidationError as e:
        return flask.jsonify({"error": str(e)}), 400

    try:
        raw_tools = get_available_tools(include_mcp=True)
        serialized = [_serialize_tool(t) for t in raw_tools]
        filtered = _apply_filters(serialized, query.filters)

        if query.fields:
            return flask.jsonify({"tools": _project_fields(filtered, query.fields)})

        return flask.jsonify({"tools": [t.model_dump() for t in filtered]})
    except ValueError as e:
        # Validation errors from _apply_filters / _project_fields → 400
        return flask.jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("Error querying tools")
        return flask.jsonify({"error": str(e)}), 500
