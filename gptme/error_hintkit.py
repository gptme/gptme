"""Actionable error hints for common gptme CLI failures."""

from __future__ import annotations

import os
import re
import sys
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from types import TracebackType

ENV_FLAG = "HINTKIT_ENABLED"
_FALSY = {"0", "false", "no", "off"}
_DIM = "\033[2m"
_CYAN = "\033[36m"
_RESET = "\033[0m"


@dataclass(frozen=True)
class ErrorHint:
    """A compact hint for a known error pattern."""

    id: str
    title: str
    patterns: tuple[str, ...]
    text: str
    fix: str
    docs_url: str
    surfaces: tuple[str, ...] = ("cli",)

    def matches(self, message: str) -> bool:
        return any(
            re.search(pattern, message, re.IGNORECASE) for pattern in self.patterns
        )


_HINTS = (
    ErrorHint(
        id="read_tool_disabled",
        title="Read Tool Disabled",
        patterns=("read.*disabled", "disabled_by_default", "ToolNotFound.*read"),
        text="The 'read' tool is disabled by default for security.",
        fix="gptme config set tools.read.enabled true",
        docs_url="https://gptme.org/docs/tools.html#read",
    ),
    ErrorHint(
        id="health_requires_auth",
        title="Health Check Requires Auth",
        patterns=(
            "/api/v2/server/health.*401",
            "health.*authentication",
            "Unauthorized.*health",
        ),
        text="Server health checks require auth; use a fresh auth token.",
        fix="gptme -s gptme-server get-auth",
        docs_url="https://gptme.org/docs/server.html#server-auth-model",
    ),
    ErrorHint(
        id="ipython_triple_quote_fence",
        title="IPython Triple-Quote String Fence",
        patterns=("ipython.*fence", "triple.*quote.*syntax", "SyntaxError.*triple"),
        text="IPython triple-quoted strings need an execution marker or clearer fence shape.",
        fix="# Add an execution guard before triple-quoted strings in ipython blocks",
        docs_url="https://gptme.org/docs/tools.html#python",
    ),
    ErrorHint(
        id="nested_fence_swallowed",
        title="Nested Codeblock Swallowed",
        patterns=("nested.*fence", "command.*swallowed", "exec.*heuristic.*false"),
        text="The codeblock heuristic likely hit nested fences.",
        fix="gptme --tool-format tool ...",
        docs_url="https://gptme.org/docs/tool-formats.html#markdown-default",
    ),
    ErrorHint(
        id="proxy_url_shape",
        title="Proxy URL Malformed",
        patterns=("proxy.*url", "http_proxy.*invalid", "scheme.*proxy"),
        text="Proxy URLs need an explicit http:// or socks5:// scheme.",
        fix="export http_proxy=http://proxy.example.com:8080",
        docs_url="https://gptme.org/docs/config.html#environment-variables",
    ),
    ErrorHint(
        id="provider_key_missing",
        title="Provider Key Missing",
        patterns=(
            "api.*key.*missing",
            "ANTHROPIC_API_KEY",
            "OpenAI.*key",
            "provider.*env",
        ),
        text="Set the provider API key in the environment or config.",
        fix="export ANTHROPIC_API_KEY=your-key-here",
        docs_url="https://gptme.org/docs/providers.html#configuring-credentials",
    ),
    ErrorHint(
        id="cors_script_error",
        title="CORS 'Script Error' in Web",
        patterns=("CORS.*policy", "Script error", "cross-origin"),
        text="CORS blocked the request; whitelist the web origin or use dev mode locally.",
        fix="export GPTME_CORS_ORIGINS=http://localhost:5173",
        docs_url="https://gptme.org/docs/server.html#production-deployment-nginx-reverse-proxy",
    ),
    ErrorHint(
        id="server_unreachable",
        title="Server Not Reachable",
        patterns=(
            "Connection refused",
            "server.*unreachable",
            "localhost.*refused",
            "ECONNREFUSED",
        ),
        text="The gptme server is not reachable on the expected port.",
        fix="gptme-server run",
        docs_url="https://gptme.org/docs/server.html#installation",
    ),
    ErrorHint(
        id="session_archive_disabled",
        title="Session Archive Disabled",
        patterns=("archive.*disabled", "session.*unavailable", "GPTME_SAVE_SESSIONS"),
        text="Session archive support is disabled for this run.",
        fix="export GPTME_SAVE_SESSIONS=true",
        docs_url="https://gptme.org/docs/config.html#chat-config",
    ),
    ErrorHint(
        id="github_auth_expired",
        title="GitHub Auth Token Expired",
        patterns=("gh auth.*expired", "GitHub.*401", "invalid_token"),
        text="GitHub authentication expired or lacks the required scope.",
        fix="gh auth login",
        docs_url="https://gptme.org/docs/tools.html#gh",
    ),
)


def is_enabled() -> bool:
    """Return whether CLI hint rendering is enabled."""
    return os.environ.get(ENV_FLAG, "true").strip().lower() not in _FALSY


def all_hints() -> tuple[ErrorHint, ...]:
    """Return the built-in hint registry."""
    return _HINTS


def hint_for_exception(exc: BaseException) -> ErrorHint | None:
    """Find the first CLI hint matching an exception."""
    message = f"{type(exc).__name__}: {exc}"
    for hint in _HINTS:
        if "cli" in hint.surfaces and hint.matches(message):
            return hint
    return None


def render_hint(hint: ErrorHint, *, color: bool = True) -> str:
    """Render a hint as compact CLI text."""
    if color:
        return "\n".join(
            [
                f"{_DIM}hint:{_RESET} {hint.text}",
                f"  {_CYAN}{hint.fix}{_RESET}",
                f"{_DIM}  docs: {hint.docs_url}{_RESET}",
            ]
        )
    return "\n".join(
        [
            f"hint: {hint.text}",
            f"  {hint.fix}",
            f"  docs: {hint.docs_url}",
        ]
    )


def format_error(
    exc: BaseException,
    *,
    verbose: bool = False,
    color: bool = True,
    tb: TracebackType | None = None,
) -> str:
    """Format an exception plus a matching actionable hint, if known."""
    if verbose:
        parts = [
            "".join(
                traceback.format_exception(
                    type(exc), exc, tb if tb is not None else exc.__traceback__
                )
            ).rstrip()
        ]
    else:
        parts = [f"{type(exc).__name__}: {exc}"]

    if is_enabled() and (hint := hint_for_exception(exc)):
        parts.append(render_hint(hint, color=color))
    return "\n".join(parts)


_HINTKIT_HOOK_MARK = "_gptme_error_hintkit"
_HINTKIT_PREVIOUS_MARK = "_gptme_error_hintkit_previous"


def _is_hintkit_hook(hook: object) -> bool:
    return getattr(hook, _HINTKIT_HOOK_MARK, False) is True


def install_excepthook(*, verbose: bool = False, stream: TextIO | None = None) -> None:
    """Install a ``sys.excepthook`` that appends hints to uncaught CLI errors.

    Reinstall is idempotent: a later call replaces the existing HintKit hook
    in place and keeps chaining to the pre-HintKit hook, so repeated CLI
    entry (tests, embedding) cannot stack wrappers or duplicate stderr.
    """
    if not is_enabled():
        return

    current = sys.excepthook
    previous = (
        getattr(current, _HINTKIT_PREVIOUS_MARK, sys.__excepthook__)
        if _is_hintkit_hook(current)
        else current
    )
    out = stream if stream is not None else sys.stderr

    def _hook(
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc, tb)
            return
        print(format_error(exc, verbose=verbose, color=out.isatty(), tb=tb), file=out)
        if previous is not sys.__excepthook__:
            previous(exc_type, exc, tb)

    setattr(_hook, _HINTKIT_HOOK_MARK, True)
    setattr(_hook, _HINTKIT_PREVIOUS_MARK, previous)
    sys.excepthook = _hook
