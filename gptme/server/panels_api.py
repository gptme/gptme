"""
Panel registry API endpoints for conversation-scoped iframe and live_app panels.

Phase 3b of the webui artifact surface (#830): parse ``panel_hints`` from
message metadata and expose them as a typed per-conversation panel registry.
Tools declare iframe panels at runtime; the server validates src and sandbox
tokens and the webui renders each via ``SandboxedIframePanel``.

Since #830 peer-follow-on-3, the registry also supports ``kind: "live_app"``
panels with lifecycle state (running/stopped/error), distinct from the generic
iframe panel.
"""

import logging
from typing import Any, Literal
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
    kind: str = Field("iframe", description="Discriminator; always 'iframe'")
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
    warnings: list[str] = Field(
        default_factory=list,
        description="Validation warnings about opaque-origin sandbox limitations",
    )


class LiveAppPanelOut(BaseModel):
    """A validated live app preview panel descriptor.

    A live app panel represents a durably running process with a listen
    address, lifecycle state (running/stopped/error), and health signal.
    It is a distinct semantic from the generic iframe panel — the webui
    renders it with a status header bar.
    """

    id: str = Field(..., description="Unique panel id within the conversation")
    kind: str = Field("live_app", description="Discriminator; always 'live_app'")
    title: str = Field(..., description="Tab label shown in the sidebar")
    url: str = Field(
        ..., description="Validated URL of the running app (same allowlist as iframe)"
    )
    status: Literal["loading", "running", "stopped", "error", "unavailable"] = Field(
        ..., description="Current app lifecycle state"
    )
    status_message: str | None = Field(
        None, description="Human-readable status line shown in the panel header"
    )
    sandbox: list[str] = Field(
        default_factory=list,
        description="Sandbox tokens (same allowlist as iframe panels)",
    )
    icon: str | None = Field(None, description="Lucide icon name hint for the tab")
    message_index: int | None = Field(
        None, description="Index of the first message that declared this panel"
    )


class PanelListResponse(BaseModel):
    """Response containing the conversation's declared panels."""

    panels: list[IframePanelOut | LiveAppPanelOut] = Field(
        ..., description="Validated panel descriptors (iframe + live_app)"
    )


def panels_from_messages(manager: LogManager) -> list[IframePanelOut | LiveAppPanelOut]:
    """Collect panel hints from ``metadata.panel_hints`` in each message.

    Supports ``kind: "iframe"`` (generic sandboxed iframe UI) and
    ``kind: "live_app"`` (running app preview with lifecycle state).

    Later declarations with a duplicate ``id`` are dropped — the first
    declaration wins. The src/url and sandbox tokens are validated server-side
    before the descriptor reaches the webui.
    """
    seen_ids: set[str] = set()
    out: list[IframePanelOut | LiveAppPanelOut] = []

    for idx, msg in enumerate(manager.log):
        meta: Any = msg.metadata or {}
        hints = meta.get("panel_hints")
        if not isinstance(hints, list):
            continue

        for hint in hints:
            if not isinstance(hint, dict):
                continue
            kind = hint.get("kind")
            if kind not in ("iframe", "live_app"):
                continue

            panel_id = hint.get("id")
            if not isinstance(panel_id, str) or not panel_id:
                continue
            if panel_id in seen_ids:
                continue

            # Both kinds validate against the same src/url allowlist.
            src_or_url = hint.get("src") if kind == "iframe" else hint.get("url")
            if not _is_allowed_src(src_or_url):
                logger.debug(
                    "Panel %s (%s) rejected: src/url not allowed: %s",
                    panel_id, kind, src_or_url,
                )
                continue

            seen_ids.add(panel_id)
            title = str(hint.get("title") or panel_id)

            if kind == "iframe":
                panel = _build_iframe_panel(hint, idx, panel_id, title)
            else:
                panel = _build_live_app_panel(hint, idx, panel_id, title)
            out.append(panel)

    return out


def _build_iframe_panel(
    hint: dict[str, Any], idx: int, panel_id: str, title: str
) -> IframePanelOut:
    """Build an IframePanelOut from a validated hint dict."""
    src = hint.get("src", "")
    sandbox = _resolve_sandbox(hint.get("sandbox"))

    bootstrap = hint.get("bootstrap")
    if not isinstance(bootstrap, dict):
        bootstrap = None

    warnings: list[str] = []
    stripped_src = str(src).strip()
    if stripped_src.startswith("/") and "allow-scripts" in sandbox:
        warnings.append(
            "Server-relative src with 'allow-scripts' sandbox has opaque origin; "
            "postMessage bootstrap handshake will not work. "
            "Use a localhost absolute URL for full functionality."
        )

    if isinstance(hint.get("allow"), str):
        warnings.append(
            "The 'allow' attribute is not forwarded for security. "
            "Use the postMessage bootstrap handshake for controlled capabilities."
        )

    return IframePanelOut(
        id=panel_id,
        kind="iframe",
        title=title,
        src=stripped_src,
        sandbox=sandbox,
        allow=None,
        resize=hint.get("resize")
        if hint.get("resize") in ("auto", "fixed")
        else None,
        bootstrap=bootstrap,
        icon=hint.get("icon") if isinstance(hint.get("icon"), str) else None,
        message_index=idx,
        warnings=warnings,
    )


def _build_live_app_panel(
    hint: dict[str, Any], idx: int, panel_id: str, title: str
) -> LiveAppPanelOut:
    """Build a LiveAppPanelOut from a validated hint dict."""
    url = str(hint.get("url", ""))
    sandbox = _resolve_sandbox(hint.get("sandbox"))

    valid_statuses = {"loading", "running", "stopped", "error", "unavailable"}
    status: str = hint.get("status", "loading")
    if status not in valid_statuses:
        status = "loading"

    return LiveAppPanelOut(
        id=panel_id,
        kind="live_app",
        title=title,
        url=url.strip(),
        status=status,  # type: ignore
        status_message=str(hint["status_message"])
        if isinstance(hint.get("status_message"), str)
        else None,
        sandbox=sandbox,
        icon=hint.get("icon") if isinstance(hint.get("icon"), str) else None,
        message_index=idx,
    )


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
    """List panels declared by tools in message metadata.

    Tools emit ``panel_hints`` in their message metadata. Supports two kinds:

    - ``kind: "iframe"`` — generic sandboxed iframe panels for plugin-owned
      custom UI. The server validates src against the localhost/server-relative
      allowlist, filters sandbox tokens, and drops the dangerous
      ``allow-scripts + allow-same-origin`` combination.
    - ``kind: "live_app"`` — running app preview panels with explicit lifecycle
      state (running/stopped/error). Uses the same URL allowlist but carries
      a ``status`` field and no ``allow`` / ``resize`` / ``bootstrap`` fields.

    The webui renders each validated panel as a tab in the right sidebar.
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
