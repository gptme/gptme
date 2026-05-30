"""
Panel registry API endpoints for conversation-scoped iframe panels.

Phase 3b of the webui artifact surface (#830): parse ``panel_hints`` from
message metadata and expose them as a typed per-conversation panel registry.
Tools declare iframe panels at runtime; the server validates src and sandbox
tokens and the webui renders each via ``SandboxedIframePanel``.
"""

import logging
from typing import Any
from urllib.parse import urlparse

import flask
from pydantic import BaseModel, Field

from ..logmanager import LogManager
from .api_v2_common import _validate_conversation_id
from .auth import require_auth
from .openapi_docs import ErrorResponse, api_doc_simple

logger = logging.getLogger(__name__)

panels_api = flask.Blueprint("panels_api", __name__)

# Sandbox tokens a descriptor may request — mirrors iframePanelPolicy.ts.
_SANDBOX_ALLOWLIST: frozenset[str] = frozenset(
    {"allow-scripts", "allow-same-origin", "allow-forms", "allow-downloads"}
)


def _is_allowed_src(src: object) -> bool:
    """Mirror the TypeScript ``isAllowedIframeSrc`` allowlist on the server.

    Accepts localhost origins (any scheme/port) and server-relative paths
    (single leading slash, not ``//``). Everything else is rejected.
    """
    if not isinstance(src, str) or not src.strip():
        return False
    value = src.strip()
    # Server-relative path: single leading slash, not protocol-relative.
    if (
        value.startswith("/")
        and not value.startswith("//")
        and not value.startswith("/\\")
    ):
        return True
    try:
        url = urlparse(value)
        host = (url.hostname or "").lower()
        return host in ("localhost", "127.0.0.1", "[::1]", "::1")
    except Exception:
        return False


def _resolve_sandbox(raw: object) -> list[str]:
    """Filter sandbox tokens to the allowlist and drop the dangerous combination.

    When both ``allow-scripts`` and ``allow-same-origin`` are requested for a
    same-origin src, the iframe can remove its own sandbox attribute.
    ``allow-same-origin`` is unconditionally dropped in that case so scripts
    still run but cannot escalate.
    """
    if not isinstance(raw, list):
        return []
    allowed = [t for t in raw if isinstance(t, str) and t in _SANDBOX_ALLOWLIST]
    deduped = list(dict.fromkeys(allowed))
    if "allow-scripts" in deduped and "allow-same-origin" in deduped:
        deduped.remove("allow-same-origin")
    return deduped


class IframePanelOut(BaseModel):
    """A validated iframe panel descriptor ready for the webui to render."""

    id: str = Field(..., description="Unique panel id within the conversation")
    kind: str = Field("iframe", description="Discriminator; always 'iframe' for now")
    title: str = Field(..., description="Tab label shown in the sidebar")
    src: str = Field(..., description="Validated iframe src URL")
    sandbox: list[str] = Field(
        default_factory=list,
        description="Filtered sandbox tokens (dangerous combinations removed)",
    )
    allow: str | None = Field(None, description="Feature-Policy string for the iframe")
    resize: str | None = Field(None, description="'auto' or 'fixed' height mode")
    bootstrap: dict[str, Any] | None = Field(
        None, description="Opaque JSON forwarded to the iframe on bootstrap"
    )
    icon: str | None = Field(None, description="Lucide icon name hint for the tab")
    message_index: int | None = Field(
        None, description="Index of the first message that declared this panel"
    )


class PanelListResponse(BaseModel):
    """Response containing the conversation's declared iframe panels."""

    panels: list[IframePanelOut] = Field(..., description="Validated panel descriptors")


def panels_from_messages(manager: LogManager) -> list[IframePanelOut]:
    """Collect iframe panel hints from ``metadata.panel_hints`` in each message.

    Later declarations with a duplicate ``id`` are dropped — the first
    declaration wins. The src and sandbox tokens are validated server-side
    before the descriptor reaches the webui.
    """
    seen_ids: set[str] = set()
    out: list[IframePanelOut] = []

    for idx, msg in enumerate(manager.log):
        meta: Any = msg.metadata or {}
        hints = meta.get("panel_hints")
        if not isinstance(hints, list):
            continue

        for hint in hints:
            if not isinstance(hint, dict):
                continue
            if hint.get("kind") != "iframe":
                continue

            panel_id = hint.get("id")
            if not isinstance(panel_id, str) or not panel_id:
                continue
            if panel_id in seen_ids:
                continue

            src = hint.get("src", "")
            if not _is_allowed_src(src):
                logger.debug("Panel %s rejected: src not allowed: %s", panel_id, src)
                continue

            seen_ids.add(panel_id)

            title = hint.get("title") or panel_id
            sandbox = _resolve_sandbox(hint.get("sandbox"))

            bootstrap = hint.get("bootstrap")
            if not isinstance(bootstrap, dict):
                bootstrap = None

            out.append(
                IframePanelOut(
                    id=panel_id,
                    kind="iframe",
                    title=str(title),
                    src=str(src).strip(),
                    sandbox=sandbox,
                    allow=hint.get("allow")
                    if isinstance(hint.get("allow"), str)
                    else None,
                    resize=hint.get("resize")
                    if hint.get("resize") in ("auto", "fixed")
                    else None,
                    bootstrap=bootstrap,
                    icon=hint.get("icon")
                    if isinstance(hint.get("icon"), str)
                    else None,
                    message_index=idx,
                )
            )

    return out


@panels_api.route("/api/v2/conversations/<string:conversation_id>/panels")
@require_auth
@api_doc_simple(
    responses={
        200: PanelListResponse,
        404: ErrorResponse,
        500: ErrorResponse,
    },
    tags=["panels"],
)
def list_panels(conversation_id: str):
    """List iframe panels declared by tools in message metadata.

    Tools emit ``panel_hints`` in their message metadata. The server validates
    each descriptor's src against the localhost/server-relative allowlist,
    filters the sandbox tokens, and drops the dangerous
    ``allow-scripts + allow-same-origin`` combination. The webui renders each
    validated panel as a ``SandboxedIframePanel`` tab in the right sidebar.
    """
    if error := _validate_conversation_id(conversation_id):
        return error
    try:
        try:
            manager = LogManager.load(conversation_id, lock=False)
        except FileNotFoundError:
            return flask.jsonify({"error": "Conversation not found"}), 404

        panels = panels_from_messages(manager)
        return flask.jsonify({"panels": [p.model_dump() for p in panels]})
    except Exception as e:
        logger.exception("Error listing panels for %s", conversation_id)
        return flask.jsonify({"error": str(e)}), 500
