"""
Artifact registry API endpoints for conversation-scoped artifacts.

Phase 1 of the webui artifact surface (see ErikBjare/bob#830): expose a typed,
conversation-scoped list of artifacts computed on read from the existing
attachments directory. No new tool APIs or persisted manifest are required yet
-- richer structured emission from tools/plugins is a later phase.

The descriptor shape intentionally mirrors the design doc so the webui can
render artifacts and choose a preview renderer from typed data instead of
filename heuristics.
"""

import hashlib
import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import flask
from pydantic import BaseModel, Field

from ..logmanager import LogManager
from .api_v2_common import _validate_conversation_id
from .auth import require_auth
from .openapi_docs import ErrorResponse, api_doc_simple

logger = logging.getLogger(__name__)

artifacts_api = flask.Blueprint("artifacts_api", __name__)

# Artifact kinds the webui knows how to render. Anything unrecognized falls
# back to "binary" (downloadable but not previewable inline).
ArtifactKind = Literal[
    "image",
    "audio",
    "video",
    "html",
    "markdown",
    "pdf",
    "diff",
    "dataset",
    "webapp",
    "binary",
    "other",
]

# Coarse renderer hint, deliberately smaller than the kind enum.
PreviewType = Literal["image", "audio", "video", "text", "pdf", "none"]


class ArtifactSource(BaseModel):
    """Where an artifact's bytes come from."""

    type: Literal["attachment", "workspace", "external", "inline"] = Field(
        ..., description="Source category"
    )
    path: str | None = Field(
        None, description="Logdir-relative path (attachment/workspace sources)"
    )
    url: str | None = Field(None, description="External URL (external sources)")


class ArtifactProvenance(BaseModel):
    """Best-effort origin metadata for an artifact."""

    message_index: int | None = Field(
        None, description="Index of the first message referencing this artifact"
    )
    tool: str | None = Field(
        None, description="Tool that produced the artifact, if known"
    )


class ArtifactPreview(BaseModel):
    """Typed preview hint so the webui can pick a renderer without heuristics."""

    type: PreviewType = Field(..., description="Renderer hint")


class ArtifactAction(BaseModel):
    """A user-facing action available for an artifact."""

    type: str = Field(
        ..., description="Action identifier (download, open_workspace, ...)"
    )
    panel: str | None = Field(
        None, description="Target panel id for open_panel actions"
    )
    artifact_id: str | None = Field(
        None, description="Artifact id for open_panel actions"
    )


class Artifact(BaseModel):
    """A conversation-scoped artifact descriptor."""

    id: str = Field(..., description="Stable artifact id (derived from its source)")
    kind: ArtifactKind = Field(..., description="Artifact kind")
    title: str = Field(..., description="Human-readable title")
    source: ArtifactSource = Field(..., description="Where the artifact comes from")
    created_at: str = Field(..., description="Creation timestamp (ISO format)")
    size: int | None = Field(
        None, description="Size in bytes for file-backed artifacts"
    )
    mime_type: str | None = Field(None, description="MIME type if known")
    provenance: ArtifactProvenance = Field(
        ..., description="Best-effort origin metadata"
    )
    preview: ArtifactPreview = Field(..., description="Preview/renderer hint")
    actions: list[ArtifactAction] = Field(..., description="Available user actions")


class ArtifactListResponse(BaseModel):
    """Response containing the conversation's artifacts."""

    artifacts: list[Artifact] = Field(..., description="Artifact descriptors")


# Extensions that should be classified ahead of (or in absence of) a MIME type.
_EXTENSION_KINDS: dict[str, ArtifactKind] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".diff": "diff",
    ".patch": "diff",
    ".html": "html",
    ".htm": "html",
    ".csv": "dataset",
    ".tsv": "dataset",
    ".json": "dataset",
    ".parquet": "dataset",
}

_PREVIEW_FOR_KIND: dict[ArtifactKind, PreviewType] = {
    "image": "image",
    "audio": "audio",
    "video": "video",
    "pdf": "pdf",
    "markdown": "text",
    "html": "text",
    "diff": "text",
    "dataset": "text",
}


def classify_kind(path: Path, mime_type: str | None) -> ArtifactKind:
    """Classify an artifact kind from its extension and MIME type.

    Extension wins for the cases where MIME is ambiguous (e.g. ``.md`` and
    ``.diff`` are both ``text/plain`` or ``text/markdown`` depending on the
    platform), then we fall back to the MIME top-level type.
    """
    ext = path.suffix.lower()
    if ext in _EXTENSION_KINDS:
        return _EXTENSION_KINDS[ext]

    if mime_type:
        top = mime_type.split("/", 1)[0]
        if top == "image":
            return "image"
        if top == "audio":
            return "audio"
        if top == "video":
            return "video"
        if mime_type == "application/pdf":
            return "pdf"
        if mime_type in ("text/html", "application/xhtml+xml"):
            return "html"
        if mime_type == "text/markdown":
            return "markdown"
        if top == "text":
            return "other"
        return "binary"

    return "other"


def _artifact_id(rel_path: str) -> str:
    """Derive a stable artifact id from its logdir-relative source path."""
    digest = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:12]
    return f"art_{digest}"


def _provenance_index(manager: LogManager, filename: str) -> int | None:
    """Best-effort: first message index whose attached files match ``filename``."""
    for idx, msg in enumerate(manager.log):
        for f in msg.files:
            try:
                if Path(str(f)).name == filename:
                    return idx
            except (TypeError, ValueError):
                continue
    return None


def derive_artifacts(manager: LogManager) -> list[Artifact]:
    """Compute the artifact list for a conversation from its attachments.

    Phase 1 only reads the ``attachments/`` directory. This is intentionally a
    pure function (no Flask state) so it can be unit tested directly.
    """
    attachments_dir = manager.logdir / "attachments"
    if not attachments_dir.is_dir():
        return []

    artifacts: list[Artifact] = []
    for item in sorted(attachments_dir.iterdir(), key=lambda p: p.name.lower()):
        if not item.is_file() or item.name.startswith("."):
            continue

        rel_path = f"attachments/{item.name}"
        mime_type, _ = mimetypes.guess_type(item.name)
        kind = classify_kind(item, mime_type)

        try:
            stat = item.stat()
            size: int | None = stat.st_size
            created_at = datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat()
        except OSError:
            size = None
            created_at = ""

        artifact_id = _artifact_id(rel_path)
        actions = [
            ArtifactAction(type="download", panel=None, artifact_id=None),
            ArtifactAction(type="open_workspace", panel=None, artifact_id=None),
            ArtifactAction(
                type="open_panel", panel="artifacts", artifact_id=artifact_id
            ),
        ]
        artifacts.append(
            Artifact(
                id=artifact_id,
                kind=kind,
                title=item.name,
                source=ArtifactSource(type="attachment", path=rel_path, url=None),
                created_at=created_at,
                size=size,
                mime_type=mime_type,
                provenance=ArtifactProvenance(
                    message_index=_provenance_index(manager, item.name), tool=None
                ),
                preview=ArtifactPreview(type=_PREVIEW_FOR_KIND.get(kind, "none")),
                actions=actions,
            )
        )
    return artifacts


@artifacts_api.route("/api/v2/conversations/<string:conversation_id>/artifacts")
@require_auth
@api_doc_simple(
    responses={
        200: ArtifactListResponse,
        404: ErrorResponse,
        500: ErrorResponse,
    },
    tags=["artifacts"],
)
def list_artifacts(conversation_id: str):
    """List artifacts for a conversation.

    Returns typed artifact descriptors computed on read from the conversation's
    attachments. The webui uses these to render a first-class Artifacts surface
    instead of relying on filename heuristics.
    """
    if error := _validate_conversation_id(conversation_id):
        return error
    try:
        try:
            manager = LogManager.load(conversation_id, lock=False)
        except FileNotFoundError:
            return flask.jsonify({"error": "Conversation not found"}), 404

        artifacts = derive_artifacts(manager)
        return flask.jsonify({"artifacts": [a.model_dump() for a in artifacts]})
    except Exception as e:
        logger.exception("Error listing artifacts")
        return flask.jsonify({"error": str(e)}), 500


@artifacts_api.route(
    "/api/v2/conversations/<string:conversation_id>/artifacts/<string:artifact_id>"
)
@require_auth
@api_doc_simple(
    responses={
        200: Artifact,
        404: ErrorResponse,
        500: ErrorResponse,
    },
    tags=["artifacts"],
)
def get_artifact(conversation_id: str, artifact_id: str):
    """Get a single artifact descriptor by id."""
    if error := _validate_conversation_id(conversation_id):
        return error
    try:
        try:
            manager = LogManager.load(conversation_id, lock=False)
        except FileNotFoundError:
            return flask.jsonify({"error": "Conversation not found"}), 404

        for artifact in derive_artifacts(manager):
            if artifact.id == artifact_id:
                return flask.jsonify(artifact.model_dump())
        return flask.jsonify({"error": "Artifact not found"}), 404
    except Exception as e:
        logger.exception("Error getting artifact")
        return flask.jsonify({"error": str(e)}), 500
