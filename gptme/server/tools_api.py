"""
Tools registry API — list available tools and their metadata.

Exposes ``GET /api/v2/tools`` so the webui can render a FunctionBrowser panel
that shows the agent's current tool palette, descriptions, block types, and
parameter schemas.
"""

import logging

import flask
from pydantic import BaseModel, Field

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
                required=bool(getattr(p, "required", True)),
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
