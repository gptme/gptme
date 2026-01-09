"""
Tasks API endpoints for managing parallel workflows and task orchestration.

This API provides lightweight task management by leveraging the existing conversation
infrastructure. Tasks are metadata containers that reference one or more conversations,
with workspace and git information derived from the active conversation.
"""

import json
import logging
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import flask
from pydantic import BaseModel, Field

from ..config import ChatConfig
from ..dirs import get_logs_dir
from ..logmanager import LogManager
from ..message import Message
from ..prompts import get_prompt
from ..tools import get_toolchain
from .auth import require_auth
from .openapi_docs import ErrorResponse, StatusResponse, api_doc_simple

logger = logging.getLogger(__name__)

tasks_api = flask.Blueprint("tasks_api", __name__)

# Type definitions
TaskStatus = Literal["pending", "active", "completed", "failed"]
TargetType = Literal["stdout", "pr", "email", "tweet"]


@dataclass
class Task:
    """Task metadata container."""

    id: str
    content: str
    created_at: str
    status: TaskStatus
    target_type: TargetType
    target_repo: str | None = None
    conversation_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    archived: bool = False


def _find_git_workspace(task: Task, manager: LogManager) -> Path:
    """Find the git workspace directory for a task.

    Args:
        task: The task to find git workspace for
        manager: The log manager with workspace information

    Returns:
        Path to the git workspace directory
    """
    git_workspace = manager.workspace

    # If this is a cloned repo, the git repo might be in a subdirectory
    if task.target_repo and "/" in task.target_repo:
        repo_name = task.target_repo.split("/")[-1]
        potential_repo_path = manager.workspace / repo_name
        if potential_repo_path.exists() and (potential_repo_path / ".git").exists():
            git_workspace = potential_repo_path

    return git_workspace


# Pydantic models for OpenAPI
class TaskCreateRequest(BaseModel):
    """Request to create a new task."""

    content: str = Field(..., description="Task description or content")
    target_type: TargetType = Field("stdout", description="Target type for task output")
    target_repo: str | None = Field(
        None, description="Target repository (for PR tasks)"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional task metadata"
    )


class TaskUpdateRequest(BaseModel):
    """Request to update a task."""

    content: str | None = Field(None, description="Updated task content")
    target_type: TargetType | None = Field(None, description="Updated target type")
    target_repo: str | None = Field(None, description="Updated target repository")
    metadata: dict[str, Any] | None = Field(None, description="Updated metadata")


class ConversationInfo(BaseModel):
    """Conversation information."""

    id: str = Field(..., description="Conversation ID")
    name: str = Field(..., description="Conversation name")
    message_count: int = Field(..., description="Number of messages in conversation")


class GitInfo(BaseModel):
    """Git repository information."""

    branch: str | None = Field(None, description="Current git branch")
    clean: bool | None = Field(None, description="Whether working directory is clean")
    files: list[str] = Field(default_factory=list, description="Modified files")
    remote_url: str | None = Field(None, description="Remote repository URL")
    pr_url: str | None = Field(None, description="Pull request URL")
    pr_status: str | None = Field(None, description="Pull request status")
    pr_merged: bool | None = Field(None, description="Whether PR is merged")
    error: str | None = Field(None, description="Error message if git info unavailable")


class TaskResponse(BaseModel):
    """Complete task information."""

    id: str = Field(..., description="Task ID")
    content: str = Field(..., description="Task content")
    created_at: str = Field(..., description="Task creation timestamp")
    status: TaskStatus = Field(..., description="Task status")
    target_type: TargetType = Field(..., description="Target type")
    target_repo: str | None = Field(None, description="Target repository")
    conversation_ids: list[str] = Field(
        default_factory=list, description="Associated conversation IDs"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Task metadata")
    archived: bool = Field(False, description="Whether task is archived")
    workspace: str | None = Field(None, description="Task workspace path")
    conversation: ConversationInfo | None = Field(
        None, description="Active conversation info"
    )
    git: GitInfo | None = Field(None, description="Git repository information")
    error: str | None = Field(None, description="Error message if any")


class TaskListResponse(BaseModel):
    """Response containing a list of tasks."""

    tasks: list[TaskResponse] = Field(..., description="List of tasks")


def get_tasks_dir() -> Path:
    """Get the tasks storage directory."""
    return get_logs_dir().parent / "tasks"


def load_task(task_id: str) -> Task | None:
    """Load a task from storage."""
    tasks_dir = get_tasks_dir()
    task_file = tasks_dir / f"{task_id}.json"

    if not task_file.exists():
        return None

    try:
        with open(task_file) as f:
            data = json.load(f)
        return Task(**data)
    except Exception as e:
        logger.error(f"Error loading task {task_id}: {e}")
        return None


def save_task(task: Task) -> None:
    """Save a task to storage."""
    tasks_dir = get_tasks_dir()
    tasks_dir.mkdir(parents=True, exist_ok=True)

    task_file = tasks_dir / f"{task.id}.json"

    try:
        with open(task_file, "w") as f:
            json.dump(asdict(task), f, indent=2)
    except Exception as e:
        logger.error(f"Error saving task {task.id}: {e}")
        raise


def list_tasks() -> list[Task]:
    """List all tasks from storage."""
    tasks_dir = get_tasks_dir()

    if not tasks_dir.exists():
        return []

    tasks = []
    for task_file in tasks_dir.glob("*.json"):
        task_id = task_file.stem
        task = load_task(task_id)
        if task:
            tasks.append(task)

    return sorted(tasks, key=lambda t: t.created_at, reverse=True)


def derive_task_status(task: Task) -> TaskStatus:
    """Derive task status from git/PR state and process information."""
    if not task.conversation_ids:
        return "pending"

    try:
        # Get the latest conversation
        latest_conv_id = task.conversation_ids[-1]

        # Check if conversation exists
        logdir = get_logs_dir() / latest_conv_id
        if not logdir.exists():
            return "failed"

        # Load conversation without lock for status check
        manager = LogManager.load(latest_conv_id, lock=False)

        # Check if process is active by looking for lock file
        lock_file = logdir / ".lock"
        if lock_file.exists():
            try:
                with open(lock_file):
                    pass  # If we can read it without blocking, no active process
            except OSError:
                # If we can't read it, likely an active process
                return "active"

        # Try to get git status for more accurate status determination
        if manager.workspace and manager.workspace.exists():
            git_workspace = manager.workspace

            git_workspace = _find_git_workspace(task, manager)

            try:
                git_info = get_git_status(git_workspace)
                if not git_info.get("error"):
                    return determine_task_status(task, git_info)
            except Exception as e:
                logger.debug(f"Could not get git status for task {task.id}: {e}")

        # Default to pending if no git status available
        return "pending"

    except Exception as e:
        logger.error(f"Error deriving status for task {task.id}: {e}")
        return "failed"


def get_task_info(task: Task) -> dict[str, Any]:
    """Get comprehensive task information with derived data."""
    current_status = task.status

    # Only derive status if we can potentially progress
    derived_status = derive_task_status(task)
    if is_status_progression(current_status, derived_status):
        task.status = derived_status

    result = asdict(task)

    if task.conversation_ids:
        try:
            # Get active conversation info
            active_conv_id = task.conversation_ids[-1]
            manager = LogManager.load(active_conv_id, lock=False)

            # Add workspace info
            result["workspace"] = str(manager.workspace)

            # Add conversation info
            result["conversation"] = {
                "id": active_conv_id,
                "name": manager.name,
                "message_count": len(manager.log.messages),
            }

            # Try to get git info from the actual git repository
            git_workspace = manager.workspace
            if manager.workspace.exists():
                git_workspace = _find_git_workspace(task, manager)

                try:
                    git_info = get_git_status(git_workspace)
                    result["git"] = git_info
                    logger.debug(f"Git info for task {task.id}: {git_info}")

                    # Update task status based on git info only if it's a valid progression
                    git_based_status = determine_task_status(task, git_info)
                    if git_based_status != task.status and is_status_progression(
                        task.status, git_based_status
                    ):
                        logger.debug(
                            f"Updating task {task.id} status from {task.status} to {git_based_status}"
                        )
                        task.status = git_based_status
                        result["status"] = git_based_status

                except Exception as e:
                    logger.error(f"Error getting git status for {task.id}: {e}")
                    result["git"] = {"error": f"Could not get git status: {str(e)}"}
            else:
                result["git"] = {"error": "Workspace does not exist"}

        except Exception as e:
            logger.error(f"Error getting task info for {task.id}: {e}")
            result["error"] = str(e)

    return result


def is_status_progression(current: TaskStatus, new: TaskStatus) -> bool:
    """Check if status transition is a valid progression."""
    # Define valid status progressions
    progressions = {
        "pending": ["active", "completed", "failed"],
        "active": ["pending", "completed", "failed"],
        "completed": [],  # No regression from completed
        "failed": ["pending"],  # Allow retry from failed
    }

    return new in progressions.get(current, [])


def determine_task_status(
    task: Task, git_info: dict[str, Any] | None = None
) -> TaskStatus:
    """Determine task status based on git info and other conditions."""
    try:
        # If we have git info with PR status, use that
        if git_info and git_info.get("pr_status"):
            pr_status = git_info.get("pr_status")
            pr_merged = git_info.get("pr_merged", False)

            if pr_merged or pr_status == "MERGED":
                return "completed"
            elif pr_status == "CLOSED":
                return "failed"  # Closed without merge
            elif pr_status == "OPEN":
                # PR is open, waiting for review
                return "pending"

        # Check if task has any associated commits
        if git_info:
            recent_commits = git_info.get("recent_commits", [])
            files_changed = git_info.get("diff_stats", {}).get("files_changed", 0)

            if recent_commits or files_changed > 0:
                # Work has been done
                return "pending"

        # Default to pending for new tasks
        return "pending"

    except Exception as e:
        logger.error(f"Error determining task status: {e}")
        return task.status  # Return current status if error


def get_git_status(workspace_path: Path) -> dict[str, Any]:
    """Get git status for a workspace."""
    try:
        logger.debug(f"Getting git status for workspace: {workspace_path}")

        # Check if workspace exists
        if not workspace_path.exists():
            logger.warning(f"Workspace path does not exist: {workspace_path}")
            return {"error": f"Workspace path does not exist: {workspace_path}"}

        # Check if it's a git repository
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            logger.warning(f"Not a git repository: {workspace_path}")
            return {"error": f"Not a git repository: {workspace_path}"}

        # Get current branch
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            check=False,
        )

        current_branch = (
            branch_result.stdout.strip() if branch_result.returncode == 0 else "unknown"
        )
        logger.debug(f"Current branch for {workspace_path}: {current_branch}")

        # Get status
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            check=False,
        )

        files = []
        if status_result.returncode == 0 and status_result.stdout.strip():
            files = [line.strip() for line in status_result.stdout.strip().split("\n")]

        # Determine upstream branch (try common default branches)
        upstream_branch = None
        for candidate in ["origin/main", "origin/master", "origin/develop"]:
            check_result = subprocess.run(
                ["git", "rev-parse", "--verify", candidate],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                check=False,
            )
            if check_result.returncode == 0:
                upstream_branch = candidate
                break

        # Get diff statistics (lines added/removed compared to upstream)
        diff_stats = {"files_changed": 0, "lines_added": 0, "lines_removed": 0}
        if upstream_branch:
            diff_stat_result = subprocess.run(
                ["git", "diff", "--stat", upstream_branch, "HEAD"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                check=False,
            )

            # Parse diff stats
            if diff_stat_result.returncode == 0 and diff_stat_result.stdout.strip():
                diff_output = diff_stat_result.stdout.strip()
                lines = diff_output.split("\n")

                # Parse the summary line (e.g., "3 files changed, 45 insertions(+), 2 deletions(-)")
                if lines:
                    summary_line = lines[-1]
                    if "changed" in summary_line:
                        import re

                        # Extract numbers from summary
                        files_match = re.search(r"(\d+) files? changed", summary_line)
                        added_match = re.search(
                            r"(\d+) insertions?\(\+\)", summary_line
                        )
                        removed_match = re.search(
                            r"(\d+) deletions?\(-\)", summary_line
                        )

                        if files_match:
                            diff_stats["files_changed"] = int(files_match.group(1))
                        if added_match:
                            diff_stats["lines_added"] = int(added_match.group(1))
                        if removed_match:
                            diff_stats["lines_removed"] = int(removed_match.group(1))

        # Get commits that are on current branch but not on upstream
        commits = []
        if upstream_branch:
            log_result = subprocess.run(
                ["git", "log", "--oneline", f"{upstream_branch}..HEAD"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                check=False,
            )

            if log_result.returncode == 0 and log_result.stdout.strip():
                commits = log_result.stdout.strip().split("\n")
        else:
            # Fallback: show recent commits if no upstream found
            log_result = subprocess.run(
                ["git", "log", "--oneline", "-5"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                check=False,
            )

            if log_result.returncode == 0 and log_result.stdout.strip():
                commits = log_result.stdout.strip().split("\n")

        # Try to get remote info for PR detection
        remote_result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            check=False,
        )

        remote_url = (
            remote_result.stdout.strip() if remote_result.returncode == 0 else None
        )

        # Try to get PR info using gh CLI
        pr_url = None
        pr_status = None
        pr_merged = False
        try:
            pr_result = subprocess.run(
                [
                    "gh",
                    "pr",
                    "view",
                    "--json",
                    "url,state,mergedAt",
                    "-q",
                    '.url + "|" + .state + "|" + (.mergedAt != null | tostring)',
                ],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                check=False,
            )
            if pr_result.returncode == 0 and pr_result.stdout.strip():
                parts = pr_result.stdout.strip().split("|")
                if len(parts) >= 3:
                    pr_url = parts[0]
                    pr_status = parts[1]  # OPEN, MERGED, CLOSED
                    pr_merged = parts[2].lower() == "true"
                    logger.debug(
                        f"Found PR: {pr_url}, status: {pr_status}, merged: {pr_merged}"
                    )
        except Exception as e:
            logger.debug(f"Could not get PR info: {e}")

        git_info = {
            "branch": current_branch,
            "clean": len(files) == 0,
            "files": files,
            "remote_url": remote_url,
            "pr_url": pr_url,
            "pr_status": pr_status,  # OPEN, MERGED, CLOSED
            "pr_merged": pr_merged,
            "diff_stats": diff_stats,
            "recent_commits": commits,
        }

        logger.debug(f"Git info for {workspace_path}: {git_info}")
        return git_info

    except Exception as e:
        logger.error(f"Error getting git status for {workspace_path}: {e}")
        return {"error": str(e)}


def create_task_conversation(task: Task) -> str:
    """Create a conversation for a task."""
    # Use simple incremental suffix instead of timestamp
    suffix = len(task.conversation_ids)
    conversation_id = f"{task.id}-{suffix}"
    logdir = get_logs_dir() / conversation_id

    # Create conversation directory (but not workspace subdirectory yet)
    logdir.mkdir(parents=True, exist_ok=True)

    # Always use task-level workspace (all conversations for this task will share it)
    workspace = setup_task_workspace(task.id, task.target_repo)

    chat_config = ChatConfig(
        name=task.content[:50] + "..." if len(task.content) > 50 else task.content,
        workspace=workspace,
        _logdir=logdir,
    )

    # Create initial system messages
    messages = get_prompt(
        tools=[t for t in get_toolchain(None)],
        interactive=True,
        tool_format="markdown",
        model=None,
        prompt="full",
        workspace=workspace,
        agent_path=None,
    )

    # Add task-specific messages
    task_prompt = f"Task: {task.content}"
    if task.target_type == "pr" and task.target_repo:
        task_prompt += f"\n\nTarget: Create a PR in {task.target_repo}"

    messages.append(Message("user", task_prompt))

    # Create and write conversation
    manager = LogManager(messages, logdir=logdir)
    manager.write()

    # Save chat config
    chat_config.save()

    return conversation_id


def setup_task_workspace(task_id: str, target_repo: str | None = None) -> Path:
    """Setup workspace for task. All conversations for this task will share this workspace."""
    # Use task-level workspace that all conversations for this task will share
    task_dir = get_tasks_dir() / task_id
    workspace = task_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    if target_repo and "/" in target_repo:
        # Validate target_repo format (owner/repo)
        import re

        if not re.match(r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$", target_repo):
            logger.error(f"Invalid target_repo format: {target_repo}")
            return workspace

        # Clone target repository into workspace
        repo_name = target_repo.split("/")[-1]
        repo_path = workspace / repo_name

        if not repo_path.exists():
            try:
                subprocess.run(
                    [
                        "git",
                        "clone",
                        f"https://github.com/{target_repo}.git",
                        str(repo_path),
                    ],
                    check=True,
                )
                logger.info(f"Cloned {target_repo} to {repo_path}")
                return repo_path
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to clone {target_repo}: {e}")
                # Fallback to empty workspace
                return workspace

        return repo_path
    else:
        # Return empty workspace
        return workspace


# API Endpoints
# -------------


@tasks_api.route("/api/v2/tasks")
@require_auth
@api_doc_simple(
    responses={200: TaskListResponse, 500: ErrorResponse},
    tags=["tasks"],
)
def api_tasks_list():
    """List all tasks.

    List all tasks with their cached status information.
    """
    try:
        tasks = list_tasks()

        # Return with stored status (no side effects in GET)
        tasks_info = [asdict(task) for task in tasks]
        return flask.jsonify(tasks_info)

    except Exception as e:
        logger.error(f"Error listing tasks: {e}")
        return flask.jsonify({"error": str(e)}), 500


@tasks_api.route("/api/v2/tasks", methods=["POST"])
@require_auth
@api_doc_simple(
    request_body=TaskCreateRequest,
    responses={201: TaskResponse, 400: ErrorResponse, 500: ErrorResponse},
    tags=["tasks"],
)
def api_tasks_create():
    """Create a new task.

    Create a new task with the specified content and configuration.
    A conversation will be automatically created for the task.
    """
    req_json = flask.request.json
    if not req_json:
        return flask.jsonify({"error": "No JSON data provided"}), 400

    if "content" not in req_json:
        return flask.jsonify({"error": "Missing required field: content"}), 400

    try:
        # Generate task ID
        task_id = f"task-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        # Create task
        task = Task(
            id=task_id,
            content=req_json["content"],
            created_at=datetime.now().isoformat(),
            status="pending",
            target_type=req_json.get("target_type", "stdout"),
            target_repo=req_json.get("target_repo"),
            conversation_ids=[],
            metadata=req_json.get("metadata", {}),
        )

        # Create initial conversation
        conversation_id = create_task_conversation(task)
        task.conversation_ids = [conversation_id]

        # Save task
        save_task(task)

        logger.info(f"Created task {task_id} with conversation {conversation_id}")

        return flask.jsonify(get_task_info(task)), 201

    except Exception as e:
        logger.error(f"Error creating task: {e}")
        return flask.jsonify({"error": str(e)}), 500


@tasks_api.route("/api/v2/tasks/<string:task_id>")
@require_auth
@api_doc_simple(
    responses={200: TaskResponse, 404: ErrorResponse, 500: ErrorResponse},
    tags=["tasks"],
)
def api_tasks_get(task_id: str):
    """Get detailed task information.

    Retrieve comprehensive information about a task including git status,
    conversation details, and derived status information.
    """
    task = load_task(task_id)
    if not task:
        return flask.jsonify({"error": f"Task not found: {task_id}"}), 404

    try:
        original_status = task.status
        task_info = get_task_info(task)

        # Persist updated status if it changed
        if task.status != original_status:
            save_task(task)
            logger.info(
                f"Updated task {task_id} status from {original_status} to {task.status}"
            )

        return flask.jsonify(task_info)
    except Exception as e:
        logger.error(f"Error getting task {task_id}: {e}")
        return flask.jsonify({"error": str(e)}), 500


@tasks_api.route("/api/v2/tasks/<string:task_id>", methods=["PUT"])
@require_auth
@api_doc_simple(
    request_body=TaskUpdateRequest,
    responses={
        200: TaskResponse,
        400: ErrorResponse,
        404: ErrorResponse,
        500: ErrorResponse,
    },
    tags=["tasks"],
)
def api_tasks_update(task_id: str):
    """Update task metadata.

    Update task content, target type, target repository, or metadata.
    Only provided fields will be updated.
    """
    task = load_task(task_id)
    if not task:
        return flask.jsonify({"error": f"Task not found: {task_id}"}), 404

    req_json = flask.request.json
    if not req_json:
        return flask.jsonify({"error": "No JSON data provided"}), 400

    try:
        # Update allowed fields
        if "content" in req_json:
            task.content = req_json["content"]
        if "target_type" in req_json:
            task.target_type = req_json["target_type"]
        if "target_repo" in req_json:
            task.target_repo = req_json["target_repo"]
        if "metadata" in req_json:
            task.metadata.update(req_json["metadata"])

        save_task(task)

        return flask.jsonify(get_task_info(task))

    except Exception as e:
        logger.error(f"Error updating task {task_id}: {e}")
        return flask.jsonify({"error": str(e)}), 500


@tasks_api.route("/api/v2/tasks/<string:task_id>/archive", methods=["POST"])
@require_auth
@api_doc_simple(
    responses={
        200: StatusResponse,
        400: ErrorResponse,
        404: ErrorResponse,
        500: ErrorResponse,
    },
    tags=["tasks"],
)
def api_tasks_archive(task_id: str):
    """Archive a task.

    Archive a task to hide it from active view while preserving all data.
    Archived tasks can be restored using the unarchive endpoint.
    """
    task = load_task(task_id)
    if not task:
        return flask.jsonify({"error": f"Task not found: {task_id}"}), 404

    try:
        if task.archived:
            return flask.jsonify({"error": "Task is already archived"}), 400

        task.archived = True
        save_task(task)

        logger.info(f"Archived task {task_id}")
        return flask.jsonify({"status": "ok", "message": f"Task {task_id} archived"})

    except Exception as e:
        logger.error(f"Error archiving task {task_id}: {e}")
        return flask.jsonify({"error": str(e)}), 500


@tasks_api.route("/api/v2/tasks/<string:task_id>/unarchive", methods=["POST"])
@require_auth
@api_doc_simple(
    responses={
        200: StatusResponse,
        400: ErrorResponse,
        404: ErrorResponse,
        500: ErrorResponse,
    },
    tags=["tasks"],
)
def api_tasks_unarchive(task_id: str):
    """Unarchive a task.

    Restore an archived task to active view, making it visible
    in the standard task listings again.
    """
    task = load_task(task_id)
    if not task:
        return flask.jsonify({"error": f"Task not found: {task_id}"}), 404

    try:
        if not task.archived:
            return flask.jsonify({"error": "Task is not archived"}), 400

        task.archived = False
        save_task(task)

        logger.info(f"Unarchived task {task_id}")
        return flask.jsonify({"status": "ok", "message": f"Task {task_id} unarchived"})

    except Exception as e:
        logger.error(f"Error unarchiving task {task_id}: {e}")
        return flask.jsonify({"error": str(e)}), 500
