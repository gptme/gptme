"""
OpenAPI documentation using pydantic dataclasses.

Simple decorator-based approach for existing function routes with automatic inference.
"""

import inspect
import re
from collections.abc import Callable
from typing import (
    Any,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from flask import Blueprint, current_app, jsonify
from pydantic import BaseModel, Field

from gptme.__version__ import __version__

# Pydantic Models (auto-generate OpenAPI schemas)
# -----------------------------------------------


class ConversationListItem(BaseModel):
    """A conversation list item."""

    name: str = Field(..., description="Conversation name")
    path: str = Field(..., description="Conversation path")
    created: str = Field(..., description="Creation timestamp")
    modified: str = Field(..., description="Last modified timestamp")


class Message(BaseModel):
    """A conversation message."""

    role: str = Field(..., description="Message role (user, assistant, system)")
    content: str = Field(..., description="Message content")
    timestamp: str = Field(..., description="Message timestamp")
    files: list[str] | None = Field(None, description="Associated files")


class Conversation(BaseModel):
    """A complete conversation."""

    name: str = Field(..., description="Conversation name")
    log: list[Message] = Field(..., description="Message history")
    workspace: str = Field(..., description="Workspace path")


class MessageCreateRequest(BaseModel):
    """Request to add a message."""

    role: str = Field(..., description="Message role")
    content: str = Field(..., description="Message content")
    files: list[str] | None = Field(None, description="Associated files")
    branch: str = Field("main", description="Conversation branch")


class GenerateRequest(BaseModel):
    """Request to generate a response."""

    model: str | None = Field(None, description="Model to use")
    stream: bool = Field(False, description="Enable streaming")
    branch: str = Field("main", description="Conversation branch")


class FileMetadata(BaseModel):
    """File metadata."""

    name: str = Field(..., description="File name")
    path: str = Field(..., description="File path")
    type: str = Field(..., description="File type: file or directory")
    size: int = Field(..., description="File size in bytes")
    modified: str = Field(..., description="Last modified timestamp")
    mime_type: str | None = Field(None, description="MIME type")


class ErrorResponse(BaseModel):
    """Error response."""

    error: str = Field(..., description="Error message")


class StatusResponse(BaseModel):
    """Generic status response."""

    status: str = Field(..., description="Operation status")


class ConversationResponse(BaseModel):
    """Response containing conversation data."""

    name: str = Field(..., description="Conversation name")
    log: list[dict] = Field(..., description="Message history as raw objects")
    workspace: str = Field(..., description="Workspace path")


class ConversationListResponse(BaseModel):
    """Response containing a list of conversations."""

    conversations: list[ConversationListItem] = Field(
        ..., description="List of conversations"
    )


class ConversationCreateRequest(BaseModel):
    """Request to create a new conversation."""

    config: dict = Field(default_factory=dict, description="Chat configuration")
    prompt: str = Field("full", description="System prompt type")
    messages: list[dict] = Field(default_factory=list, description="Initial messages")


class GenerateResponse(BaseModel):
    """Response from generation endpoint."""

    role: str = Field(..., description="Message role")
    content: str = Field(..., description="Generated content")
    stored: bool = Field(..., description="Whether message was stored")


# V2 API Models
# -------------


class SessionResponse(BaseModel):
    """Session information response."""

    session_id: str = Field(..., description="Session ID")
    conversation_id: str = Field(..., description="Conversation ID")


class StepRequest(BaseModel):
    """Request to take a step in conversation."""

    session_id: str = Field(..., description="Session ID")
    model: str | None = Field(None, description="Model to use")
    stream: bool = Field(True, description="Enable streaming")
    branch: str = Field("main", description="Conversation branch")
    auto_confirm: bool | int = Field(False, description="Auto-confirm tools")


class ToolConfirmRequest(BaseModel):
    """Request to confirm or modify tool execution."""

    session_id: str = Field(..., description="Session ID")
    tool_id: str = Field(..., description="Tool ID")
    action: str = Field(..., description="Action: confirm, edit, skip, auto")
    content: str | None = Field(None, description="Modified content (for edit action)")
    count: int | None = Field(None, description="Auto-confirm count (for auto action)")


class InterruptRequest(BaseModel):
    """Request to interrupt generation."""

    session_id: str = Field(..., description="Session ID")


class AgentCreateRequest(BaseModel):
    """Request to create a new agent."""

    name: str = Field(..., description="Agent name")
    template_repo: str = Field(..., description="Template repository URL")
    template_branch: str = Field(..., description="Template repository branch")
    fork_command: str = Field(..., description="Fork command to execute")
    path: str = Field(..., description="Path where the agent will be created")
    project_config: dict | None = Field(
        None, description="Optional project configuration"
    )


class AgentCreateResponse(BaseModel):
    """Response from agent creation."""

    status: str = Field(..., description="Operation status")
    message: str = Field(..., description="Success message")
    initial_conversation_id: str = Field(..., description="Initial conversation ID")


class ChatConfig(BaseModel):
    """Chat configuration."""

    name: str | None = Field(None, description="Conversation name")
    model: str | None = Field(None, description="Default model")
    tools: list[str] | None = Field(None, description="Enabled tools")
    workspace: str | None = Field(None, description="Workspace path")


# Helper functions for automatic inference
# ----------------------------------------


def _parse_docstring(docstring: str | None) -> tuple[str, str]:
    """Parse docstring into summary and description."""
    if not docstring:
        return "", ""

    lines = [line.strip() for line in docstring.strip().split("\n") if line.strip()]
    if not lines:
        return "", ""

    summary = lines[0]
    description = "\n".join(lines[1:]) if len(lines) > 1 else ""

    return summary, description


def _infer_response_type(func: Callable) -> dict[int, type | None]:
    """Infer response types from function type annotations."""
    try:
        type_hints = get_type_hints(func)
        return_type = type_hints.get("return")

        if return_type is None:
            return {200: None}

        # Handle Union types (e.g., Union[SuccessResponse, ErrorResponse])
        if get_origin(return_type) is Union:
            # For now, just use the first non-None type as 200 response
            args = get_args(return_type)
            for arg in args:
                if (
                    arg is not type(None)
                    and isinstance(arg, type)
                    and issubclass(arg, BaseModel)
                ):
                    return {200: arg, 500: ErrorResponse}
            return {200: None}

        # Handle direct BaseModel subclasses
        if isinstance(return_type, type) and issubclass(return_type, BaseModel):
            return {200: return_type, 500: ErrorResponse}

        return {200: None}
    except Exception:
        return {200: None}


def _infer_request_body(func: Callable) -> type[BaseModel] | None:
    """Infer request body type from function parameters or annotations."""
    try:
        # First try to get from function annotations (for new style)
        type_hints = get_type_hints(func)

        # Look for parameters that are BaseModel subclasses (excluding path params)
        sig = inspect.signature(func)
        for param_name, _param in sig.parameters.items():
            if param_name in ("logfile", "filename"):  # Skip path parameters
                continue

            param_type = type_hints.get(param_name)
            if (
                param_type
                and isinstance(param_type, type)
                and issubclass(param_type, BaseModel)
            ):
                return param_type

        return None
    except Exception:
        return None


def api_doc_simple(
    responses: dict[int, type | None] | None = None,
    request_body: type[BaseModel] | None = None,
    parameters: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
):
    """
    Simplified decorator that infers everything possible automatically.

    Use this for maximum automation - only specify what can't be inferred.
    """
    return api_doc(
        summary=None,  # Will be inferred from docstring
        description=None,  # Will be inferred from docstring
        responses=responses,
        request_body=request_body,
        parameters=parameters,  # Manual override for special cases
        tags=tags,
    )


def _convert_flask_path_to_openapi(flask_path: str) -> str:
    """Convert Flask route pattern to OpenAPI path pattern."""
    # Convert <type:param> or <param> to {param}
    # Handles string:, path:, int:, float:, etc.
    return re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", flask_path)


def _infer_parameters(func: Callable, rule_string: str) -> list[dict[str, Any]]:
    """Infer parameters from Flask route and function signature."""
    parameters = []

    # Extract path parameters from route - handle all Flask parameter types
    path_params = re.findall(r"<(?:\w+:)?(\w+)>", rule_string)
    for param in path_params:
        parameters.append(
            {
                "name": param,
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
                "description": f"{param.replace('_', ' ').title()} identifier",
            }
        )

    # Try to infer query parameters from function signature
    try:
        sig = inspect.signature(func)
        for param_name, param in sig.parameters.items():
            if param_name in path_params or param_name in ("logfile", "filename"):
                continue

            # If parameter has a default value, it might be a query parameter
            if param.default is not inspect.Parameter.empty:
                param_type = "integer" if isinstance(param.default, int) else "string"
                parameters.append(
                    {
                        "name": param_name,
                        "in": "query",
                        "required": False,
                        "schema": {"type": param_type, "default": param.default},
                        "description": f"{param_name.replace('_', ' ').title()} parameter",
                    }
                )
    except Exception:
        pass

    return parameters


def _infer_tags(func: Callable) -> list[str]:
    """Infer tags from function module and name."""
    module_parts = func.__module__.split(".")
    if "api" in module_parts:
        # Extract meaningful parts after 'api'
        api_index = module_parts.index("api")
        if api_index + 1 < len(module_parts):
            return [module_parts[api_index + 1]]

    # Fallback to extracting from function name
    func_name = func.__name__
    if func_name.startswith("api_"):
        return [func_name[4:].split("_")[0]]

    return ["general"]


# Enhanced decorator for OpenAPI documentation
# --------------------------------------------

_endpoint_docs: dict[str, dict[str, Any]] = {}

# Common parameter objects for reuse across endpoints
CONVERSATION_ID_PARAM = {
    "name": "conversation_id",
    "in": "path",
    "required": True,
    "schema": {"type": "string"},
    "description": "Conversation ID",
}


def api_doc(
    summary: str | None = None,
    description: str | None = None,
    responses: dict[int, type | None] | None = None,
    request_body: type | None = None,
    parameters: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
):
    """
    Enhanced decorator to add OpenAPI documentation to endpoints.

    Automatically infers information from:
    - Function docstrings (summary/description)
    - Type annotations (response/request types)
    - Function signatures (parameters)
    - Module structure (tags)

    Manual parameters override automatic inference.
    """

    def decorator(func):
        endpoint = f"{func.__module__}.{func.__name__}"

        # Auto-infer from docstring if not provided
        if summary is None or description is None:
            auto_summary, auto_description = _parse_docstring(func.__doc__)
            final_summary = (
                summary or auto_summary or func.__name__.replace("_", " ").title()
            )
            final_description = description or auto_description
        else:
            final_summary = summary
            final_description = description

        # Auto-infer response types if not provided
        final_responses = responses or _infer_response_type(func)

        # Auto-infer request body if not provided
        final_request_body = request_body or _infer_request_body(func)

        # Auto-infer tags if not provided
        final_tags = tags or _infer_tags(func)

        _endpoint_docs[endpoint] = {
            "summary": final_summary,
            "description": final_description,
            "responses": final_responses,
            "request_body": final_request_body,
            "parameters": parameters,  # Will be inferred later when we have route info
            "tags": final_tags,
        }
        return func

    return decorator


def _create_base_spec() -> dict[str, Any]:
    """Create the base OpenAPI specification structure."""
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "gptme API",
            "version": __version__,
            "description": "Personal AI assistant server API",
            "contact": {"name": "gptme", "url": "https://gptme.org"},
            "license": {
                "name": "MIT",
                "url": "https://github.com/gptme/gptme/blob/master/LICENSE",
            },
        },
        "servers": [{"url": "/", "description": "gptme server"}],
        "paths": {},
        "components": {"schemas": {}},
    }


def _collect_models_from_endpoints() -> set[type[BaseModel]]:
    """Collect all Pydantic models used in endpoint documentation."""
    model_classes_set: set[type[BaseModel]] = set()

    def collect_models_from_type(type_hint: Any) -> None:
        """Recursively collect BaseModel classes from type annotations."""
        if isinstance(type_hint, type) and issubclass(type_hint, BaseModel):
            model_classes_set.add(type_hint)
        elif hasattr(type_hint, "__origin__"):
            # Handle generic types like list[TaskResponse], dict[str, Model], etc.
            if hasattr(type_hint, "__args__"):
                for arg in type_hint.__args__:
                    collect_models_from_type(arg)

    # Collect models from all documented endpoints
    for doc in _endpoint_docs.values():
        # Collect from request body
        if doc.get("request_body"):
            collect_models_from_type(doc["request_body"])

        # Collect from responses
        for response_type in doc.get("responses", {}).values():
            if response_type:
                collect_models_from_type(response_type)

    return model_classes_set


def _generate_schemas(model_classes: set[type[BaseModel]]) -> dict[str, Any]:
    """Generate schemas for all collected models."""
    all_schemas: dict[str, Any] = {}

    for cls in model_classes:
        try:
            # BaseModel has model_json_schema() method directly
            schema = cls.model_json_schema()

            # Add main schema
            all_schemas[cls.__name__] = schema

            # Extract any $defs and add them as top-level schemas
            if "$defs" in schema:
                for def_name, def_schema in schema["$defs"].items():
                    if def_name not in all_schemas:
                        all_schemas[def_name] = def_schema

                # Remove $defs from main schema since we've promoted them
                del schema["$defs"]

        except Exception as e:
            print(f"Warning: Could not generate schema for {cls.__name__}: {e}")

    return all_schemas


def _update_schema_refs(all_schemas: dict[str, Any]) -> dict[str, Any]:
    """Update all references to point to components/schemas."""

    def update_refs(obj: Any) -> Any:
        if isinstance(obj, dict):
            if "$ref" in obj and obj["$ref"].startswith("#/$defs/"):
                # Convert $defs reference to components/schemas reference
                ref_name = obj["$ref"].split("/")[-1]
                obj["$ref"] = f"#/components/schemas/{ref_name}"
            return {k: update_refs(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [update_refs(item) for item in obj]
        return obj

    # Apply reference updates to all schemas
    updated_schemas = {}
    for schema_name, schema in all_schemas.items():
        updated_schemas[schema_name] = update_refs(schema)

    return updated_schemas


def _convert_to_openapi_nullable(schema: dict) -> dict:
    """Recursively convert Pydantic's anyOf nullable patterns to OpenAPI 3.0 format."""
    if isinstance(schema, dict):
        # Handle anyOf nullable patterns
        if "anyOf" in schema:
            any_of_items = schema["anyOf"]
            if isinstance(any_of_items, list) and len(any_of_items) == 2:
                # Check for type + null pattern
                type_item = None
                null_item = None
                for item in any_of_items:
                    if isinstance(item, dict):
                        if item.get("type") == "null":
                            null_item = item
                        elif "type" in item:
                            type_item = item
                        elif "$ref" in item:
                            type_item = item

                if null_item and type_item:
                    # Convert to OpenAPI 3.0 nullable format
                    new_schema = {k: v for k, v in schema.items() if k != "anyOf"}
                    new_schema.update(type_item)
                    new_schema["nullable"] = True

                    # If field has enum, add null to the allowed values
                    if "enum" in new_schema and None not in new_schema["enum"]:
                        new_schema["enum"] = new_schema["enum"] + [None]

                    return new_schema

        # Handle direct nullable patterns (type + default: null)
        elif (
            "type" in schema
            and "default" in schema
            and schema["default"] is None
            and "nullable" not in schema
            and schema["type"] != "null"
        ):
            # This is a field with default: null but not explicitly nullable
            new_schema = schema.copy()
            new_schema["nullable"] = True

            # If field has enum, add null to the allowed values
            if "enum" in new_schema and None not in new_schema["enum"]:
                new_schema["enum"] = new_schema["enum"] + [None]

            return new_schema

        # Recursively process all dictionary values
        return {k: _convert_to_openapi_nullable(v) for k, v in schema.items()}
    elif isinstance(schema, list):
        return [_convert_to_openapi_nullable(item) for item in schema]
    else:
        return schema


def _process_route_parameters(
    view_func: Callable, rule_string: str, doc: dict[str, Any]
) -> list[dict[str, Any]]:
    """Process and merge route parameters from inference and manual specification."""
    # Always infer path parameters, then merge with manual parameters
    inferred_parameters = _infer_parameters(view_func, rule_string)
    manual_parameters = doc["parameters"] or []

    # Merge parameters, with manual parameters taking precedence
    final_parameters = []
    manual_param_names = {p["name"] for p in manual_parameters}

    # Add inferred path parameters that aren't manually overridden
    for param in inferred_parameters:
        if param["name"] not in manual_param_names:
            final_parameters.append(param)

    # Add manual parameters
    final_parameters.extend(manual_parameters)

    return final_parameters


def _create_method_spec(
    doc: dict[str, Any], method: str, final_parameters: list[dict[str, Any]]
) -> dict[str, Any]:
    """Create OpenAPI method specification for a single HTTP method."""
    method_spec: dict[str, Any] = {
        "summary": doc["summary"],
        "description": doc["description"],
        "tags": doc["tags"],
        "responses": {},
    }

    # Add responses with better descriptions
    for code, response_type in doc["responses"].items():
        if response_type:
            # Get description from response model if available
            response_description = (
                getattr(response_type, "__doc__", None) or f"HTTP {code}"
            )
            method_spec["responses"][str(code)] = {
                "description": response_description,
                "content": {
                    "application/json": {
                        "schema": {
                            "$ref": f"#/components/schemas/{response_type.__name__}"
                        }
                    }
                },
            }
        else:
            # Handle non-JSON responses (like file downloads)
            if code == 200:
                method_spec["responses"][str(code)] = {
                    "description": "File download or binary content",
                    "content": {
                        "application/octet-stream": {
                            "schema": {"type": "string", "format": "binary"}
                        }
                    },
                }
            else:
                method_spec["responses"][str(code)] = {
                    "description": f"HTTP {code} response"
                }

    # Add request body with better validation
    if doc["request_body"] and method.lower() in ["post", "put", "patch"]:
        request_description = (
            getattr(doc["request_body"], "__doc__", None) or "Request body"
        )
        method_spec["requestBody"] = {
            "description": request_description,
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "$ref": f"#/components/schemas/{doc['request_body'].__name__}"
                    }
                }
            },
        }

    # Add parameters (inferred or manual)
    if final_parameters:
        method_spec["parameters"] = final_parameters

    return method_spec


def _add_flask_routes_to_spec(spec: dict[str, Any]) -> None:
    """Add Flask routes to the OpenAPI specification."""
    # Add documented endpoints
    for rule in current_app.url_map.iter_rules():
        if rule.endpoint.startswith("static") or rule.endpoint.startswith(
            "openapi_docs"
        ):
            continue

        # Get the actual view function to match decorator storage format
        try:
            view_func = current_app.view_functions[rule.endpoint]
            endpoint_key = f"{view_func.__module__}.{view_func.__name__}"
        except (KeyError, AttributeError):
            continue

        if endpoint_key not in _endpoint_docs:
            continue

        doc = _endpoint_docs[endpoint_key]
        path = _convert_flask_path_to_openapi(rule.rule)
        methods = (rule.methods or set()) - {"HEAD", "OPTIONS"}

        final_parameters = _process_route_parameters(view_func, rule.rule, doc)

        paths_dict = spec["paths"]  # type: ignore
        if path not in paths_dict:
            paths_dict[path] = {}

        for method in methods:
            method_spec = _create_method_spec(doc, method, final_parameters)
            paths_dict[path][method.lower()] = method_spec  # type: ignore


def generate_openapi_spec() -> dict[str, Any]:
    """Generate OpenAPI spec from documented endpoints and dataclasses."""
    # Create base specification
    spec = _create_base_spec()

    # Collect and process models
    model_classes_set = _collect_models_from_endpoints()
    all_schemas = _generate_schemas(model_classes_set)
    all_schemas = _update_schema_refs(all_schemas)

    # Apply nullable conversion to all schemas
    for schema_name in list(all_schemas.keys()):
        all_schemas[schema_name] = _convert_to_openapi_nullable(
            all_schemas[schema_name]
        )

    # Add all schemas to spec
    spec["components"]["schemas"].update(all_schemas)  # type: ignore

    # Add Flask routes
    _add_flask_routes_to_spec(spec)

    # Apply conversion to parameter schemas in paths (after paths are populated)
    if "paths" in spec:
        spec["paths"] = _convert_to_openapi_nullable(spec["paths"])

    return spec


# Flask Blueprint
# ---------------

docs_api = Blueprint("openapi_docs", __name__, url_prefix="/api/docs")


@docs_api.route("/openapi.json")
def openapi_json():
    """Serve OpenAPI specification as JSON."""
    return jsonify(generate_openapi_spec())


@docs_api.route("/")
def swagger_ui():
    """Serve Swagger UI."""
    return """
<!DOCTYPE html>
<html>
<head>
    <title>gptme API Documentation</title>
    <link rel="stylesheet" type="text/css" href="https://unpkg.com/swagger-ui-dist@5.9.0/swagger-ui.css" />
</head>
<body>
    <div id="swagger-ui"></div>
    <script src="https://unpkg.com/swagger-ui-dist@5.9.0/swagger-ui-bundle.js"></script>
    <script src="https://unpkg.com/swagger-ui-dist@5.9.0/swagger-ui-standalone-preset.js"></script>
    <script>
        window.onload = function() {
            SwaggerUIBundle({
                url: '/api/docs/openapi.json',
                dom_id: '#swagger-ui',
                deepLinking: true,
                presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
                layout: "StandaloneLayout"
            });
        };
    </script>
</body>
</html>
    """
