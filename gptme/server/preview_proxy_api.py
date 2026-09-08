"""
Preview port proxy for gptme-server.

Exposes in-pod TCP ports under the authenticated ``/preview/{port}/`` path
so that the browser (and noVNC) can reach agent-started services without
any infrastructure changes.

Preview content is intentionally **not** mounted under ``/api/``. Serving
agent-controlled HTML on the cookie-authenticated API path would give that
document the gptme-server origin *and* the ``gptme_auth`` cookie scope, so
preview JavaScript could invoke protected API routes as the user. Iframe
and WebSocket clients authenticate with the ``Path=/preview/`` auth cookie
(set alongside ``Path=/api/``) or via Traefik ForwardAuth in the cloud.

Route (HTTP)::

    ANY /preview/<port>/[<path>]           →  http://127.0.0.1:<port>/[<path>]

Route (WebSocket)::

    WS  /preview/<port>/[<path>]           →  ws://127.0.0.1:<port>/[<path>]
    (detected by ``Upgrade: websocket`` request header)

The path is reachable via the existing Traefik-authenticated ``/api/v1/instances/{id}``
strip-prefix path in the cloud deployment, so no infra changes are required —
authentication, ownership, and WebSocket forwarding are all inherited.

Security
--------
- Only loopback targets (``127.0.0.1``) — user-supplied hostnames are rejected
  (SSRF guard).
- Ports below 1024 are rejected (privileged).
- A small set of well-known ports are explicitly blocked (e.g. gptme-server
  itself on 5700, raw x11vnc on 5900).
- ``require_auth`` applied to every route.
- HTML-like responses get ``Content-Security-Policy: sandbox`` *without*
  ``allow-same-origin``, so preview documents run in an opaque origin and
  cannot inherit API privileges. Upstream CSP is stripped so a malicious
  listener cannot opt back into ``allow-same-origin``.
- Upstream ``Set-Cookie`` / ``Clear-Site-Data`` are stripped so a preview
  service cannot overwrite the ``Path=/preview/`` auth cookie or wipe
  origin credentials.

noVNC / VNC
-----------
Interactive noVNC (websockify on :6080) is proxied via::

    /preview/6080/vnc.html
    /preview/6080/websockify   (WebSocket)

The ``ComputerPreview`` webui component should build this URL from the
server-relative base path instead of hard-coding ``http://localhost:6080``.

Design reference: gptme/gptme-cloud#910 (Option A — gptme-server sub-path proxy).
"""

from __future__ import annotations

import logging
import select
import socket
import threading
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode

import flask
import requests as req_lib

if TYPE_CHECKING:
    from collections.abc import Iterator

from .auth import require_auth

logger = logging.getLogger(__name__)

preview_proxy_api = flask.Blueprint("preview_proxy_api", __name__)

# Ports below this threshold are rejected (privileged / system ports).
_MIN_ALLOWED_PORT: int = 1024
# Ports above 65535 are invalid.
_MAX_ALLOWED_PORT: int = 65535

# Ports explicitly blocked even above 1024 (well-known dangerous local services).
_BLOCKED_PORTS: frozenset[int] = frozenset(
    {
        5700,  # gptme-server itself — avoid self-proxy loops
        5900,  # x11vnc raw VNC — allow only via websockify/noVNC on 6080
    }
)

# Hop-by-hop headers that must not be forwarded end-to-end (RFC 7230 §6.1).
_HOP_BY_HOP: frozenset[str] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

# Auth / identity headers must never be forwarded to loopback listeners.
# Local processes are untrusted; the server token authorizes every protected
# route.  Also drop Traefik ForwardAuth identity headers.
_IDENTITY_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
    }
)

# Response headers that become invalid once requests.iter_content() has
# decoded a compressed body.  Flask recomputes Content-Length as needed.
_DECODED_RESPONSE_STRIP: frozenset[str] = frozenset(
    {
        "content-encoding",
        "content-length",
        "content-md5",
    }
)

# Upstream isolation headers are replaced — a malicious listener must not
# weaken or remove our sandbox CSP.
_ISOLATION_STRIP: frozenset[str] = frozenset(
    {
        "content-security-policy",
        "content-security-policy-report-only",
        "x-content-type-options",
    }
)

# Cookie-setting / credential-clearing headers from untrusted upstreams
# must not reach the browser.  A preview service can otherwise overwrite
# gptme_auth (Path=/preview/) and 401 later preview requests, or wipe
# origin credentials via Clear-Site-Data.
_CREDENTIAL_RESPONSE_STRIP: frozenset[str] = frozenset(
    {
        "set-cookie",
        "set-cookie2",
        "clear-site-data",
    }
)

# Documents that can execute script.  Unique-origin sandbox them so they
# cannot call cookie-authenticated /api/ routes as the user.
_HTML_LIKE_MIME: frozenset[str] = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "image/svg+xml",
        "text/xml",
        "application/xml",
    }
)

# No allow-same-origin: that flag plus allow-scripts restores the parent
# origin and reopens the API-privilege hole this header exists to close.
PREVIEW_CSP_SANDBOX = (
    "sandbox allow-scripts allow-forms allow-modals "
    "allow-pointer-lock allow-popups allow-popups-to-escape-sandbox"
)


def _is_identity_header(name: str) -> bool:
    """Return True for credentials or trusted-identity headers."""
    lower = name.lower()
    if lower in _IDENTITY_HEADERS:
        return True
    return lower.startswith(("x-forwarded-", "x-auth-request-"))


def _json_error(message: str, status: int) -> flask.Response:
    """Build a JSON error Response with an explicit status code.

    Returning ``(jsonify(...), status)`` is a tuple, which mypy rejects
    when the function is annotated as ``-> flask.Response``.
    """
    response = flask.jsonify({"error": message})
    response.status_code = status
    return response


def _sanitize_query_string(qs: str) -> str:
    """Drop the deprecated ``token`` auth query param before forwarding."""
    if not qs:
        return ""
    pairs = [
        (key, value)
        for key, value in parse_qsl(qs, keep_blank_values=True)
        if key.lower() != "token"
    ]
    return urlencode(pairs)


def _forward_request_headers(
    headers: Any,
    *,
    drop_accept_encoding: bool = False,
) -> dict[str, str]:
    """Copy request headers, stripping hop-by-hop, Host, and identity."""
    forwarded: dict[str, str] = {}
    for key, value in headers:
        lower = key.lower()
        if lower in _HOP_BY_HOP or lower == "host":
            continue
        if drop_accept_encoding and lower == "accept-encoding":
            continue
        if _is_identity_header(key):
            continue
        forwarded[key] = value
    if drop_accept_encoding:
        # Prevent requests from injecting Accept-Encoding: gzip, which would
        # make iter_content() decode a body whose Content-Encoding we must
        # then strip.  identity asks the upstream not to compress.
        forwarded["Accept-Encoding"] = "identity"
    return forwarded


def _forward_response_headers(headers: Any) -> list[tuple[str, str]]:
    """Copy upstream response headers, dropping hop-by-hop, decoded-body, isolation, cookies."""
    skip = (
        _HOP_BY_HOP
        | _DECODED_RESPONSE_STRIP
        | _ISOLATION_STRIP
        | _CREDENTIAL_RESPONSE_STRIP
    )
    return [(key, value) for key, value in headers.items() if key.lower() not in skip]


def _isolation_headers(content_type: str | None) -> list[tuple[str, str]]:
    """Return origin-isolation headers for a proxied response.

    HTML-like documents are unique-origin sandboxed so preview JavaScript
    cannot exercise the user's ``gptme_auth`` cookie against ``/api/``.
    """
    headers: list[tuple[str, str]] = [("X-Content-Type-Options", "nosniff")]
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if mime in _HTML_LIKE_MIME:
        headers.append(("Content-Security-Policy", PREVIEW_CSP_SANDBOX))
    return headers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_port(port: int) -> str | None:
    """Return an error string if the port is disallowed, else ``None``."""
    if port < _MIN_ALLOWED_PORT:
        return f"port {port} is below the minimum allowed port ({_MIN_ALLOWED_PORT})"
    if port > _MAX_ALLOWED_PORT:
        return f"port {port} exceeds maximum ({_MAX_ALLOWED_PORT})"
    if port in _BLOCKED_PORTS:
        return f"port {port} is explicitly blocked"
    return None


def _is_websocket_upgrade(request: flask.Request) -> bool:
    """Return True when the HTTP request carries a WebSocket upgrade."""
    return (
        request.headers.get("Upgrade", "").lower() == "websocket"
        and "upgrade" in request.headers.get("Connection", "").lower()
    )


def _get_raw_client_socket(environ: dict[str, Any]) -> socket.socket | None:
    """Extract the raw client TCP socket from a WSGI environ dict.

    Works with Werkzeug (development server), gunicorn-gevent, and
    gunicorn-sync transports that back ``wsgi.input`` with a real socket.
    Returns ``None`` when the transport does not expose a raw socket
    (e.g. eventlet with a wrapped file object that has no ``_sock``).
    """
    # Werkzeug dev server exposes the socket directly.
    if "werkzeug.socket" in environ:
        return environ["werkzeug.socket"]
    # gunicorn exposes it under gunicorn.socket.
    if "gunicorn.socket" in environ:
        return environ["gunicorn.socket"]
    # Fall back: inspect wsgi.input for a backing socket object.
    wsgi_input = environ.get("wsgi.input")
    if wsgi_input is None:
        return None
    # gevent / gunicorn-gevent: wsgi.input.raw._sock
    raw = getattr(wsgi_input, "raw", None)
    if raw is not None:
        sock = getattr(raw, "_sock", None)
        if isinstance(sock, socket.socket):
            return sock
    # Simple case: wsgi.input._sock
    sock = getattr(wsgi_input, "_sock", None)
    if isinstance(sock, socket.socket):
        return sock
    return None


def _build_upstream_request_line(
    port: int, subpath: str, environ: dict[str, Any]
) -> bytes:
    """Build the raw HTTP/1.1 request bytes to forward to the upstream server.

    This includes the request line, all forwarded headers, and the
    double CRLF terminator.  The resulting bytes are sent verbatim over
    the upstream TCP socket so the upstream can complete the WebSocket
    handshake and then exchange frames with the browser.
    """
    method = environ.get("REQUEST_METHOD", "GET")
    path = "/" + subpath.lstrip("/")
    qs = _sanitize_query_string(environ.get("QUERY_STRING", ""))
    if qs:
        path = f"{path}?{qs}"
    http_version = environ.get("SERVER_PROTOCOL", "HTTP/1.1")

    lines: list[str] = [f"{method} {path} {http_version}"]
    for key, value in _forward_request_headers(flask.request.headers).items():
        lines.append(f"{key}: {value}")
    lines.append(f"Host: 127.0.0.1:{port}")
    lines.append("Connection: Upgrade")
    lines.append("Upgrade: websocket")
    # Two trailing CRLFs: one ends the last header, one ends the header block.
    raw = "\r\n".join(lines) + "\r\n\r\n"
    return raw.encode("latin-1")


def _pump_bidirectional(client_sock: socket.socket, target_sock: socket.socket) -> None:
    """Forward bytes between *client_sock* and *target_sock* until both close.

    Spawns two daemon threads (one per direction) and blocks until both
    finish.  Each thread shuts down the write half of the opposite socket
    when the source side closes, which propagates EOF correctly.
    """

    def _pump(src: socket.socket, dst: socket.socket, label: str) -> None:
        try:
            while True:
                # Use select with a short timeout so we can notice if the
                # other thread already closed dst.
                ready, _, _ = select.select([src], [], [src], 1.0)
                if not ready:
                    continue
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    t_fwd = threading.Thread(
        target=_pump, args=(client_sock, target_sock, "client→target"), daemon=True
    )
    t_rev = threading.Thread(
        target=_pump, args=(target_sock, client_sock, "target→client"), daemon=True
    )
    t_fwd.start()
    t_rev.start()
    t_fwd.join()
    t_rev.join()


def _websocket_tunnel(
    port: int,
    subpath: str,
    environ: dict[str, Any],
) -> flask.Response:
    """Handle a WebSocket upgrade by opening a raw TCP tunnel to the target port.

    The function connects to ``127.0.0.1:{port}``, forwards the upgrade
    request, then pumps bytes bidirectionally until both sides close.

    Returns a 501 response when the WSGI transport does not expose a raw socket.
    Returns a 502 response when the target port is unreachable.
    """
    client_sock = _get_raw_client_socket(environ)
    if client_sock is None:
        logger.warning(
            "preview_proxy: WS tunnel requested for port %d but WSGI transport "
            "does not expose a raw socket — falling back to 501",
            port,
        )
        return _json_error(
            "WebSocket tunnelling is not supported by this server transport. "
            "Run gptme-server under gunicorn-gevent or Werkzeug.",
            501,
        )

    raw_request = _build_upstream_request_line(port, subpath, environ)

    try:
        target_sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    except OSError as exc:
        logger.warning("preview_proxy: cannot connect to 127.0.0.1:%d — %s", port, exc)
        return _json_error(f"target unreachable: {exc}", 502)

    target_sock.sendall(raw_request)
    _pump_bidirectional(client_sock, target_sock)
    target_sock.close()

    # The socket is already closed; the WSGI layer must not write any body.
    # Returning a minimal 101 response satisfies Flask's response contract even
    # though no bytes will actually be sent (the connection is gone).
    return flask.Response(status=101)


def _http_stream_proxy(port: int, subpath: str) -> flask.Response:
    """Forward an HTTP request to ``127.0.0.1:{port}`` and stream the response."""
    qs = _sanitize_query_string(flask.request.query_string.decode("latin-1"))
    target_path = "/" + subpath.lstrip("/")
    if qs:
        target_path = f"{target_path}?{qs}"
    target_url = f"http://127.0.0.1:{port}{target_path}"

    # Drop Accept-Encoding so we prefer an uncompressed upstream body.
    # iter_content() still decodes if the upstream gzips anyway; response
    # Content-Encoding / Content-Length are stripped below so the browser
    # never sees a decoded body labeled as compressed.
    fwd_headers = _forward_request_headers(
        flask.request.headers, drop_accept_encoding=True
    )

    try:
        upstream = req_lib.request(
            method=flask.request.method,
            url=target_url,
            headers=fwd_headers,
            data=flask.request.get_data(),
            stream=True,
            timeout=30,
            allow_redirects=False,
        )
    except req_lib.exceptions.ConnectionError as exc:
        logger.debug("preview_proxy: upstream %s unreachable: %s", target_url, exc)
        return _json_error(f"target unreachable: {exc}", 502)
    except req_lib.exceptions.Timeout:
        return _json_error("upstream timed out", 504)

    response_headers = _forward_response_headers(upstream.headers)
    content_type = next(
        (value for key, value in response_headers if key.lower() == "content-type"),
        upstream.headers.get("Content-Type"),
    )
    response_headers.extend(_isolation_headers(content_type))

    def _generate() -> Iterator[bytes]:
        try:
            for chunk in upstream.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return flask.Response(
        _generate(),
        status=upstream.status_code,
        headers=response_headers,
        direct_passthrough=True,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_PROXY_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]


@preview_proxy_api.route(
    "/preview/<int:port>/",
    defaults={"subpath": ""},
    methods=_PROXY_METHODS,
)
@preview_proxy_api.route(
    "/preview/<int:port>/<path:subpath>",
    methods=_PROXY_METHODS,
)
@require_auth
def preview_proxy(port: int, subpath: str) -> flask.Response:
    """Proxy HTTP/WebSocket to a loopback port (Traefik Option A path)."""
    err = _check_port(port)
    if err:
        return _json_error(err, 400)

    if _is_websocket_upgrade(flask.request):
        return _websocket_tunnel(port, subpath, flask.request.environ)

    return _http_stream_proxy(port, subpath)
