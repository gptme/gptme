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
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, NamedTuple, cast, get_args

import flask
import json_repair
from pydantic import BaseModel, Field

from ..codeblock import Codeblock
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
    diff: str | None = Field(
        None,
        description="Unified diff of the change, for files modified by the conversation",
    )


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


# Valid artifact kinds, derived from the ArtifactKind literal so a tool-supplied
# ``kind`` override is validated rather than trusted blindly.
_ARTIFACT_KINDS: frozenset[str] = frozenset(get_args(ArtifactKind))

# Source types a descriptor may declare.
_SOURCE_TYPES = frozenset({"attachment", "workspace", "external", "inline"})


def _artifact_from_descriptor(
    desc: object,
    message_index: int,
    desc_index: int,
    logdir: Path,
    default_created_at: str,
) -> Artifact | None:
    """Build an :class:`Artifact` from a tool-emitted descriptor.

    Returns ``None`` for malformed descriptors so one bad entry never breaks the
    whole artifact list. The server owns id derivation, preview hint, and
    actions; the tool only declares source, kind, title, and provenance.
    """
    if not isinstance(desc, dict):
        return None

    source_type = desc.get("source_type")
    if source_type not in _SOURCE_TYPES:
        return None

    path = desc.get("path")
    url = desc.get("url")

    # Stable id key per source type. Attachment/workspace ids hash the path so a
    # tool-declared attachment dedups against the attachment-scan artifact.
    if source_type == "external":
        if not url:
            return None
        id_key = str(url)
    elif source_type == "inline":
        id_key = f"inline:{message_index}:{desc_index}:{desc.get('title', '')}"
    else:  # attachment / workspace
        if not path:
            return None
        id_key = str(path)
    artifact_id = _artifact_id(id_key)

    title = desc.get("title") or (Path(path).name if path else url) or "artifact"

    ref_name = path or url or title
    mime_type = desc.get("mime_type")
    if mime_type is None:
        mime_type, _ = mimetypes.guess_type(ref_name)

    kind_raw = desc.get("kind")
    if kind_raw in _ARTIFACT_KINDS:
        kind = cast(ArtifactKind, kind_raw)
    else:
        kind = classify_kind(Path(ref_name), mime_type)

    # File-backed sources: stat the real file for size and creation proxy.
    size: int | None = None
    created_at = default_created_at
    if source_type in ("attachment", "workspace") and path:
        candidate = logdir / path if source_type == "attachment" else Path(path)
        try:
            stat = candidate.stat()
            size = stat.st_size
            created_at = datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat()
        except OSError:
            pass

    actions = [
        ArtifactAction(type="download", panel=None, artifact_id=None),
        ArtifactAction(type="open_workspace", panel=None, artifact_id=None),
        ArtifactAction(type="open_panel", panel="artifacts", artifact_id=artifact_id),
    ]
    return Artifact(
        id=artifact_id,
        kind=kind,
        title=str(title),
        source=ArtifactSource(type=source_type, path=path, url=url),
        created_at=created_at,
        size=size,
        mime_type=mime_type,
        provenance=ArtifactProvenance(
            message_index=message_index, tool=desc.get("tool")
        ),
        preview=ArtifactPreview(type=_PREVIEW_FOR_KIND.get(kind, "none")),
        actions=actions,
        diff=desc["diff"] if isinstance(desc.get("diff"), str) else None,
    )


def _artifacts_from_messages(
    manager: LogManager, target_id: str | None = None
) -> list[Artifact]:
    """Collect artifacts declared in message metadata (``metadata.artifacts``)."""
    out: list[Artifact] = []
    for idx, msg in enumerate(manager.log):
        meta: Any = msg.metadata or {}
        descriptors = meta.get("artifacts")
        if not isinstance(descriptors, list):
            continue
        ts = msg.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        default_created = ts.isoformat()
        for desc_idx, desc in enumerate(descriptors):
            art = _artifact_from_descriptor(
                desc, idx, desc_idx, manager.logdir, default_created
            )
            if art is not None:
                if target_id is None or art.id == target_id:
                    out.append(art)
    return out


# File-writing tools whose target becomes a conversation artifact. "save"
# creates a file; the rest modify an existing one. patch_many writes several
# files in a single tool use.
_FILE_WRITE_TOOLS = {"save", "append", "patch", "morph", "patch_many"}
_CREATE_TOOLS = {"save"}
_MULTI_PATH_TOOLS = {"patch_many"}

# Matches the per-file header of multi-hunk patch_many bodies (mirrors
# gptme.tools.patch_many._CONFIRM_HEADER_RE), used to recover paths embedded in
# the codeblock body rather than the langtag/args.
_PATCH_MANY_PATH_RE = re.compile(r"(?m)^=== PATH: (?P<path>.+?) ===\s*$")


class _FileWrite(NamedTuple):
    """One file-writing operation recovered from a message, format-agnostic."""

    tool: str
    raw_path: str
    # Edit payload for diff generation (patch body, appended text, ...). None
    # when the tool's change can't be turned into a diff (e.g. morph).
    payload: str | None


def _diff_for_write(tool: str, payload: str | None) -> str | None:
    """Build a unified-diff string for a modifying write, or None if not derivable.

    ``patch``/``patch_many`` payloads carry ORIGINAL/UPDATED blocks we can diff
    directly. ``append`` is pure additions. ``morph`` edits use ``... existing
    code ...`` placeholders with no recoverable original, so they get no diff.
    """
    if not payload:
        return None
    if tool in ("patch", "patch_many"):
        from ..tools.patch import Patch  # local import: avoids tools import at load

        try:
            diffs = [p.diff_minimal() for p in Patch._from_codeblock(payload)]
        except Exception:
            return None
        body = "\n".join(d for d in diffs if d)
        return body or None
    if tool == "append":
        added = "\n".join("+" + line for line in payload.splitlines())
        return added or None
    return None


def _iter_file_writes(content: str) -> Iterator[_FileWrite]:
    """Yield file-writing operations in a message across all tool formats.

    Handles markdown codeblocks, XML tool-use, and native tool-call JSON without
    requiring the tool registry to be initialized (the artifact read path runs in
    a server process that hasn't loaded tools). patch_many yields one entry per
    target file.
    """
    yield from _iter_markdown_writes(content)
    yield from _iter_xml_writes(content)
    yield from _iter_toolcall_writes(content)


def _split_patch_many_body(paths: list[str], body: str) -> Iterator[_FileWrite]:
    """Pair patch_many paths with their patch bodies (header- or embedded-form)."""
    embedded = list(_PATCH_MANY_PATH_RE.finditer(body))
    if embedded:
        # Multi-hunk form: `=== PATH: x ===` headers delimit per-file patches.
        for i, m in enumerate(embedded):
            start = m.end()
            end = embedded[i + 1].start() if i + 1 < len(embedded) else len(body)
            yield _FileWrite("patch_many", m.group("path"), body[start:end].strip())
        return
    # Simple form: paths in the header, one conflict-marker patch each (in order).
    from ..tools.patch import DIVIDER, ORIGINAL, UPDATED, Patch  # local import

    try:
        patches = list(Patch._from_codeblock(body))
    except Exception:
        patches = []
    for i, raw_path in enumerate(paths):
        payload = (
            f"{ORIGINAL}{patches[i].original}{DIVIDER}{patches[i].updated}{UPDATED}"
            if i < len(patches)
            else None
        )
        yield _FileWrite("patch_many", raw_path, payload)


def _iter_markdown_writes(content: str) -> Iterator[_FileWrite]:
    for cb in Codeblock.iter_from_markdown(content):
        parts = cb.lang.strip().split()
        if not parts or parts[0] not in _FILE_WRITE_TOOLS:
            continue
        tool = parts[0]
        if tool in _MULTI_PATH_TOOLS:
            yield from _split_patch_many_body(parts[1:], cb.content)
        elif len(parts) >= 2:
            # Keep the path intact even if it contains spaces.
            raw_path = cb.lang.strip().split(None, 1)[1]
            yield _FileWrite(tool, raw_path, cb.content)


def _iter_xml_writes(content: str) -> Iterator[_FileWrite]:
    from ..tools.base import ToolUse  # local import: avoids tools import at load

    for tu in ToolUse._iter_from_xml(content):
        tool = tu.tool
        if tool not in _FILE_WRITE_TOOLS:
            continue
        args = [str(a) for a in (tu.args or [])]
        body = tu.content or ""
        if tool in _MULTI_PATH_TOOLS:
            paths = [tok for a in args for tok in a.split()]
            yield from _split_patch_many_body(paths, body)
        elif args:
            # args[0] is the path; tolerate a stray leading tool-name token.
            tokens = args[0].split()
            raw_path = tokens[1] if tokens and tokens[0] == tool else args[0]
            yield _FileWrite(tool, raw_path.strip(), body)


def _iter_toolcall_writes(content: str) -> Iterator[_FileWrite]:
    from ..tools.base import find_json_end, toolcall_re  # local import

    search_from = 0
    while match := toolcall_re.search(content, search_from):
        tool = match.group(1)
        json_start = match.start(3)
        json_end = find_json_end(content, json_start)
        if json_end is None:
            break
        search_from = json_end
        if tool not in _FILE_WRITE_TOOLS:
            continue
        try:
            kwargs = json_repair.loads(content[json_start:json_end])
        except Exception:
            continue
        if not isinstance(kwargs, dict):
            continue
        if tool in _MULTI_PATH_TOOLS:
            raw = kwargs.get("patches")
            if isinstance(raw, str):
                try:
                    raw = json_repair.loads(raw)
                except Exception:
                    raw = None
            if isinstance(raw, list):
                for entry in raw:
                    if isinstance(entry, dict) and entry.get("path"):
                        yield _FileWrite(
                            "patch_many",
                            str(entry["path"]),
                            entry.get("patch"),
                        )
        else:
            path = kwargs.get("path")
            if path:
                payload = kwargs.get("patch") or kwargs.get("content")
                yield _FileWrite(tool, str(path), payload)


def _normalize_workspace_path(raw: str, workspace: Path) -> str | None:
    """Resolve a tool-written path to a workspace-relative path.

    Returns None for empty paths or files outside the workspace (those can't be
    previewed via the workspace root).
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    candidate = Path(raw) if Path(raw).is_absolute() else workspace / raw
    try:
        return str(candidate.resolve().relative_to(workspace))
    except (ValueError, OSError):
        return None


def _artifacts_from_tool_writes(
    manager: LogManager, target_id: str | None = None
) -> list[Artifact]:
    """Collect artifacts for workspace files created/modified by the conversation.

    Parses file-writing tool uses (save/append/patch/morph/patch_many) from
    assistant messages — reliable and unaffected by parallel agents, unlike a
    workspace diff. Handles markdown, XML, and native tool-call formats without
    requiring the tool registry (the read path runs in a server process that
    hasn't loaded tools). Dedups by path: a file saved then patched is reported
    once, marked as created. Modified files carry a unified ``diff`` when one can
    be derived from the edit.
    """
    workspace = manager.workspace
    # relpath -> {tool, created, message_index, created_at, diff}
    by_path: dict[str, dict[str, Any]] = {}
    for idx, msg in enumerate(manager.log):
        if msg.role != "assistant":
            continue
        for write in _iter_file_writes(msg.content):
            relpath = _normalize_workspace_path(write.raw_path, workspace)
            if not relpath:
                continue
            ts = msg.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            created = write.tool in _CREATE_TOOLS
            diff = _diff_for_write(write.tool, write.payload)
            entry = by_path.get(relpath)
            if entry is None:
                by_path[relpath] = {
                    "tool": write.tool,
                    "created": created,
                    "message_index": idx,  # first touch (kept across writes)
                    "created_at": ts.isoformat(),  # touch time; updated on later writes
                    "diff": diff,
                }
            else:
                entry["created"] = entry["created"] or created
                entry["tool"] = write.tool  # most recent operation
                entry["created_at"] = ts.isoformat()  # most recent touch
                if diff is not None:
                    entry["diff"] = diff  # keep most recent derivable diff

    out: list[Artifact] = []
    for relpath, info in by_path.items():
        # Key the id on the path (same scheme as workspace descriptors in Phase 2)
        # so a metadata-declared artifact for the same file can override this one.
        artifact_id = _artifact_id(relpath)
        if target_id is not None and artifact_id != target_id:
            continue
        mime_type, _ = mimetypes.guess_type(relpath)
        kind = classify_kind(Path(relpath), mime_type)
        size: int | None = None
        try:
            size = (workspace / relpath).stat().st_size
        except OSError:
            pass
        out.append(
            Artifact(
                id=artifact_id,
                kind=kind,
                title=Path(relpath).name,
                source=ArtifactSource(type="workspace", path=relpath, url=None),
                created_at=info["created_at"],
                size=size,
                mime_type=mime_type,
                provenance=ArtifactProvenance(
                    message_index=info["message_index"],
                    # "save" => created; otherwise the most recent modifying tool.
                    tool="save" if info["created"] else info["tool"],
                ),
                preview=ArtifactPreview(type=_PREVIEW_FOR_KIND.get(kind, "none")),
                actions=[
                    ArtifactAction(type="download", panel=None, artifact_id=None),
                    ArtifactAction(type="open_workspace", panel=None, artifact_id=None),
                    ArtifactAction(
                        type="open_panel", panel="artifacts", artifact_id=artifact_id
                    ),
                ],
                # Only modified files carry a diff; created files show full content.
                diff=None if info["created"] else info.get("diff"),
            )
        )
    return out


def derive_artifacts(
    manager: LogManager, target_id: str | None = None
) -> list[Artifact]:
    """Compute the artifact list for a conversation from its attachments.

    Phase 1 only reads the ``attachments/`` directory. This is intentionally a
    pure function (no Flask state) so it can be unit tested directly.

    Two sources are merged: the ``attachments/`` directory scan (Phase 1) and
    tool/plugin-declared descriptors in message metadata (Phase 2). When both
    describe the same artifact id, the message-declared one wins because it
    carries richer provenance (the producing tool).

    Pass ``target_id`` to filter to a single artifact, avoiding the
    O(files × messages) provenance scan for every detail request.
    """
    by_id: dict[str, Artifact] = {}

    attachments_dir = manager.logdir / "attachments"
    if attachments_dir.is_dir():
        for item in sorted(attachments_dir.iterdir(), key=lambda p: p.name.lower()):
            if not item.is_file() or item.name.startswith("."):
                continue

            rel_path = f"attachments/{item.name}"
            artifact_id = _artifact_id(rel_path)

            # Skip non-target files early to avoid the provenance scan.
            if target_id is not None and artifact_id != target_id:
                continue

            mime_type, _ = mimetypes.guess_type(item.name)
            kind = classify_kind(item, mime_type)

            try:
                stat = item.stat()
                size: int | None = stat.st_size
                # st_mtime is the last-modification time; used as a creation-time
                # proxy because st_birthtime is only available on macOS/BSD.
                created_at = datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat()
            except OSError:
                # File disappeared between iterdir() and stat() — skip it.
                continue

            actions = [
                ArtifactAction(type="download", panel=None, artifact_id=None),
                ArtifactAction(type="open_workspace", panel=None, artifact_id=None),
                ArtifactAction(
                    type="open_panel", panel="artifacts", artifact_id=artifact_id
                ),
            ]
            by_id[artifact_id] = Artifact(
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
                diff=None,  # uploaded/attachment files have no conversation diff
            )

    # Phase 3: workspace files created/modified by file-writing tool uses.
    # setdefault so an attachment with the same id (none, ids are namespaced)
    # or a richer metadata descriptor (merged below) takes precedence.
    for art in _artifacts_from_tool_writes(manager, target_id=target_id):
        by_id.setdefault(art.id, art)

    # Merge in tool/plugin-declared artifacts; these win on id collision.
    for art in _artifacts_from_messages(manager, target_id=target_id):
        by_id[art.id] = art

    return sorted(by_id.values(), key=lambda a: a.title.lower())


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

        artifacts = derive_artifacts(manager, target_id=artifact_id)
        if artifacts:
            return flask.jsonify(artifacts[0].model_dump())
        return flask.jsonify({"error": "Artifact not found"}), 404
    except Exception as e:
        logger.exception("Error getting artifact")
        return flask.jsonify({"error": str(e)}), 500
