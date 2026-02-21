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
INITIAL_WORKING_DIRECTORY = Path.cwd()


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
    req_json = flask.request.json
    if not req_json:
        return flask.jsonify({"error": "No JSON data provided"}), 400

    agent_name = req_json.get("name")
    if not agent_name:
        return flask.jsonify({"error": "name is required"}), 400

    template_repo = req_json.get("template_repo")
    if not template_repo:
        return flask.jsonify({"error": "template_repo is required"}), 400

    template_branch = req_json.get("template_branch")
    if not template_branch:
        return flask.jsonify({"error": "template_branch is required"}), 400

    fork_command = req_json.get("fork_command")
    if not fork_command:
        return flask.jsonify({"error": "fork_command is required"}), 400

    path = req_json.get("path")
    if not path:
        # Auto-generate path from initial directory + slugified agent name
        agent_slug = slugify_name(agent_name)
        path = INITIAL_WORKING_DIRECTORY / agent_slug
    else:
        path = Path(path).expanduser().resolve()

    # Ensure path is a Path object and resolved
    path = Path(path).expanduser().resolve()

    # Parse project config if provided
    project_config = req_json.get("project_config")
    if project_config:
        project_config = ProjectConfig.from_dict(project_config, workspace=path)

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
        return flask.jsonify({"error": "Failed to initialize conversation"}), 500

    return flask.jsonify(
        {
            "status": "ok",
            "message": "Agent created",
            "initial_conversation_id": conversation_id,
            "agent_path": str(path),
        }
    )
