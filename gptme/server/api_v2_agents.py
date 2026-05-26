"""
V2 API agents management.

Handles agent creation and management endpoints.
"""

import logging
import re
from pathlib import Path

import flask

from gptme.config import ProjectConfig

# Import shared workspace functions
from ..agent.workspace import (
    WorkspaceError,
    create_workspace_from_template,
    init_conversation,
)
from .auth import require_auth
from .openapi_docs import (
    AgentCreateRequest,
    AgentCreateResponse,
    ErrorResponse,
    api_doc,
)

logger = logging.getLogger(__name__)

# Store the initial working directory when the module is imported
INITIAL_WORKING_DIRECTORY = Path.cwd().resolve()


def slugify_name(name: str) -> str:
    """Convert agent name to a filesystem-safe slug."""
    # Convert to lowercase and replace spaces/special chars with hyphens
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug.strip("-")


agents_api = flask.Blueprint("agents_api", __name__)


@agents_api.route("/api/v2/agents", methods=["PUT"])
@require_auth
@api_doc(
    summary="Create a new agent",
    description="Create a new agent by cloning a template repository and setting up workspace",
    request_body=AgentCreateRequest,
    responses={200: AgentCreateResponse, 400: ErrorResponse, 500: ErrorResponse},
    tags=["agents"],
)
def api_agents_put():
    """Create a new agent."""
    req_json = flask.request.get_json(silent=True)
    if req_json is None:
        return flask.jsonify({"error": "No JSON data provided"}), 400
    if not isinstance(req_json, dict):
        return flask.jsonify({"error": "Request body must be a JSON object"}), 400

    agent_name = req_json.get("name")
    if agent_name is None or agent_name == "":
        return flask.jsonify({"error": "name is required"}), 400
    if not isinstance(agent_name, str):
        return flask.jsonify({"error": "name must be a string"}), 400

    template_repo = req_json.get("template_repo")
    if template_repo is None or template_repo == "":
        return flask.jsonify({"error": "template_repo is required"}), 400
    if not isinstance(template_repo, str):
        return flask.jsonify({"error": "template_repo must be a string"}), 400

    template_branch = req_json.get("template_branch")
    if template_branch is None or template_branch == "":
        return flask.jsonify({"error": "template_branch is required"}), 400
    if not isinstance(template_branch, str):
        return flask.jsonify({"error": "template_branch must be a string"}), 400

    fork_command = req_json.get("fork_command")
    if fork_command is None or fork_command == "":
        return flask.jsonify({"error": "fork_command is required"}), 400
    if not isinstance(fork_command, str):
        return flask.jsonify({"error": "fork_command must be a string"}), 400

    path = req_json.get("path")
    if path is not None and not isinstance(path, str):
        return flask.jsonify({"error": "path must be a string"}), 400
    if not path:
        # Auto-generate path from initial directory + slugified agent name
        agent_slug = slugify_name(agent_name)
        if not agent_slug:
            return (
                flask.jsonify(
                    {
                        "error": "agent name must contain at least one alphanumeric character"
                    }
                ),
                400,
            )
        path = INITIAL_WORKING_DIRECTORY / agent_slug
    else:
        path = Path(path).expanduser()

    # Ensure path is a Path object and resolved
    path = Path(path).resolve()

    # Validate that the resolved path is within the server's working directory
    # to prevent creating workspaces at arbitrary filesystem locations
    try:
        path.relative_to(INITIAL_WORKING_DIRECTORY)
    except ValueError:
        logger.warning(
            "Rejected agent creation path outside working directory",
            extra={
                "requested_path": str(path),
                "working_directory": str(INITIAL_WORKING_DIRECTORY),
            },
        )
        return (
            flask.jsonify(
                {"error": "Path must be within the server working directory"}
            ),
            400,
        )

    # Parse project config if provided
    project_config_raw = req_json.get("project_config")
    if project_config_raw is not None and not isinstance(project_config_raw, dict):
        return flask.jsonify({"error": "project_config must be an object"}), 400
    project_config: ProjectConfig | None = None
    if project_config_raw:
        try:
            project_config = ProjectConfig.from_dict(project_config_raw, workspace=path)
        except ValueError as exc:
            return flask.jsonify({"error": f"Invalid project_config: {exc}"}), 400

    # Create workspace using shared module
    try:
        create_workspace_from_template(
            path=path,
            agent_name=agent_name,
            template_repo=template_repo,
            template_branch=template_branch,
            fork_command=fork_command,
            project_config=project_config,
        )
    except WorkspaceError as e:
        error_msg = str(e)
        logger.error(f"Workspace creation failed: {error_msg}")

        # Determine appropriate HTTP status code
        if "already exists" in error_msg:
            return flask.jsonify({"error": f"Folder/path already exists: {path}"}), 400
        if "timed out" in error_msg.lower():
            return flask.jsonify({"error": error_msg}), 504
        return flask.jsonify({"error": error_msg}), 500

    # Create initial conversation using shared module
    try:
        conversation_id = init_conversation(workspace=path)
    except Exception as e:
        logger.exception(f"Failed to initialize conversation: {e}")
        return flask.jsonify({"error": f"Failed to initialize conversation: {e}"}), 500

    return flask.jsonify(
        {
            "status": "ok",
            "message": "Agent created",
            "initial_conversation_id": conversation_id,
            "agent_path": str(path),
        }
    )
