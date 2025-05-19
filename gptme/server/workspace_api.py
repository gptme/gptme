"""
Workspace API endpoints for browsing files in conversation workspaces.
"""

import logging
import mimetypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, TypedDict

import flask
from flask import request

from ..logmanager import LogManager

logger = logging.getLogger(__name__)

workspace_api = flask.Blueprint("workspace_api", __name__)


class FileType(TypedDict):
    """File metadata type."""

    name: str
    path: str
    type: Literal["file", "directory"]
    size: int
    modified: str
    mime_type: str | None


@dataclass
class WorkspaceFile:
    """Represents a file or directory in the workspace."""

    path: Path
    workspace: Path

    @property
    def is_dir(self) -> bool:
        return self.path.is_dir()

    @property
    def is_hidden(self) -> bool:
        """Check if file/directory is hidden."""
        return self.path.name.startswith(".")

    @property
    def relative_path(self) -> str:
        """Get path relative to workspace."""
        return str(self.path.relative_to(self.workspace))

    @property
    def mime_type(self) -> str | None:
        """Get MIME type of file."""
        if self.is_dir:
            return None
        return mimetypes.guess_type(self.path)[0]

    def to_dict(self) -> FileType:
        """Convert to dictionary representation."""
        stat = self.path.stat()
        return {
            "name": self.path.name,
            "path": self.relative_path,
            "type": "directory" if self.is_dir else "file",
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "mime_type": self.mime_type,
        }


def safe_workspace_path(workspace: Path, path: str | None = None) -> Path:
    """
    Safely resolve a path within a workspace.

    Args:
        workspace: Base workspace path
        path: Optional path relative to workspace

    Returns:
        Resolved absolute path, guaranteed to be within workspace

    Raises:
        ValueError: If path would escape workspace
    """
    workspace = workspace.resolve()
    if not path:
        return workspace

    # Resolve the full path
    full_path = (workspace / path).resolve()

    # Check if path is within workspace
    if not full_path.is_relative_to(workspace):
        raise ValueError("Path escapes workspace")

    return full_path


def list_directory(
    path: Path, workspace: Path, show_hidden: bool = False
) -> list[FileType]:
    """
    List contents of a directory.

    Args:
        path: Directory path to list
        workspace: Base workspace path
        show_hidden: Whether to include hidden files

    Returns:
        List of file metadata
    """
    if not path.is_dir():
        raise ValueError("Path is not a directory")

    files = []
    for item in path.iterdir():
        wfile = WorkspaceFile(item, workspace)
        if not show_hidden and wfile.is_hidden:
            continue
        files.append(wfile.to_dict())

    return sorted(files, key=lambda f: (f["type"] == "file", f["name"].lower()))


@workspace_api.route("/api/v2/conversations/<string:conversation_id>/workspace")
@workspace_api.route(
    "/api/v2/conversations/<string:conversation_id>/workspace/<path:subpath>"
)
def browse_workspace(conversation_id: str, subpath: str | None = None):
    """
    List contents of a conversation's workspace directory.

    Args:
        conversation_id: ID of the conversation
        subpath: Optional path within workspace
    """
    try:
        # Load the conversation to get its workspace
        manager = LogManager.load(conversation_id, lock=False)
        workspace = manager.workspace

        if not workspace.is_dir():
            return flask.jsonify({"error": "Workspace not found"}), 404

        path = safe_workspace_path(workspace, subpath)
        show_hidden = request.args.get("show_hidden", "").lower() == "true"

        if path.is_file():
            # Return single file metadata
            return flask.jsonify(WorkspaceFile(path, workspace).to_dict())
        else:
            # Return directory listing
            return flask.jsonify(list_directory(path, workspace, show_hidden))

    except ValueError as e:
        return flask.jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("Error browsing workspace")
        return flask.jsonify({"error": str(e)}), 500


@workspace_api.route(
    "/api/v2/conversations/<string:conversation_id>/workspace/<path:filepath>/preview"
)
def preview_file(conversation_id: str, filepath: str):
    """
    Get a preview of a file in the conversation's workspace.

    Currently supports:
    - Text files (returned as-is)
    - Images (returned as-is)
    - Binary files (returns metadata only)

    Args:
        conversation_id: ID of the conversation
        filepath: Path to file within workspace
    """
    try:
        # Load the conversation to get its workspace
        manager = LogManager.load(conversation_id, lock=False)
        workspace = manager.workspace

        if not workspace.is_dir():
            return flask.jsonify({"error": "Workspace not found"}), 404

        path = safe_workspace_path(workspace, filepath)
        if not path.is_file():
            return flask.jsonify({"error": "File not found"}), 404

        wfile = WorkspaceFile(path, workspace)
        mime_type = wfile.mime_type

        # Handle different file types
        if mime_type and mime_type.startswith("text/"):
            # Text files
            with open(path) as f:
                content = f.read()
            return flask.jsonify({"type": "text", "content": content})
        elif mime_type and mime_type.startswith("image/"):
            # Images
            return flask.send_file(path, mimetype=mime_type)
        else:
            # Binary files - just return metadata
            return flask.jsonify({"type": "binary", "metadata": wfile.to_dict()})

    except ValueError as e:
        return flask.jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("Error previewing file")
        return flask.jsonify({"error": str(e)}), 500
