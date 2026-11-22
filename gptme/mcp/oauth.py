"""OAuth authorization-code (PKCE) support for MCP HTTP servers.

The ``mcp`` SDK drives the protocol side of the flow: RFC 9728 protected-resource
metadata, RFC 8414 authorization-server metadata, dynamic client registration,
PKCE, and refresh. This module supplies the two pieces the SDK leaves to the
application:

- where tokens live (:class:`FileTokenStorage`, one 0600 JSON file per server), and
- how the browser round-trip happens (:class:`LoopbackReceiver` plus the two
  handlers the provider awaits), including what to do when there is no browser
  to open.

Keeping this out of :mod:`gptme.mcp.client` leaves the transport code readable
and lets the flow be unit-tested without an MCP server or an authorization
server.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import threading
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl, ValidationError

from ..dirs import get_config_dir

logger = logging.getLogger(__name__)

TOKEN_DIRNAME = "mcp-oauth"
DEFAULT_FLOW_TIMEOUT_S = 300.0
# Opt-out for callers that never want a browser to open (CI, scripted runs).
OAUTH_ENV_VAR = "GPTME_MCP_OAUTH"
_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})
_CALLBACK_PATH = "/callback"
# A browser may open the socket before it sends the request (preconnect), and a
# stale connection must not hold a worker forever.
_REQUEST_TIMEOUT_S = 30


class MCPOAuthError(RuntimeError):
    """Base class for MCP OAuth failures that deserve a clear message."""


class OAuthLoginUnavailable(MCPOAuthError):
    """Raised when the flow needs a browser the current session cannot provide."""


def oauth_enabled(environ: dict[str, str] | None = None) -> bool:
    """Return whether the OAuth flow may run (``GPTME_MCP_OAUTH`` opts out)."""
    env = os.environ if environ is None else environ
    return env.get(OAUTH_ENV_VAR, "").strip().lower() not in _DISABLED_VALUES


def has_authorization_header(headers: dict[str, str] | None) -> bool:
    """Return whether static config already carries a bearer token."""
    return any(name.lower() == "authorization" for name in (headers or {}))


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", text or "").strip("-.")
    return cleaned[:48] or "server"


def token_storage_path(
    server_name: str, url: str, *, config_dir: Path | None = None
) -> Path:
    """Return the token file for one server.

    The URL is part of the name: pointing a server at a different resource must
    start a fresh authorization instead of reusing a token minted for the old
    one.
    """
    digest = hashlib.sha256((url or "").encode("utf-8")).hexdigest()[:8]
    base = get_config_dir() if config_dir is None else config_dir
    return base / TOKEN_DIRNAME / f"{_slug(server_name)}-{digest}.json"


class FileTokenStorage(TokenStorage):
    """Persist client registration and tokens as one private JSON file.

    A read error is treated as "no credentials yet" rather than a hard failure:
    a truncated file should cost one extra authorization, not a broken config.
    Writes go through a private temporary file and ``os.replace`` so a crash
    cannot leave half a token file behind.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as e:
            logger.warning(f"MCP OAuth: cannot read {self.path}: {e}")
            return {}
        except UnicodeDecodeError:
            # Truncated or partially overwritten bytes are not recoverable, but
            # they must not stop a fresh authorization either.
            logger.warning(f"MCP OAuth: ignoring undecodable token file {self.path}")
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            logger.warning(f"MCP OAuth: ignoring unreadable token file {self.path}")
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, key: str, value: dict | None) -> None:
        data = self._read()
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        # mkstemp already creates 0600; chmod keeps it after replacement on
        # platforms where the mode survives the rename differently.
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._read().get("tokens")
        if not isinstance(raw, dict):
            return None
        try:
            return OAuthToken.model_validate(raw)
        except ValidationError:
            logger.warning(f"MCP OAuth: ignoring malformed tokens in {self.path}")
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._write("tokens", tokens.model_dump(mode="json", exclude_none=True))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._read().get("client_info")
        if not isinstance(raw, dict):
            return None
        try:
            return OAuthClientInformationFull.model_validate(raw)
        except ValidationError:
            logger.warning(f"MCP OAuth: ignoring malformed client info in {self.path}")
            return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._write(
            "client_info", client_info.model_dump(mode="json", exclude_none=True)
        )


class LoopbackReceiver:
    """One-shot HTTP listener for the authorization redirect (RFC 8252 §7.3).

    The port is chosen by the OS before the client metadata is built, because
    the redirect URI is part of the registration and must not change between
    registration and callback.
    """

    def __init__(self, host: str = "127.0.0.1") -> None:
        self._result: tuple[str, str | None] | None = None
        self._error: str | None = None
        self._done = threading.Event()
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            # Drop connections that connect but never send a request, instead
            # of keeping a worker blocked on them.
            timeout = _REQUEST_TIMEOUT_S

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path != _CALLBACK_PATH:
                    self._respond(404, "Not found")
                    return
                params = parse_qs(parsed.query)
                error = params.get("error", [None])[0]
                code = params.get("code", [None])[0]
                if error:
                    outer._error = params.get("error_description", [error])[0] or error
                elif not code:
                    outer._error = "the authorization server returned no code"
                else:
                    outer._result = (code, params.get("state", [None])[0])
                self._respond(
                    200 if outer._result else 400,
                    "gptme: authorization received, you can close this tab."
                    if outer._result
                    else "gptme: authorization failed, check the terminal.",
                )
                outer._done.set()

            def _respond(self, status: int, message: str) -> None:
                body = message.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        # One thread per connection: a browser that opens the socket early would
        # otherwise block the actual callback in a single-threaded server.
        self._httpd = ThreadingHTTPServer((host, 0), _Handler)
        self._httpd.daemon_threads = True
        self._closed = False
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="gptme-mcp-oauth", daemon=True
        )
        self._thread.start()

    @property
    def redirect_uri(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host!s}:{port}{_CALLBACK_PATH}"

    def reset(self) -> None:
        """Arm the receiver for one flow, before the URL is shown to the user."""
        self._result = None
        self._error = None
        self._done.clear()

    async def wait(self, timeout: float) -> tuple[str, str | None]:
        """Block until the redirect arrives, then return ``(code, state)``.

        The receiver is not torn down afterwards: the redirect URI is part of
        the client registration, so a flow restarted after a failed refresh has
        to land on the same port. ``reset()`` arms it for the next attempt.
        """
        if self._closed:
            raise OAuthLoginUnavailable("the OAuth listener is already closed")
        loop = asyncio.get_running_loop()
        try:
            delivered = await loop.run_in_executor(None, self._done.wait, timeout)
        except asyncio.CancelledError:
            # Cancelling the sign-in must not leave a worker thread blocked for
            # the rest of the OAuth timeout: interpreter shutdown joins it.
            self._done.set()
            raise
        if not delivered:
            raise OAuthLoginUnavailable(
                f"timed out after {int(timeout)}s waiting for the OAuth redirect "
                "on the loopback address"
            )
        if self._error:
            raise OAuthLoginUnavailable(f"authorization was not granted: {self._error}")
        if self._result is None:
            raise OAuthLoginUnavailable("no authorization code was received")
        return self._result

    def close(self) -> None:
        """Stop the listener and release anyone waiting for the redirect."""
        if self._closed:
            return
        self._closed = True
        if self._result is None and self._error is None:
            self._error = "the OAuth listener was closed before the redirect arrived"
        # Wakes a `wait()` blocked in a worker thread, so closing cannot hang
        # for the remainder of the OAuth timeout.
        self._done.set()
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)


def _can_prompt_for_browser() -> bool:
    """Whether this session can hand the user a login URL and wait for it."""
    if os.environ.get("CI"):
        return False
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


@dataclass
class OAuthHandler:
    """An ``httpx`` auth handler plus the loopback listener it owns.

    The provider needs a stable redirect URI for the whole session (a refresh
    failure can restart the flow), so the listener cannot be torn down as soon
    as the first token arrives. Bundling them keeps that lifetime explicit.
    """

    provider: OAuthClientProvider
    receiver: LoopbackReceiver
    # The MCP transport reports a failed handshake as a cancellation, which
    # discards the exception that caused it. Keeping the last handler failure
    # here is what lets the caller report "no browser available" instead of a
    # generic 401.
    errors: list[Exception] = field(default_factory=list)

    @property
    def last_error(self) -> Exception | None:
        return self.errors[-1] if self.errors else None

    def close(self) -> None:
        self.receiver.close()


def build_oauth_provider(
    server_name: str,
    url: str,
    *,
    timeout: float = DEFAULT_FLOW_TIMEOUT_S,
    config_dir: Path | None = None,
    can_prompt: bool | None = None,
) -> OAuthHandler | None:
    """Build the ``httpx`` auth handler for one MCP HTTP server.

    Returns ``None`` when the flow is disabled (``GPTME_MCP_OAUTH=0``), so the
    caller can fall back to static headers unchanged.
    """
    if not oauth_enabled():
        return None

    receiver = LoopbackReceiver()
    metadata = OAuthClientMetadata(
        redirect_uris=[AnyUrl(receiver.redirect_uri)],
        client_name="gptme",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )
    interactive = _can_prompt_for_browser() if can_prompt is None else can_prompt
    errors: list[Exception] = []

    async def redirect_handler(authorization_url: str) -> None:
        if not interactive:
            receiver.close()
            error = OAuthLoginUnavailable(
                "this MCP server requires OAuth, but the current session cannot "
                "open a browser. Run gptme interactively once to authorize it "
                f"(the token is cached in {token_storage_path(server_name, url, config_dir=config_dir).parent}), "
                "or configure [mcp.servers.headers] with an Authorization value."
            )
            errors.append(error)
            raise error
        logger.info(f"MCP OAuth: authorize {server_name} in your browser")
        receiver.reset()
        print(
            f"\nAuthorize gptme for MCP server '{server_name}':\n{authorization_url}\n",
            file=sys.stderr,
        )
        if not webbrowser.open(authorization_url):
            logger.warning("MCP OAuth: could not open a browser; use the URL above")

    async def callback_handler() -> tuple[str, str | None]:
        try:
            code, state = await receiver.wait(timeout)
        except OAuthLoginUnavailable as e:
            errors.append(e)
            raise
        # The provider compares `state` with the value it generated; keeping the
        # nonce opaque here avoids a second, subtly different check.
        return code, state

    provider = OAuthClientProvider(
        server_url=url,
        client_metadata=metadata,
        storage=FileTokenStorage(
            token_storage_path(server_name, url, config_dir=config_dir)
        ),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
        timeout=timeout,
    )
    return OAuthHandler(provider=provider, receiver=receiver, errors=errors)


def close_oauth_provider(handler: OAuthHandler | None) -> None:
    """Release the loopback listener owned by ``handler``, if any."""
    if handler is not None:
        handler.close()
