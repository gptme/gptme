"""
Workspace API endpoints for browsing files in conversation workspaces.
"""

import logging
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypedDict

import flask
from flask import request
from pydantic import BaseModel, Field

from ..logmanager import LogManager
from .api_v2_common import _validate_conversation_id
from .auth import require_auth
from .openapi_docs import ErrorResponse, api_doc_simple

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


# Pydantic models for OpenAPI
class FileMetadata(BaseModel):
    """File metadata for OpenAPI documentation."""

    name: str = Field(..., description="File or directory name")
    path: str = Field(..., description="Path relative to workspace")
    type: Literal["file", "directory"] = Field(..., description="File type")
    size: int = Field(..., description="File size in bytes")
    modified: str = Field(..., description="Last modified timestamp (ISO format)")
    mime_type: str | None = Field(None, description="MIME type (files only)")


class FileListResponse(BaseModel):
    """Response containing a list of files."""

    files: list[FileMetadata] = Field(..., description="List of files and directories")


class UploadedFileMetadata(BaseModel):
    """Metadata for an uploaded file."""

    name: str = Field(..., description="File name")
    path: str = Field(
        ..., description="Absolute filesystem path for use in message files"
    )
    type: Literal["file", "directory"] = Field(..., description="File type")
    size: int = Field(..., description="File size in bytes")
    modified: str = Field(..., description="Last modified timestamp (ISO format)")
    mime_type: str | None = Field(None, description="MIME type (files only)")


class UploadFileResponse(BaseModel):
    """Response for file upload."""

    files: list[UploadedFileMetadata] = Field(..., description="List of uploaded files")


class FilePreviewResponse(BaseModel):
    """Response for file preview."""

    type: Literal["text", "binary"] = Field(..., description="Preview type")
    content: str | None = Field(None, description="File content (text files only)")
    metadata: FileMetadata | None = Field(
        None, description="File metadata (binary files)"
    )


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

    @property
    def is_text(self) -> bool:
        """Check if file is a text file."""
        if self.mime_type and (
            self.mime_type.startswith("text/") or self.mime_type == "application/json"
        ):
            return True

        # Check if file is a text file by reading the first few bytes
        try:
            with open(self.path, "rb") as f:
                content = f.read(1024)
                content.decode("utf-8")
            return True
        except (UnicodeDecodeError, OSError):
            return False

    def to_dict(self) -> FileType:
        """Convert to dictionary representation."""
        stat = self.path.stat()
        return {
            "name": self.path.name,
            "path": self.relative_path,
            "type": "directory" if self.is_dir else "file",
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat(),
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


def allocate_attachment_path(
    attachments_dir: Path, filename: str, reserved_names: set[str] | None = None
) -> Path:
    """Allocate a non-conflicting path inside the attachments directory."""
    reserved_names = reserved_names or set()
    candidate = filename
    candidate_path = attachments_dir / candidate
    if candidate not in reserved_names and not candidate_path.exists():
        return candidate_path

    path = Path(filename)
    suffix = "".join(path.suffixes)
    stem = path.name[: -len(suffix)] if suffix else path.name
    counter = 1
    while True:
        candidate = f"{stem}-{counter}{suffix}"
        candidate_path = attachments_dir / candidate
        if candidate not in reserved_names and not candidate_path.exists():
            return candidate_path
        counter += 1


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
@require_auth
@api_doc_simple(
    responses={
        200: FileMetadata,
        400: ErrorResponse,
        404: ErrorResponse,
        500: ErrorResponse,
    },
    parameters=[
        {
            "name": "show_hidden",
            "in": "query",
            "schema": {"type": "boolean", "default": False},
            "description": "Whether to include hidden files and directories",
        },
        {
            "name": "root",
            "in": "query",
            "schema": {
                "type": "string",
                "enum": ["workspace", "attachments"],
                "default": "workspace",
            },
            "description": "Root directory to browse: 'workspace' (default) or 'attachments' (uploaded files)",
        },
    ],
    tags=["workspace"],
)
def browse_workspace(conversation_id: str, subpath: str | None = None):
    """Browse workspace directory.

    List contents of a conversation's workspace directory.
    Returns file metadata for a single file or directory listing.
    Use ?root=attachments to browse uploaded attachment files instead.
    """
    if error := _validate_conversation_id(conversation_id):
        return error
    try:
        # Load the conversation to get its workspace
        manager = LogManager.load(conversation_id, lock=False)

        root_param = request.args.get("root", "workspace")
        if root_param not in ("workspace", "attachments"):
            return flask.jsonify(
                {"error": "root must be 'workspace' or 'attachments'"}
            ), 400
        if root_param == "attachments":
            workspace = manager.logdir / "attachments"
            if not workspace.exists():
                workspace.mkdir(parents=True, exist_ok=True)
        else:
            workspace = manager.workspace

        if not workspace.is_dir():
            return flask.jsonify({"error": "Workspace not found"}), 404

        path = safe_workspace_path(workspace, subpath)
        show_hidden = request.args.get("show_hidden", "").lower() == "true"

        if path.is_file():
            # Return single file metadata
            return flask.jsonify(WorkspaceFile(path, workspace).to_dict())
        # Return directory listing
        return flask.jsonify(list_directory(path, workspace, show_hidden))

    except ValueError as e:
        return flask.jsonify({"error": str(e)}), 400
    except FileNotFoundError:
        return flask.jsonify({"error": "Conversation not found"}), 404
    except Exception as e:
        logger.exception("Error browsing workspace")
        return flask.jsonify({"error": str(e)}), 500


@workspace_api.route(
    "/api/v2/conversations/<string:conversation_id>/workspace/upload",
    methods=["POST"],
)
@require_auth
@api_doc_simple(
    responses={
        200: UploadFileResponse,
        400: ErrorResponse,
        404: ErrorResponse,
        413: ErrorResponse,
        500: ErrorResponse,
    },
    tags=["workspace"],
)
def upload_files(conversation_id: str):
    """Upload files to conversation attachments.

    Upload one or more files to a conversation's attachments directory
    (<logdir>/attachments/). Accepts multipart/form-data with file fields.
    Uploaded files are intended for the agent to read as context; the agent can
    move them into the workspace if it needs to modify them.
    Returns absolute file paths so they can be included directly in message files.
    """
    if error := _validate_conversation_id(conversation_id):
        return error
    try:
        manager = LogManager.load(conversation_id, lock=False)
        attachments_dir = manager.logdir / "attachments"

        if not request.files:
            return flask.jsonify({"error": "No files provided"}), 400

        # Size limit: 50MB per file
        max_size = 50 * 1024 * 1024

        # Collect files from all form field names (MultiDict may have duplicates)
        all_files = []
        for key in request.files:
            all_files.extend(request.files.getlist(key))

        # First pass: validate all files before writing any (prevents partial-upload state)
        validated: list[tuple[Path, bytes]] = []
        reserved_names: set[str] = set()
        for file in all_files:
            if not file.filename:
                continue

            # Sanitize filename (prevent path traversal via filename)
            filename = Path(file.filename).name
            if not filename or filename.startswith("."):
                continue

            # Check file size by reading into memory
            content = file.read()
            if len(content) > max_size:
                return (
                    flask.jsonify(
                        {
                            "error": f"File '{filename}' exceeds 50MB limit "
                            f"({len(content) / 1024 / 1024:.1f}MB)"
                        }
                    ),
                    413,
                )
            file_path = allocate_attachment_path(
                attachments_dir, filename, reserved_names
            )
            reserved_names.add(file_path.name)
            validated.append((file_path, content))

        if not validated:
            return flask.jsonify({"error": "No valid files uploaded"}), 400

        # Second pass: write all files (only reached if all files passed validation)
        attachments_dir.mkdir(parents=True, exist_ok=True)
        uploaded: list[FileType] = []
        for file_path, content in validated:
            file_path.write_bytes(content)
            stat = file_path.stat()
            uploaded.append(
                {
                    "name": file_path.name,
                    "path": str(
                        file_path.relative_to(manager.logdir)
                    ),  # logdir-relative: "attachments/filename"
                    "type": "file",
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                    "mime_type": mimetypes.guess_type(file_path)[0],
                }
            )

        return flask.jsonify({"files": uploaded})

    except FileNotFoundError:
        return flask.jsonify({"error": "Conversation not found"}), 404
    except ValueError as e:
        return flask.jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("Error uploading files")
        return flask.jsonify({"error": str(e)}), 500


@workspace_api.route(
    "/api/v2/conversations/<string:conversation_id>/files/<path:filepath>"
)
@require_auth
@api_doc_simple(
    responses={
        200: None,  # raw file content
        400: ErrorResponse,
        404: ErrorResponse,
        500: ErrorResponse,
    },
    tags=["workspace"],
)
def serve_conversation_file(conversation_id: str, filepath: str):
    """Serve a file from a conversation.

    Serves files from the conversation's workspace or logdir (e.g. attachments/).
    Used by the webui to display images and linked files in messages.

    Looks up files relative to the conversation's logdir first, then workspace.
    """
    if error := _validate_conversation_id(conversation_id):
        return error
    try:
        manager = LogManager.load(conversation_id, lock=False)
        logdir = manager.logdir
        workspace = manager.workspace

        # Resolve path against logdir (covers workspace/ and attachments/)
        try:
            path = safe_workspace_path(logdir, filepath)
        except ValueError:
            return flask.jsonify({"error": "Path escapes conversation directory"}), 400

        if not path.exists():
            # Fallback: try workspace-relative (for backwards compat)
            try:
                path = safe_workspace_path(workspace, filepath)
            except ValueError:
                # filepath is valid within logdir but escapes workspace
                # (unusual config); fall through to 404
                pass

        if not path.is_file():
            return flask.jsonify({"error": "File not found"}), 404

        mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return flask.send_file(path, mimetype=mime_type)

    except FileNotFoundError:
        return flask.jsonify({"error": "Conversation not found"}), 404
    except ValueError as e:
        return flask.jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("Error serving conversation file")
        return flask.jsonify({"error": str(e)}), 500


@workspace_api.route(
    "/api/v2/conversations/<string:conversation_id>/workspace/<path:filepath>/preview"
)
@require_auth
@api_doc_simple(
    responses={
        200: FilePreviewResponse,
        400: ErrorResponse,
        404: ErrorResponse,
        500: ErrorResponse,
    },
    tags=["workspace"],
)
def preview_file(conversation_id: str, filepath: str):
    """Preview workspace file.

    Get a preview of a file in the conversation's workspace.

    Currently supports:
    - Text files: returned as JSON with content
    - Images: returned as binary data with appropriate MIME type
    - Binary files: returns metadata only
    """
    if error := _validate_conversation_id(conversation_id):
        return error
    try:
        # Load the conversation to get its workspace
        manager = LogManager.load(conversation_id, lock=False)
        root_param = flask.request.args.get("root", "workspace")
        if root_param not in ("workspace", "attachments"):
            return flask.jsonify(
                {"error": "root must be 'workspace' or 'attachments'"}
            ), 400
        if root_param == "attachments":
            workspace = manager.logdir / "attachments"
            workspace.mkdir(parents=True, exist_ok=True)
        else:
            workspace = manager.workspace

        if not workspace.is_dir():
            return flask.jsonify({"error": "Workspace not found"}), 404

        path = safe_workspace_path(workspace, filepath)
        if not path.is_file():
            return flask.jsonify({"error": "File not found"}), 404

        wfile = WorkspaceFile(path, workspace)
        mime_type = wfile.mime_type

        # Handle different file types
        if wfile.is_text:
            # Text files
            with open(path) as f:
                content = f.read()
            return flask.jsonify({"type": "text", "content": content})
        if mime_type and mime_type.startswith("image/"):
            # Images
            return flask.send_file(path, mimetype=mime_type)
        # Binary files - return only metadata
        return flask.jsonify({"type": "binary", "metadata": wfile.to_dict()})

    except ValueError as e:
        return flask.jsonify({"error": str(e)}), 400
    except FileNotFoundError:
        return flask.jsonify({"error": "Conversation not found"}), 404
    except Exception as e:
        logger.exception("Error previewing file")
        return flask.jsonify({"error": str(e)}), 500


@workspace_api.route(
    "/api/v2/conversations/<string:conversation_id>/workspace/<path:filepath>/download"
)
@require_auth
@api_doc_simple(
    responses={
        200: None,  # raw file content, not JSON
        400: ErrorResponse,
        404: ErrorResponse,
        500: ErrorResponse,
    },
    tags=["workspace"],
)
def download_file(conversation_id: str, filepath: str):
    """Download workspace file.

    Download raw file content from the conversation's workspace.
    Returns the file with appropriate Content-Type and Content-Disposition
    headers for direct download.
    """
    if error := _validate_conversation_id(conversation_id):
        return error
    try:
        manager = LogManager.load(conversation_id, lock=False)
        root_param = flask.request.args.get("root", "workspace")
        if root_param not in ("workspace", "attachments"):
            return flask.jsonify(
                {"error": "root must be 'workspace' or 'attachments'"}
            ), 400
        if root_param == "attachments":
            workspace = manager.logdir / "attachments"
            workspace.mkdir(parents=True, exist_ok=True)
        else:
            workspace = manager.workspace

        if not workspace.is_dir():
            return flask.jsonify({"error": "Workspace not found"}), 404

        path = safe_workspace_path(workspace, filepath)
        if not path.is_file():
            return flask.jsonify({"error": "File not found"}), 404

        return flask.send_file(
            path,
            mimetype=WorkspaceFile(path, workspace).mime_type
            or "application/octet-stream",
            as_attachment=True,
            download_name=path.name,
        )

    except ValueError as e:
        return flask.jsonify({"error": str(e)}), 400
    except FileNotFoundError:
        return flask.jsonify({"error": "Conversation not found"}), 404
    except Exception as e:
        logger.exception("Error downloading file")
        return flask.jsonify({"error": str(e)}), 500
