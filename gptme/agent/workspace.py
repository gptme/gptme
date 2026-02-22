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
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import tomlkit

from gptme.config import (
    AgentConfig,
    ChatConfig,
    ProjectConfig,
    get_project_config,
)
from gptme.prompts import get_prompt

from ..dirs import get_logs_dir
from ..logmanager import LogManager
from ..tools import get_toolchain

logger = logging.getLogger(__name__)

# Default template repository
DEFAULT_TEMPLATE_REPO = "https://github.com/gptme/gptme-agent-template"
DEFAULT_TEMPLATE_BRANCH = "master"


class WorkspaceError(Exception):
    """Error during workspace creation or management."""


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
            # No fork command - move temp dir and create clean git history
            # (without this, the full template repo history would be included)
            shutil.move(str(temp_dir), str(path))
            _replace_template_strings(path, agent_name)
            _reset_git_history(path, agent_name)

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
        tools=list(get_toolchain(chat_config.tools)),
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


def _replace_template_strings(path: Path, agent_name: str) -> None:
    """Replace template placeholder strings with the agent name in all text files.

    When creating a workspace without a fork command, template files contain
    placeholder strings like "gptme-agent-template" and "gptme-agent" that need
    to be replaced with the actual agent name. This performs the same replacements
    that fork.sh would do.

    Respects .gitignore when the workspace has a git repo; falls back to hardcoded
    skip dirs (__pycache__, node_modules, .venv, .git) otherwise.
    """
    template_name = "gptme-agent"

    # Try to use git ls-files to enumerate files (respects .gitignore)
    files: list[Path] = []
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=path,
            capture_output=True,
            check=True,
        )
        files = [path / f for f in result.stdout.decode().split("\0") if f]
        logger.debug(
            "Using git ls-files for template replacement (respects .gitignore)"
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fall back to manual traversal if git is not available or fails
        skip_dirs = {"__pycache__", "node_modules", ".venv", ".git"}
        files = [
            filepath
            for filepath in path.rglob("*")
            if filepath.is_file()
            and not any(part in skip_dirs for part in filepath.parts)
            and not filepath.name.startswith(".")
        ]
        logger.debug(
            "git not available, using manual file traversal for template replacement"
        )

    for filepath in files:
        if not filepath.is_file():
            continue
        try:
            content = filepath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue
        original = content
        # Replace "gptme-agent-template" first (more specific, avoids double-replace)
        content = content.replace(f"{template_name}-template", agent_name)
        # Then replace "gptme-agent"
        content = content.replace(template_name, agent_name)
        # Strip template comment blocks
        content = re.sub(
            r"<!--template-->.*?<!--/template-->", "", content, flags=re.DOTALL
        )
        if content != original:
            filepath.write_text(content, encoding="utf-8")
            logger.debug(f"Replaced template strings in {filepath}")
    logger.info(f"Replaced template strings with '{agent_name}' in workspace")


def _reset_git_history(path: Path, agent_name: str) -> None:
    """Reset git history to a single clean initial commit.

    When creating a workspace without a fork command, the cloned template
    repository includes its full git history. This function replaces that
    with a single clean initial commit for the new agent.
    """
    git_dir = path / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir)

    # Use -c flags to bypass global git hooks and set identity for CI environments
    # where user.email/user.name may not be configured globally
    git_no_hooks = [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "user.email=agent@gptme.org",
        "-c",
        "user.name=gptme-agent",
    ]
    subprocess.run(
        [*git_no_hooks, "init"], cwd=str(path), capture_output=True, check=True
    )
    subprocess.run(
        [*git_no_hooks, "add", "."], cwd=str(path), capture_output=True, check=True
    )
    subprocess.run(
        [
            *git_no_hooks,
            "commit",
            "-m",
            f"feat: initialize {agent_name} agent workspace",
        ],
        cwd=str(path),
        capture_output=True,
        check=True,
    )
    logger.info(f"Created clean git history for {agent_name}")


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
        final_config = project_config  # type: ignore[assignment]

    # Ensure agent name is set
    if not final_config.agent or not final_config.agent.name:
        final_config.agent = AgentConfig(name=agent_name)

    # Write the config
    with open(path / "gptme.toml", "w") as f:
        f.write(tomlkit.dumps(final_config.to_dict()))


@dataclass
class DetectedWorkspace:
    """Represents a detected agent workspace."""

    path: Path
    name: str
    installed: bool = False

    @property
    def has_run_script(self) -> bool:
        """Check if workspace has autonomous run script."""
        return (
            self.path / "scripts" / "runs" / "autonomous" / "autonomous-run.sh"
        ).exists()


def detect_workspaces(
    search_paths: list[Path] | None = None,
    installed_agents: list[str] | None = None,
) -> list[DetectedWorkspace]:
    """
    Detect agent workspaces by scanning for gptme.toml with [agent] section.

    Args:
        search_paths: Paths to search. If None, uses default locations:
            - Current directory
            - ~/Programming/*
            - ~/projects/*
            - ~/.local/share/gptme/agents/*
        installed_agents: List of already installed agent names (to mark as installed)

    Returns:
        List of detected workspaces
    """
    if search_paths is None:
        home = Path.home()
        search_paths = [
            Path.cwd(),
            home / "Programming",
            home / "projects",
            home / ".local" / "share" / "gptme" / "agents",
        ]

    installed_set = set(installed_agents or [])
    workspaces: dict[str, DetectedWorkspace] = {}

    for base_path in search_paths:
        if not base_path.exists():
            continue

        try:
            # Check if base_path itself is a workspace
            config = get_project_config(base_path, quiet=True)
            if config and config.agent and config.agent.name:
                name = config.agent.name
                if name not in workspaces:
                    workspaces[name] = DetectedWorkspace(
                        path=base_path.resolve(),
                        name=name,
                        installed=name in installed_set,
                    )

            # Search subdirectories (depth 1)
            if base_path.is_dir():
                for subdir in base_path.iterdir():
                    if not subdir.is_dir():
                        continue
                    try:
                        config = get_project_config(subdir, quiet=True)
                        if config and config.agent and config.agent.name:
                            name = config.agent.name
                            if name not in workspaces:
                                workspaces[name] = DetectedWorkspace(
                                    path=subdir.resolve(),
                                    name=name,
                                    installed=name in installed_set,
                                )
                    except (PermissionError, OSError):
                        # Skip directories we can't read
                        continue
        except (PermissionError, OSError):
            # Skip paths we can't access
            continue

    return list(workspaces.values())


def is_agent_workspace(path: Path, quiet: bool = True) -> bool:
    """
    Check if a directory is a valid agent workspace.

    A valid workspace has:
    - gptme.toml with [agent] section containing name

    Args:
        path: Path to check
        quiet: Suppress log messages (default True for quick checks)
    """
    config = get_project_config(path, quiet=quiet)
    return config is not None and config.agent is not None and bool(config.agent.name)


def get_workspace_name(path: Path, quiet: bool = True) -> str | None:
    """
    Get the agent name from a workspace, if valid.

    Args:
        path: Path to check
        quiet: Suppress log messages (default True for quick checks)

    Returns:
        Agent name or None if not a valid agent workspace
    """
    config = get_project_config(path, quiet=quiet)
    if config and config.agent and config.agent.name:
        return config.agent.name
    return None


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
