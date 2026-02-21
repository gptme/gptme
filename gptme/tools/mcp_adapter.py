from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Generator
from logging import getLogger
from typing import TYPE_CHECKING

import mcp.types as mcp_types

if TYPE_CHECKING:
    from ..hooks.elicitation import ElicitationRequest, ElicitationResponse

from gptme.config import Config, MCPServerConfig, get_config, set_config

from ..mcp.client import MCPClient, MCPInterruptedError
from ..mcp.registry import MCPRegistry, format_server_details, format_server_list
from ..message import Message
from ..util.ask_execute import execute_with_confirmation
from .base import (
    ExecuteFunc,
    Parameter,
    ToolSpec,
    ToolUse,
)

# ConfirmFunc type removed - confirmation now uses hook system

logger = getLogger(__name__)

# Global storage for MCP clients
_mcp_clients: dict[str, MCPClient] = {}

# Add type annotation for tool_specs
tool_specs: list[ToolSpec] = []

# Global registry instance
_registry = MCPRegistry()

# Cache of dynamically loaded servers
_dynamic_servers: dict[str, MCPClient] = {}


def _get_mcp_client(server_name: str) -> MCPClient | None:
    """Get MCP client from either pre-configured or dynamically loaded servers."""
    return _mcp_clients.get(server_name) or _dynamic_servers.get(server_name)


def _extract_content_text(
    item: mcp_types.TextContent
    | mcp_types.ImageContent
    | mcp_types.AudioContent
    | mcp_types.ResourceLink
    | mcp_types.EmbeddedResource
    | str,
) -> str:
    """Extract text from a content item (TextContent, ImageContent, etc.).

    Per MCP spec, content items can be TextContent, ImageContent, AudioContent,
    ResourceLink, or EmbeddedResource. This function handles all types gracefully.
    """
    if isinstance(item, str):
        return item
    if isinstance(item, mcp_types.TextContent):
        return item.text
    if isinstance(item, mcp_types.ImageContent):
        return f"[Image: {item.mimeType}]"
    if isinstance(item, mcp_types.AudioContent):
        return f"[Audio: {item.mimeType}]"
    if isinstance(item, mcp_types.ResourceLink):
        return f"[Resource Link: {item.uri}]"
    if isinstance(item, mcp_types.EmbeddedResource):
        resource = item.resource
        uri = str(resource.uri)
        # TextResourceContents has text, BlobResourceContents has blob
        if isinstance(resource, mcp_types.TextResourceContents):
            return f"[Resource: {uri}]\n{resource.text}"
        return f"[Resource: {uri}]"
    return str(item)


def _restart_mcp_client(server_name: str, config: Config) -> MCPClient:
    """Restart an MCP client by reconnecting to the server"""
    logger.info(f"Restarting MCP client for server: {server_name}")

    # Get existing client if any
    old_client = _mcp_clients.get(server_name)

    # Close old client if it exists
    if old_client is not None:
        try:
            # Clean up the old client
            if old_client.stack:
                old_client.loop.run_until_complete(
                    old_client.stack.__aexit__(None, None, None)
                )
            old_client.loop.close()
            logger.debug(f"Closed old MCP client for {server_name}")
        except Exception as e:
            logger.debug(f"Error closing old MCP client: {e}")

    # Create new client and reconnect
    new_client = MCPClient(config=config)
    tools, session = new_client.connect(server_name)

    # Store the new client
    _mcp_clients[server_name] = new_client

    logger.info(f"Successfully restarted MCP client for {server_name}")
    return new_client


def _call_mcp_tool_with_retry(
    server_name: str,
    tool_name: str,
    arguments: dict,
    config: Config,
    max_retries: int = 1,
) -> str:
    """Call an MCP tool with automatic retry on connection failures"""
    from ..mcp.client import _is_connection_error

    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            # Get the client for this server
            client = _mcp_clients.get(server_name)
            if client is None:
                raise RuntimeError(f"No MCP client found for server: {server_name}")

            # Call the tool
            return client.call_tool(tool_name, arguments)

        except Exception as e:
            last_error = e

            if _is_connection_error(e) and attempt < max_retries:
                logger.info(f"MCP connection failed for {server_name}, restarting...")
                _restart_mcp_client(server_name, config)
                continue
            break

    # last_error will never be None here since we only break after setting it
    assert last_error is not None
    raise last_error


# Function to create MCP tools
def create_mcp_tools(config: Config) -> list[ToolSpec]:
    """Create tool specs for all MCP tools from the config"""

    tool_specs: list[ToolSpec] = []

    # Skip if MCP is not enabled
    if not config.mcp.enabled:
        return tool_specs

    # Initialize connections to all servers
    for server_config in config.mcp.servers:
        try:
            client = MCPClient(config=config)

            # Connect to server
            tools, session = client.connect(server_config.name)

            # Store the client globally for restart capability
            _mcp_clients[server_config.name] = client

            # Create tool specs for each tool
            for mcp_tool in tools.tools:
                # Extract parameters
                parameters = []
                # Check if the tool has inputSchema with properties
                input_schema = mcp_tool.inputSchema
                if isinstance(input_schema, dict) and "properties" in input_schema:
                    required_params = input_schema.get("required", [])
                    for param_name, param_schema in input_schema["properties"].items():
                        parameters.append(
                            Parameter(
                                name=param_name,
                                description=param_schema.get("description", ""),
                                type=param_schema.get("type", "string"),
                                required=param_name in required_params,
                            )
                        )

                # Add example usage in the correct format
                example = {
                    param.name: f"<{param.type}>"
                    for param in parameters
                    if param.required
                }
                example_str = json.dumps(example, indent=2)

                name = f"{server_config.name}.{mcp_tool.name}"

                def make_examples(
                    tool_name: str, example_content: str
                ) -> Callable[[str], str]:
                    return lambda tool_format: ToolUse(
                        tool_name, [], example_content
                    ).to_output(tool_format)  # type: ignore[arg-type]

                tool_spec = ToolSpec(
                    name=name,
                    desc=f"[{server_config.name}] {mcp_tool.description}",
                    parameters=parameters,
                    execute=create_mcp_execute_function(
                        mcp_tool.name, server_config.name, config
                    ),
                    available=True,
                    examples=make_examples(name, example_str),
                    block_types=[name],
                    is_mcp=True,
                )

                tool_specs.append(tool_spec)

        except (Exception, asyncio.CancelledError) as e:
            import traceback

            error_details = traceback.format_exc()
            logger.error(
                f"Failed to connect to MCP server {server_config.name}: {e}\n{error_details}"
            )

    return tool_specs


# Function to create execute function for a specific MCP tool
def create_mcp_execute_function(
    tool_name: str, server_name: str, config: Config
) -> ExecuteFunc:
    """Create an execute function for an MCP tool"""

    def preview_mcp(content: str, tool_name: str = tool_name) -> str | None:
        """Prepare preview content for MCP tool execution."""
        try:
            if content:
                # Try to parse and format the JSON parameters
                params = json.loads(content)
                return json.dumps(params, indent=2)
            return None
        except json.JSONDecodeError:
            return content  # Return as-is if not valid JSON

    def execute_mcp_impl(
        content: str, tool_name: str
    ) -> Generator[Message, None, None]:
        """Actual MCP tool implementation."""
        try:
            # Get the client for getting tool definition
            client = _mcp_clients.get(server_name)
            if client is None:
                raise RuntimeError(f"No MCP client found for server: {server_name}")

            # Get the tool definition from the client
            tool_def = None
            if client.tools is not None:
                tool_def = next(
                    (tool for tool in client.tools.tools if tool.name == tool_name),
                    None,
                )

            # Parse content as JSON
            try:
                kwargs = json.loads(content) if content else {}
            except json.JSONDecodeError as err:
                if tool_def and tool_def.inputSchema:
                    example = json.dumps(
                        {
                            prop_name: f"<{prop_info.get('type', 'string')}>"
                            for prop_name, prop_info in tool_def.inputSchema.get(
                                "properties", {}
                            ).items()
                        },
                        indent=2,
                    )
                else:
                    example = '{\n  "parameter": "value"\n}'
                raise ValueError(
                    f"Content must be a valid JSON object with parameters. Example:\n{example}"
                ) from err

            # Execute the tool with retry on connection failures
            result = _call_mcp_tool_with_retry(server_name, tool_name, kwargs, config)
            yield Message("system", result)
        except MCPInterruptedError:
            # User interrupted the operation - don't log as error, just inform
            yield Message(
                "system", "MCP operation interrupted. The server is still running."
            )
        except Exception as e:
            logger.error(f"Error executing MCP tool {tool_name}: {e}")

            # Provide a helpful error message with parameter information
            error_msg = f"Error executing tool: {e}\n\n"
            if tool_def and tool_def.inputSchema:
                error_msg += "Expected parameters:\n"
                for param_name, param_info in tool_def.inputSchema.get(
                    "properties", {}
                ).items():
                    required = (
                        "Required"
                        if param_name in tool_def.inputSchema.get("required", [])
                        else "Optional"
                    )
                    desc = param_info.get("description", "No description")
                    error_msg += f"- {param_name}: {desc} ({required})\n"

            yield Message("system", error_msg)

    def execute(
        code: str | None,
        args: list[str] | None,
        kwargs: dict[str, str] | None,
    ):
        """Execute an MCP tool with confirmation"""
        if not code:
            yield Message("system", "No parameters provided")
            return

        # Use execute_with_confirmation like save tool does
        yield from execute_with_confirmation(
            code,
            args,
            kwargs,
            execute_fn=lambda content, *_: execute_mcp_impl(content, tool_name),
            get_path_fn=lambda *_: None,  # MCP tools don't have paths
            preview_fn=lambda content, *_: preview_mcp(content),
            preview_lang="json",
            confirm_msg=f"Execute MCP tool '{tool_name}'?",
            allow_edit=True,
        )

    return execute


def search_mcp_servers(query: str = "", registry: str = "all", limit: int = 10) -> str:
    """
    Search for MCP servers in registries.

    Args:
        query: Search query (searches name, description, tags)
        registry: Which registry to search ('all', 'official', 'mcp.so')
        limit: Maximum number of results

    Returns:
        Formatted list of servers
    """
    if registry == "all":
        results = _registry.search_all(query, limit)
    elif registry == "official":
        results = _registry.search_official_registry(query, limit)
    elif registry == "mcp.so":
        results = _registry.search_mcp_so(query, limit)
    else:
        return f"Unknown registry: {registry}. Use 'all', 'official', or 'mcp.so'."

    return format_server_list(results)


def get_mcp_server_info(name: str) -> str:
    """
    Get detailed information about a specific MCP server.

    Args:
        name: Server name

    Returns:
        Formatted server details
    """
    server = _registry.get_server_details(name)
    if not server:
        return f"Server '{name}' not found in any registry."

    return format_server_details(server)


def load_mcp_server(name: str, config_override: dict | None = None) -> str:
    """
    Dynamically load an MCP server during the session.

    Args:
        name: Server name (will search registries if not in config)
        config_override: Optional config overrides (command, args, url, etc.)

    Returns:
        Status message
    """
    config = get_config()

    # Check if server already loaded
    if name in _dynamic_servers:
        return f"Server '{name}' is already loaded."

    # Check if server is in config
    server_config = next((s for s in config.mcp.servers if s.name == name), None)

    # If not in config, try to find in registry
    if not server_config:
        server_info = _registry.get_server_details(name)
        if not server_info:
            return f"Server '{name}' not found in config or registries."

        # Create config from registry info
        server_config = MCPServerConfig(
            name=server_info.name,
            enabled=True,
            command=server_info.command,
            args=server_info.args,
            url=server_info.url,
        )

    # Apply config overrides
    if config_override:
        if "command" in config_override:
            server_config.command = config_override["command"]
        if "args" in config_override:
            server_config.args = config_override["args"]
        if "url" in config_override:
            server_config.url = config_override["url"]
        if "env" in config_override:
            server_config.env = config_override["env"]

    # Add to config BEFORE connecting (connect() looks up server by name in config)
    config_added = False
    if server_config not in config.mcp.servers:
        config.mcp.servers.append(server_config)
        set_config(config)
        config_added = True

    try:
        # Create client and connect
        client = MCPClient(config=config)
        tools, session = client.connect(name)

        # Store in dynamic servers
        _dynamic_servers[name] = client

        tool_names = [tool.name for tool in tools.tools]
        return f"Successfully loaded server '{name}' with {len(tool_names)} tools: {', '.join(tool_names)}"

    except Exception as e:
        # If connection failed and we added the config, remove it to maintain consistency
        if config_added:
            config.mcp.servers = [s for s in config.mcp.servers if s.name != name]
            set_config(config)
        logger.error(f"Failed to load server '{name}': {e}")
        return f"Failed to load server '{name}': {e}"


def unload_mcp_server(name: str) -> str:
    """
    Unload a dynamically loaded MCP server.

    Args:
        name: Server name

    Returns:
        Status message
    """
    if name not in _dynamic_servers:
        return f"Server '{name}' is not loaded."

    # Remove from dynamic servers
    del _dynamic_servers[name]

    # Optionally disable in config (but don't remove)
    config = get_config()
    server_config = next((s for s in config.mcp.servers if s.name == name), None)
    if server_config:
        server_config.enabled = False
        set_config(config)

    return f"Successfully unloaded server '{name}'."


def list_loaded_servers() -> str:
    """
    List all currently loaded MCP servers.

    Returns:
        Formatted list of loaded servers
    """
    config = get_config()

    if not config.mcp.servers:
        return "No MCP servers configured."

    output = ["# Loaded MCP Servers\n"]

    for server in config.mcp.servers:
        status = "✓ enabled" if server.enabled else "✗ disabled"
        dynamic = " (dynamic)" if server.name in _dynamic_servers else ""
        output.append(f"- **{server.name}** {status}{dynamic}")
        if server.command:
            output.append(f"  Command: `{server.command}`")
        elif server.url:
            output.append(f"  URL: `{server.url}`")
        output.append("")

    return "\n".join(output)


def list_mcp_resources(server_name: str) -> str:
    """
    List available resources from an MCP server.

    Args:
        server_name: Name of the loaded MCP server

    Returns:
        Formatted list of available resources
    """
    client = _get_mcp_client(server_name)
    if not client:
        return (
            f"Server '{server_name}' is not loaded. Use `mcp load {server_name}` first."
        )

    try:
        result = client.list_resources()
        resources = result.resources

        if not resources:
            return f"No resources available from server '{server_name}'."

        output = [f"# Resources from {server_name}\n"]
        for resource in resources:
            output.append(f"## {resource.name}")
            output.append(f"**URI:** `{resource.uri}`")
            if resource.description:
                output.append(f"**Description:** {resource.description}")
            if resource.mimeType:
                output.append(f"**MIME Type:** {resource.mimeType}")
            output.append("")

        return "\n".join(output)
    except MCPInterruptedError:
        return "MCP operation interrupted. The server is still running."
    except Exception as e:
        logger.error(f"Failed to list resources from {server_name}: {e}")
        return f"Error listing resources: {e}"


def read_mcp_resource(server_name: str, uri: str) -> str:
    """
    Read a specific resource from an MCP server.

    Args:
        server_name: Name of the loaded MCP server
        uri: URI of the resource to read

    Returns:
        Resource content as string
    """
    client = _get_mcp_client(server_name)
    if not client:
        return (
            f"Server '{server_name}' is not loaded. Use `mcp load {server_name}` first."
        )

    try:
        result = client.read_resource(uri)
        contents = result.contents

        if not contents:
            return f"No content returned for resource '{uri}'."

        output = []
        for content in contents:
            if isinstance(content, mcp_types.TextResourceContents):
                output.append(content.text)
            elif isinstance(content, mcp_types.BlobResourceContents):
                # For binary content, indicate it's binary
                output.append(f"[Binary content: {len(content.blob)} bytes]")
            else:
                output.append(str(content))

        return "\n".join(output)
    except MCPInterruptedError:
        return "MCP operation interrupted. The server is still running."
    except Exception as e:
        logger.error(f"Failed to read resource {uri} from {server_name}: {e}")
        return f"Error reading resource: {e}"


def list_mcp_resource_templates(server_name: str) -> str:
    """
    List available resource templates from an MCP server.

    Resource templates are parameterized resources like `db://table/{name}`.

    Args:
        server_name: Name of the loaded MCP server

    Returns:
        Formatted list of available resource templates
    """
    client = _get_mcp_client(server_name)
    if not client:
        return (
            f"Server '{server_name}' is not loaded. Use `mcp load {server_name}` first."
        )

    try:
        result = client.list_resource_templates()
        templates = result.resourceTemplates

        if not templates:
            return f"No resource templates available from server '{server_name}'."

        output = [f"# Resource Templates from {server_name}\n"]
        for template in templates:
            output.append(f"## {template.name}")
            output.append(f"**URI Template:** `{template.uriTemplate}`")
            if template.description:
                output.append(f"**Description:** {template.description}")
            if template.mimeType:
                output.append(f"**MIME Type:** {template.mimeType}")
            output.append("")

        return "\n".join(output)
    except MCPInterruptedError:
        return "MCP operation interrupted. The server is still running."
    except Exception as e:
        logger.error(f"Failed to list resource templates from {server_name}: {e}")
        return f"Error listing resource templates: {e}"


def list_mcp_prompts(server_name: str) -> str:
    """
    List available prompts from an MCP server.

    Args:
        server_name: Name of the loaded MCP server

    Returns:
        Formatted list of available prompts
    """
    client = _get_mcp_client(server_name)
    if not client:
        return (
            f"Server '{server_name}' is not loaded. Use `mcp load {server_name}` first."
        )

    try:
        result = client.list_prompts()
        prompts = result.prompts

        if not prompts:
            return f"No prompts available from server '{server_name}'."

        output = [f"# Prompts from {server_name}\n"]
        for prompt in prompts:
            output.append(f"## {prompt.name}")
            if prompt.description:
                output.append(f"**Description:** {prompt.description}")
            if prompt.arguments:
                output.append("**Arguments:**")
                for arg in prompt.arguments:
                    required = " (required)" if arg.required else ""
                    desc = f" - {arg.description}" if arg.description else ""
                    output.append(f"  - `{arg.name}`{required}{desc}")
            output.append("")

        return "\n".join(output)
    except MCPInterruptedError:
        return "MCP operation interrupted. The server is still running."
    except Exception as e:
        logger.error(f"Failed to list prompts from {server_name}: {e}")
        return f"Error listing prompts: {e}"


def get_mcp_prompt(
    server_name: str, name: str, arguments: dict[str, str] | None = None
) -> str:
    """
    Get a specific prompt from an MCP server.

    Args:
        server_name: Name of the loaded MCP server
        name: Name of the prompt to retrieve
        arguments: Optional arguments for the prompt

    Returns:
        Formatted prompt content
    """
    client = _get_mcp_client(server_name)
    if not client:
        return (
            f"Server '{server_name}' is not loaded. Use `mcp load {server_name}` first."
        )

    try:
        result = client.get_prompt(name, arguments)
        messages = result.messages

        if not messages:
            return f"Prompt '{name}' returned no messages."

        output = [f"# Prompt: {name}\n"]
        if result.description:
            output.append(f"**Description:** {result.description}\n")

        for i, msg in enumerate(messages):
            output.append(f"## Message {i + 1} ({msg.role})")

            content = msg.content
            # Handle content as TextContent or ImageContent per MCP spec
            text = _extract_content_text(content)
            if text:
                output.append(text)
            output.append("")

        return "\n".join(output)
    except MCPInterruptedError:
        return "MCP operation interrupted. The server is still running."
    except Exception as e:
        logger.error(f"Failed to get prompt '{name}' from {server_name}: {e}")
        return f"Error getting prompt: {e}"


# Roots functions - client-side configuration for MCP servers


def list_mcp_roots(server_name: str | None = None) -> str:
    """
    List configured roots for MCP servers.

    Roots define operational boundaries (directories, URIs) that servers can access.
    They are advisory, helping servers understand the workspace context.

    Args:
        server_name: Optional server name. If provided, lists roots for that server only.
                     If None, lists roots for all loaded servers.

    Returns:
        Formatted list of configured roots
    """
    if server_name:
        client = _get_mcp_client(server_name)
        if not client:
            return f"Server '{server_name}' is not loaded. Use `mcp load {server_name}` first."

        roots = client.get_roots()
        if not roots:
            return f"No roots configured for server '{server_name}'."

        output = [f"# Roots for {server_name}\n"]
        output.extend(
            f"- **{root.name or '(unnamed)'}**: `{root.uri}`" for root in roots
        )
        return "\n".join(output)
    # List roots for all loaded servers
    all_clients = {**_mcp_clients, **_dynamic_servers}
    if not all_clients:
        return "No MCP servers loaded."

    output = ["# Configured Roots\n"]
    for name, client in all_clients.items():
        roots = client.get_roots()
        output.append(f"## {name}")
        if roots:
            for root in roots:
                output.append(f"- **{root.name or '(unnamed)'}**: `{root.uri}`")
        else:
            output.append("_No roots configured_")
        output.append("")
    return "\n".join(output)


def add_mcp_root(server_name: str, uri: str, name: str | None = None) -> str:
    """
    Add a root to an MCP server.

    Roots tell servers what directories or URIs they can operate on.
    After adding, the server is notified of the change.

    Args:
        server_name: Name of the loaded MCP server
        uri: URI of the root (e.g., 'file:///path/to/project')
        name: Optional human-readable name for the root

    Returns:
        Success message or error
    """
    client = _get_mcp_client(server_name)
    if not client:
        return (
            f"Server '{server_name}' is not loaded. Use `mcp load {server_name}` first."
        )

    try:
        added = client.add_root(uri, name)
        if added:
            return f"Added root '{name or uri}' to server '{server_name}'."
        return f"Root '{uri}' already exists for server '{server_name}'."
    except Exception as e:
        logger.error(f"Failed to add root to {server_name}: {e}")
        return f"Error adding root: {e}"


def remove_mcp_root(server_name: str, uri: str) -> str:
    """
    Remove a root from an MCP server.

    After removing, the server is notified of the change.

    Args:
        server_name: Name of the loaded MCP server
        uri: URI of the root to remove

    Returns:
        Success message or error
    """
    client = _get_mcp_client(server_name)
    if not client:
        return (
            f"Server '{server_name}' is not loaded. Use `mcp load {server_name}` first."
        )

    try:
        removed = client.remove_root(uri)
        if removed:
            return f"Removed root '{uri}' from server '{server_name}'."
        return f"Root '{uri}' not found in server '{server_name}'."
    except Exception as e:
        logger.error(f"Failed to remove root from {server_name}: {e}")
        return f"Error removing root: {e}"


# Elicitation support functions


def _mcp_params_to_elicitation_request(
    params: mcp_types.ElicitRequestParams,
    server_name: str,
) -> ElicitationRequest:
    """Convert MCP ElicitRequestParams to gptme's ElicitationRequest.

    Maps MCP's JSON Schema-based requestedSchema to gptme's FormField-based
    form elicitation, allowing MCP servers to use the shared elicitation UI.

    Handles both MCP elicitation param types:
    - ElicitRequestFormParams (has requestedSchema)
    - ElicitRequestURLParams (has url, no schema)
    """
    from ..hooks.elicitation import ElicitationRequest, FormField

    prompt = f"MCP Server '{server_name}': {params.message}"

    # URL-mode params don't have a schema — treat as text
    schema = getattr(params, "requestedSchema", None)

    # If no schema, treat as simple text input
    if not schema or "properties" not in schema:
        return ElicitationRequest(type="text", prompt=prompt)

    # Convert JSON Schema properties to FormFields
    properties = schema.get("properties", {})
    required_fields = schema.get("required", [])
    fields: list[FormField] = []

    for field_name, field_info in properties.items():
        json_type = field_info.get("type", "string")
        field_desc = field_info.get("description", field_name)
        field_default = field_info.get("default")

        # Map JSON Schema types to FormField types
        if json_type == "boolean":
            form_type: str = "boolean"
        elif json_type in ("integer", "number"):
            form_type = "number"
        else:
            form_type = "text"

        fields.append(
            FormField(
                name=field_name,
                prompt=field_desc,
                type=form_type,  # type: ignore[arg-type]
                required=field_name in required_fields,
                default=str(field_default) if field_default is not None else None,
            )
        )

    return ElicitationRequest(type="form", prompt=prompt, fields=fields)


def _elicitation_response_to_mcp_result(
    response: ElicitationResponse,
) -> mcp_types.ElicitResult:
    """Convert gptme's ElicitationResponse to MCP's ElicitResult."""
    if response.cancelled:
        return mcp_types.ElicitResult(action="cancel", content=None)

    if response.value is None:
        return mcp_types.ElicitResult(action="decline", content=None)

    # For form responses, the value is a JSON string of field values
    try:
        content = json.loads(response.value)
        if isinstance(content, dict):
            return mcp_types.ElicitResult(action="accept", content=content)
    except (json.JSONDecodeError, TypeError):
        pass

    # For simple text responses, wrap in a content dict
    return mcp_types.ElicitResult(action="accept", content={"value": response.value})


def _create_elicitation_handler(server_name: str):
    """Create an elicitation callback for a server using the shared hook system.

    Uses gptme's unified elicitation system (hooks/elicitation.py) so MCP
    servers get the same rich input UI as native gptme elicitation. When
    WebUI support is added, MCP servers get it automatically.

    Returns an async callable compatible with MCPClient's elicitation_callback.
    """

    async def elicitation_callback(
        params: mcp_types.ElicitRequestParams,
    ) -> mcp_types.ElicitResult | mcp_types.ErrorData:
        """Handle elicitation request from MCP server via shared hook system."""
        from ..hooks.elicitation import elicit

        logger.info(f"Elicitation request from {server_name}: {params.message}")

        try:
            request = _mcp_params_to_elicitation_request(params, server_name)
            response = elicit(request)
            return _elicitation_response_to_mcp_result(response)
        except Exception as e:
            logger.error(f"Elicitation error for {server_name}: {e}")
            return mcp_types.ErrorData(code=-32000, message=str(e))

    return elicitation_callback


def enable_mcp_elicitation(server_name: str) -> str:
    """
    Enable elicitation support for an MCP server.

    When enabled, the server can request user input during operations.
    A CLI-based handler prompts the user for input when requests come in.

    Args:
        server_name: Name of the loaded MCP server

    Returns:
        Success message or error
    """
    client = _get_mcp_client(server_name)
    if not client:
        return (
            f"Server '{server_name}' is not loaded. Use `mcp load {server_name}` first."
        )

    try:
        callback = _create_elicitation_handler(server_name)
        client.set_elicitation_callback(callback)
        return f"Elicitation enabled for server '{server_name}'. Server can now request user input."
    except Exception as e:
        logger.error(f"Failed to enable elicitation for {server_name}: {e}")
        return f"Error enabling elicitation: {e}"


def disable_mcp_elicitation(server_name: str) -> str:
    """
    Disable elicitation support for an MCP server.

    Args:
        server_name: Name of the loaded MCP server

    Returns:
        Success message or error
    """
    client = _get_mcp_client(server_name)
    if not client:
        return (
            f"Server '{server_name}' is not loaded. Use `mcp load {server_name}` first."
        )

    try:
        client.set_elicitation_callback(None)
        return f"Elicitation disabled for server '{server_name}'."
    except Exception as e:
        logger.error(f"Failed to disable elicitation for {server_name}: {e}")
        return f"Error disabling elicitation: {e}"


def get_mcp_elicitation_status(server_name: str | None = None) -> str:
    """
    Get elicitation status for MCP servers.

    Args:
        server_name: Optional server name. If provided, shows status for that server.
                     If None, shows status for all loaded servers.

    Returns:
        Formatted elicitation status
    """
    if server_name:
        client = _get_mcp_client(server_name)
        if not client:
            return f"Server '{server_name}' is not loaded."

        enabled = client.has_elicitation_callback()
        status = "✅ Enabled" if enabled else "❌ Disabled"
        return f"Elicitation for '{server_name}': {status}"
    # Show status for all loaded servers
    all_clients = {**_mcp_clients, **_dynamic_servers}
    if not all_clients:
        return "No MCP servers loaded."

    output = ["# Elicitation Status\n"]
    for name, client in all_clients.items():
        enabled = client.has_elicitation_callback()
        status = "✅ Enabled" if enabled else "❌ Disabled"
        output.append(f"- **{name}**: {status}")
    return "\n".join(output)
