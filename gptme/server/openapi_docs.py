"""
OpenAPI documentation using pydantic dataclasses.

Simple decorator-based approach for existing function routes.
"""

from typing import Any

from flask import Blueprint, jsonify, current_app
from pydantic import BaseModel, Field


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


class ChatConfig(BaseModel):
    """Chat configuration."""

    name: str | None = Field(None, description="Conversation name")
    model: str | None = Field(None, description="Default model")
    tools: list[str] | None = Field(None, description="Enabled tools")
    workspace: str | None = Field(None, description="Workspace path")


# Simple decorator for OpenAPI documentation
# ------------------------------------------

_endpoint_docs: dict[str, dict[str, Any]] = {}


def api_doc(
    summary: str,
    description: str = "",
    responses: dict[int, type | None] | None = None,
    request_body: type | None = None,
    parameters: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
):
    """Decorator to add OpenAPI documentation to endpoints."""

    def decorator(func):
        endpoint = f"{func.__module__}.{func.__name__}"
        _endpoint_docs[endpoint] = {
            "summary": summary,
            "description": description,
            "responses": responses or {},
            "request_body": request_body,
            "parameters": parameters or [],
            "tags": tags or [],
        }
        return func

    return decorator


def generate_openapi_spec() -> dict[str, Any]:
    """Generate OpenAPI spec from documented endpoints and dataclasses."""

    spec: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title": "gptme API",
            "version": "2.0.0",
            "description": "Personal AI assistant server API",
            "contact": {"name": "gptme", "url": "https://gptme.org"},
            "license": {
                "name": "MIT",
                "url": "https://github.com/gptme/gptme/blob/main/LICENSE",
            },
        },
        "servers": [{"url": "/", "description": "gptme server"}],
        "paths": {},
        "components": {"schemas": {}},
    }

    # Add schemas from pydantic models
    model_classes: list[type[BaseModel]] = [
        ConversationListItem,
        Message,
        Conversation,
        MessageCreateRequest,
        GenerateRequest,
        FileMetadata,
        ErrorResponse,
        StatusResponse,
        ConversationResponse,
        ConversationListResponse,
        ConversationCreateRequest,
        GenerateResponse,
        SessionResponse,
        StepRequest,
        ToolConfirmRequest,
        InterruptRequest,
        ChatConfig,
    ]

    # Generate schemas and collect all definitions
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

    # Update all references to point to components/schemas
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
    for schema_name, schema in all_schemas.items():
        all_schemas[schema_name] = update_refs(schema)

    # Add all schemas to spec
    spec["components"]["schemas"].update(all_schemas)  # type: ignore

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
        path = rule.rule
        methods = rule.methods - {"HEAD", "OPTIONS"}

        paths_dict = spec["paths"]  # type: ignore
        if path not in paths_dict:
            paths_dict[path] = {}

        for method in methods:
            method_spec: dict[str, Any] = {
                "summary": doc["summary"],
                "description": doc["description"],
                "tags": doc["tags"],
                "responses": {},
            }

            # Add responses
            for code, response_type in doc["responses"].items():
                if response_type:
                    method_spec["responses"][str(code)] = {
                        "description": f"HTTP {code}",
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
                            "description": "File download",
                            "content": {
                                "application/octet-stream": {
                                    "schema": {"type": "string", "format": "binary"}
                                }
                            },
                        }
                    else:
                        method_spec["responses"][str(code)] = {
                            "description": f"HTTP {code}"
                        }

            # Add request body
            if doc["request_body"] and method.lower() in ["post", "put", "patch"]:
                method_spec["requestBody"] = {
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": f"#/components/schemas/{doc['request_body'].__name__}"
                            }
                        }
                    }
                }

            # Add parameters
            if doc["parameters"]:
                method_spec["parameters"] = doc["parameters"]

            paths_dict[path][method.lower()] = method_spec  # type: ignore

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
