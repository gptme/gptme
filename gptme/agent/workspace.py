"""
Shared workspace module for agent creation.

This module provides functions for creating and managing agent workspaces,
shared between the CLI (gptme-agent) and server (API v2 agents endpoint).

Key functions:
- create_workspace_from_template: Clone gptme-agent-template and customize
- create_workspace_structure: Create standard directories (fallback)
- write_agent_config: Write gptme.toml using ProjectConfig
- generate_run_script: Create autonomous run script
- init_conversation: Initialize first conversation for agent
"""

import logging
import shlex
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import tomlkit

from gptme.config import AgentConfig, ChatConfig, ProjectConfig, get_project_config
from gptme.prompts import get_prompt

from ..dirs import get_logs_dir
from ..logmanager import LogManager
from ..tools import get_toolchain

logger = logging.getLogger(__name__)

# Default template repository
DEFAULT_TEMPLATE_REPO = "https://github.com/gptme/gptme-agent-template"
DEFAULT_TEMPLATE_BRANCH = "master"
DEFAULT_FORK_COMMAND = "./fork.sh {path} {name}"


class WorkspaceError(Exception):
    """Error during workspace creation or management."""

    pass


def create_workspace_from_template(
    path: Path,
    agent_name: str,
    template_repo: str = DEFAULT_TEMPLATE_REPO,
    template_branch: str = DEFAULT_TEMPLATE_BRANCH,
    fork_command: str | None = None,
    project_config: ProjectConfig | None = None,
    timeout: int = 300,
) -> Path:
    """
    Create an agent workspace by cloning a template repository.

    This is the recommended approach for creating agent workspaces.
    It clones the gptme-agent-template repository and runs the fork script
    to customize it for the new agent.

    Args:
        path: Destination path for the workspace
        agent_name: Name of the agent
        template_repo: Git URL of the template repository
        template_branch: Branch to clone
        fork_command: Command to run after cloning (use {path} and {name} placeholders)
        project_config: Optional ProjectConfig to merge with template config
        timeout: Timeout for git operations in seconds

    Returns:
        Path to the created workspace

    Raises:
        WorkspaceError: If creation fails
    """
    path = Path(path).expanduser().resolve()

    if path.exists():
        raise WorkspaceError(f"Destination path already exists: {path}")

    # Clone to temp directory first
    temp_dir = Path(tempfile.gettempdir()) / str(uuid.uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Clone template repository
        clone_cmd = ["git", "clone"]
        if template_branch:
            clone_cmd.extend(["--branch", template_branch])
        clone_cmd.extend([template_repo, str(temp_dir)])

        logger.info(f"Cloning template from {template_repo}")
        result = subprocess.run(
            clone_cmd, capture_output=True, check=False, timeout=timeout
        )
        if result.returncode != 0:
            raise WorkspaceError(f"Failed to clone template: {result.stderr.decode()}")

        # Update submodules
        logger.info("Updating submodules")
        result = subprocess.run(
            ["git", "submodule", "update", "--init", "--recursive"],
            capture_output=True,
            check=False,
            cwd=temp_dir,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise WorkspaceError(
                f"Failed to update submodules: {result.stderr.decode()}"
            )

        # Run fork command if provided
        if fork_command:
            # Format the command with path and name
            formatted_cmd = fork_command.format(path=str(path), name=agent_name)
            logger.info(f"Running fork command: {formatted_cmd}")

            result = subprocess.run(
                shlex.split(formatted_cmd),
                capture_output=True,
                check=False,
                cwd=temp_dir,
                timeout=120,
            )
            if result.returncode != 0:
                error_msg = result.stderr.decode() or result.stdout.decode()
                raise WorkspaceError(f"Fork command failed: {error_msg}")
        else:
            # No fork command - just move the temp dir to the destination
            shutil.move(str(temp_dir), str(path))

        # Merge project config with template config
        _merge_project_config(path, agent_name, project_config)

        return path

    except subprocess.TimeoutExpired as e:
        raise WorkspaceError(f"Operation timed out: {e}") from e
    finally:
        # Clean up temp directory if it still exists
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


def create_workspace_structure(path: Path, agent_name: str) -> Path:
    """
    Create a basic agent workspace from scratch.

    This is a fallback when template-based creation is not desired or fails.
    Creates the standard directory structure and configuration files.

    Args:
        path: Destination path for the workspace
        agent_name: Name of the agent

    Returns:
        Path to the created workspace
    """
    path = Path(path).expanduser().resolve()

    # Create main workspace directory
    path.mkdir(parents=True, exist_ok=True)

    # Create standard directories
    directories = [
        "journal",
        "tasks",
        "knowledge",
        "lessons",
        "people",
        "scripts/runs/autonomous",
    ]
    for subdir in directories:
        (path / subdir).mkdir(parents=True, exist_ok=True)

    # Generate run script
    generate_run_script(path, agent_name)

    # Write configuration
    write_agent_config(path, agent_name)

    # Create README
    _create_readme(path, agent_name)

    return path


def write_agent_config(
    path: Path,
    agent_name: str,
    project_config: ProjectConfig | None = None,
) -> Path:
    """
    Write gptme.toml configuration for an agent.

    Uses tomlkit for proper TOML formatting and preserves comments
    if merging with existing config.

    Args:
        path: Workspace path
        agent_name: Name of the agent
        project_config: Optional ProjectConfig to use (creates default if None)

    Returns:
        Path to the written config file
    """
    path = Path(path)
    config_path = path / "gptme.toml"

    # Get existing config or create new
    existing_config = get_project_config(path)

    if project_config and existing_config:
        # Merge configs
        final_config = existing_config.merge(project_config)
    elif project_config:
        final_config = project_config
    elif existing_config:
        final_config = existing_config
    else:
        # Create default config
        final_config = ProjectConfig(
            agent=AgentConfig(name=agent_name),
        )

    # Ensure agent name is set
    if not final_config.agent or not final_config.agent.name:
        final_config.agent = AgentConfig(name=agent_name)

    # Write using tomlkit for proper formatting
    with open(config_path, "w") as f:
        f.write(tomlkit.dumps(final_config.to_dict()))

    return config_path


def generate_run_script(path: Path, agent_name: str) -> Path:
    """
    Generate the autonomous run script for an agent.

    Args:
        path: Workspace path
        agent_name: Name of the agent

    Returns:
        Path to the generated script
    """
    path = Path(path)
    scripts_dir = path / "scripts" / "runs" / "autonomous"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    run_script = scripts_dir / "autonomous-run.sh"
    run_script.write_text(f"""#!/bin/bash
# Autonomous run script for {agent_name}
# This script is called by the service manager (systemd/launchd)

set -e
cd "$(dirname "$0")/../../.."

# Load environment if exists
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Load local environment overrides if exists
if [ -f .env.local ]; then
    set -a
    source .env.local
    set +a
fi

# Run gptme with autonomous prompt
exec gptme --non-interactive --workspace . \\
    "You are {agent_name}, running in autonomous mode. Check your tasks and make progress."
""")
    run_script.chmod(0o755)

    return run_script


def init_conversation(
    workspace: Path,
    model: str | None = None,
) -> str:
    """
    Initialize the first conversation for an agent.

    Creates a new conversation in the logs directory with the proper
    system prompt and context for the agent.

    Args:
        workspace: Path to the agent workspace
        model: Optional model to use for the conversation

    Returns:
        The conversation ID
    """
    workspace = Path(workspace).resolve()

    # Create a new conversation ID
    conversation_id = str(uuid.uuid4())
    logdir = get_logs_dir() / conversation_id

    # Create the log directory
    logdir.mkdir(parents=True)

    # Load or create chat config
    request_config = ChatConfig(workspace=workspace, agent=workspace)
    chat_config = ChatConfig.load_or_create(logdir, request_config).save()

    # Override model if specified
    if model:
        chat_config.model = model

    # Get system prompt with proper tools and context
    msgs = get_prompt(
        tools=[t for t in get_toolchain(chat_config.tools)],
        interactive=chat_config.interactive,
        tool_format=chat_config.tool_format or "markdown",
        model=chat_config.model,
        workspace=workspace,
        agent_path=chat_config.agent,
    )

    # Create and write the log
    log = LogManager.load(logdir=logdir, initial_msgs=msgs, create=True)
    log.write()

    return conversation_id


def _merge_project_config(
    path: Path,
    agent_name: str,
    project_config: ProjectConfig | None,
) -> None:
    """Merge project config with any existing config in the workspace."""
    existing_config = get_project_config(path)

    if not existing_config and not project_config:
        # No config exists, create default
        final_config = ProjectConfig(agent=AgentConfig(name=agent_name))
    elif existing_config and project_config:
        # Merge configs
        final_config = existing_config.merge(project_config)
    elif existing_config:
        final_config = existing_config
    else:
        final_config = project_config  # type: ignore

    # Ensure agent name is set
    if not final_config.agent or not final_config.agent.name:
        final_config.agent = AgentConfig(name=agent_name)

    # Write the config
    with open(path / "gptme.toml", "w") as f:
        f.write(tomlkit.dumps(final_config.to_dict()))


def _create_readme(path: Path, agent_name: str) -> Path:
    """Create a README.md for the workspace."""
    readme_path = path / "README.md"
    readme_path.write_text(f"""# {agent_name}

An autonomous AI agent built on [gptme](https://gptme.org).

## Quick Start

```sh
# Install services
gptme-agent install

# Check status
gptme-agent status

# View logs
gptme-agent logs --follow

# Manual run
gptme-agent run
```

## Structure

- `journal/` - Daily activity logs
- `tasks/` - Task tracking
- `knowledge/` - Documentation and learnings
- `lessons/` - Behavioral patterns and constraints
- `people/` - Contact profiles
- `scripts/` - Automation scripts

## Configuration

Edit `gptme.toml` to customize agent behavior:
- Agent name and personality
- Included context files
- Lesson directories
- Plugin configuration

## Resources

- [gptme documentation](https://gptme.org/docs/)
- [gptme-agent-template](https://github.com/gptme/gptme-agent-template)
""")
    return readme_path
