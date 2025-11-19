"""
V2 API agents management.

Handles agent creation and management endpoints.
"""

import logging
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import flask
import tomlkit

from gptme.config import AgentConfig, ChatConfig, ProjectConfig, get_project_config
from gptme.prompts import get_prompt

from ..dirs import get_logs_dir
from ..logmanager import LogManager
from ..tools import get_toolchain
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

    # Ensure the folder is empty
    if path.exists():
        return flask.jsonify({"error": f"Folder/path already exists: {path}"}), 400

    project_config = req_json.get("project_config")
    if project_config:
        project_config = ProjectConfig.from_dict(project_config, workspace=path)

    # Clone the template repo into a temp dir
    temp_base = tempfile.gettempdir()
    temp_dir = Path(temp_base) / str(uuid.uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)

    command = ["git", "clone"]
    if template_branch:
        command.extend(["--branch", template_branch])
    command.append(template_repo)
    command.append(str(temp_dir))

    clone_result = subprocess.run(command, capture_output=True, check=False)
    if clone_result.returncode != 0:
        return flask.jsonify(
            {"error": f"Failed to clone template repo: {clone_result.stderr.decode()}"}
        ), 500

    # Pull in any git submodules
    submodule_result = subprocess.run(
        ["git", "submodule", "update", "--init", "--recursive"],
        capture_output=True,
        check=False,
        cwd=temp_dir,
    )
    if submodule_result.returncode != 0:
        # Delete the temp dir if the submodule update failed
        shutil.rmtree(temp_dir)
        return flask.jsonify(
            {
                "error": f"Failed to update submodules: {submodule_result.stderr.decode()}"
            }
        ), 500
    logger.info(f"Cloned template repo to {temp_dir}")

    # Run the post-fork command
    try:
        post_fork_result = subprocess.run(
            shlex.split(fork_command), capture_output=True, check=False, cwd=temp_dir
        )
        logger.debug(f"Post-fork command result: {post_fork_result}")
        if post_fork_result.returncode != 0:
            error_msg = post_fork_result.stderr.decode()
            if not error_msg:
                error_msg = post_fork_result.stdout.decode()

            # Delete the temp dir and workspace if the post-fork command failed
            shutil.rmtree(temp_dir)
            if path.exists():
                shutil.rmtree(path)

            return flask.jsonify(
                {"error": f"Failed to run post-fork command: {error_msg}"}
            ), 500
    except Exception as e:
        # Delete the temp dir and workspace if the post-fork command failed
        shutil.rmtree(temp_dir)
        if path.exists():
            shutil.rmtree(path)
        return flask.jsonify({"error": f"Failed to run post-fork command: {e}"}), 500
    logger.info(f"Post-fork command executed successfully: {fork_command}")

    # Merge in the project config
    # TODO: with layered project configs (https://github.com/gptme/gptme/issues/584), this should be more sophisticated
    current_project_config = get_project_config(path)
    if not current_project_config and not project_config:
        # No project config, just write the agent name to the config
        project_config = ProjectConfig(agent=AgentConfig(name=agent_name))
    elif current_project_config and project_config:
        # Merge in the project config
        project_config = current_project_config.merge(project_config)
    elif current_project_config and not project_config:
        # Use the current project config
        project_config = current_project_config

    # Set agent name if not set
    if not project_config.agent or not project_config.agent.name:
        project_config.agent = AgentConfig(name=agent_name)

    # Write the project config
    with open(path / "gptme.toml", "w") as f:
        f.write(tomlkit.dumps(project_config.to_dict()))

    # Delete the temp dir
    shutil.rmtree(temp_dir)

    # Create a new empty conversation in the workspace
    conversation_id = str(uuid.uuid4())
    logdir = get_logs_dir() / conversation_id

    # Create the log directory
    logdir.mkdir(parents=True)

    # Load or create the chat config, overriding values from request config if provided
    request_config = ChatConfig(workspace=path, agent=path)
    chat_config = ChatConfig.load_or_create(logdir, request_config).save()

    msgs = get_prompt(
        tools=[t for t in get_toolchain(chat_config.tools)],
        interactive=chat_config.interactive,
        tool_format=chat_config.tool_format or "markdown",
        model=chat_config.model,
        workspace=path,
        agent_path=chat_config.agent,
    )

    log = LogManager.load(logdir=logdir, initial_msgs=msgs, create=True)
    log.write()

    return flask.jsonify(
        {
            "status": "ok",
            "message": "Agent created",
            "initial_conversation_id": conversation_id,
            "agent_path": str(path),
        }
    )
