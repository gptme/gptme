"""Harness update parsing and audit helpers.

Phase 1 is audit-only: assistant messages may request harness changes, but
gptme only validates and records those requests. Tool availability is unchanged.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal, cast

from .message import HarnessUpdateErrorMetadata, HarnessUpdateMetadata

if TYPE_CHECKING:
    from .message import Message, MessageMetadata

HARNESS_UPDATE_PREFIX = "HARNESS_UPDATE:"
_VALID_CHANGE_TYPES = {"enable_tool", "disable_tool", "configure_tool"}
_VALID_URGENCY = {"low", "medium", "high"}
_VALID_APPROVAL = {"auto", "log_only", "user_confirm"}


@dataclass(frozen=True)
class HarnessUpdateRequest:
    """Validated assistant request for a session-local harness change."""

    change_type: Literal["enable_tool", "disable_tool", "configure_tool"]
    tool_name: str
    reason: str
    urgency: Literal["low", "medium", "high"]
    approval_mode: Literal["auto", "log_only", "user_confirm"]
    raw_line: str
    extra: dict[str, str] = field(default_factory=dict)

    def to_metadata(self) -> HarnessUpdateMetadata:
        record: HarnessUpdateMetadata = {
            "change_type": self.change_type,
            "tool_name": self.tool_name,
            "reason": self.reason,
            "urgency": self.urgency,
            "approval_mode": self.approval_mode,
            "raw_line": self.raw_line,
        }
        if self.extra:
            record["extra"] = dict(self.extra)
        return record


@dataclass(frozen=True)
class HarnessUpdateError:
    """Rejected harness update line with an explicit reason."""

    raw_line: str
    error: str

    def to_metadata(self) -> HarnessUpdateErrorMetadata:
        return {"raw_line": self.raw_line, "error": self.error}


@dataclass
class SessionHarnessState:
    """Per-session audit state for harness update requests."""

    requests: list[HarnessUpdateRequest] = field(default_factory=list)
    rejections: list[HarnessUpdateError] = field(default_factory=list)
    last_update_at: datetime | None = None

    def record(
        self,
        requests: list[HarnessUpdateRequest],
        rejections: list[HarnessUpdateError],
        *,
        when: datetime | None = None,
    ) -> None:
        if not requests and not rejections:
            return
        self.requests.extend(requests)
        self.rejections.extend(rejections)
        self.last_update_at = when or datetime.now(tz=timezone.utc)


def extract_harness_updates(
    content: str, *, available_tool_names: set[str] | None = None
) -> tuple[list[HarnessUpdateRequest], list[HarnessUpdateError]]:
    """Extract and validate all harness update lines from assistant content.

    ``available_tool_names`` is the set of tool names considered valid for
    validation.  When *not* provided it defaults to **all tools discoverable
    from gptme's module system** (via :func:`~gptme.tools.get_available_tools`)
    — which includes tools that are not currently loaded in the session.  This
    means an ``enable_tool`` request for a disabled-but-known tool correctly
    passes validation even though the tool is absent from the active tool list.

    Pass an explicit set only when you want to restrict validation to a
    specific subset (e.g. in unit tests or plugin sandboxes).
    """

    # Line-anchored early-exit: only trigger tool discovery when the prefix
    # actually starts a line (after stripping leading whitespace).  A bare
    # substring check would also fire for prose or code-block mentions of the
    # prefix string, causing an unnecessary get_available_tools() call on every
    # such assistant message.
    if not any(
        line.lstrip().startswith(HARNESS_UPDATE_PREFIX) for line in content.splitlines()
    ):
        return [], []

    if available_tool_names is None:
        available_tool_names = _get_all_known_tool_names()

    requests: list[HarnessUpdateRequest] = []
    errors: list[HarnessUpdateError] = []

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith(HARNESS_UPDATE_PREFIX):
            continue
        request, error = _parse_harness_update_line(
            stripped, available_tool_names=available_tool_names
        )
        if request is not None:
            requests.append(request)
        elif error is not None:
            errors.append(error)

    return requests, errors


def annotate_message_with_harness_updates(
    message: Message,
    *,
    session_harness: SessionHarnessState | None = None,
    available_tool_names: set[str] | None = None,
) -> tuple[Message, list[HarnessUpdateRequest], list[HarnessUpdateError]]:
    """Attach parsed harness updates to message metadata and optional session state."""

    requests, errors = extract_harness_updates(
        message.content, available_tool_names=available_tool_names
    )
    if not requests and not errors:
        return message, requests, errors

    metadata = dict(message.metadata) if message.metadata else {}

    if requests:
        existing = cast(
            list[HarnessUpdateMetadata], metadata.get("harness_updates", [])
        )
        metadata["harness_updates"] = existing + [r.to_metadata() for r in requests]
    if errors:
        existing_errors = cast(
            list[HarnessUpdateErrorMetadata],
            metadata.get("harness_update_errors", []),
        )
        metadata["harness_update_errors"] = existing_errors + [
            e.to_metadata() for e in errors
        ]

    if session_harness is not None:
        # Normalize to UTC: Message.timestamp defaults to datetime.now() which
        # is naive.  SessionHarnessState.record() only substitutes utcnow()
        # when when=None, so a naive timestamp would be stored as-is, breaking
        # any subsequent tz-aware comparison (e.g. session age checks).
        ts = message.timestamp
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        session_harness.record(requests, errors, when=ts)

    return (
        message.replace(metadata=cast("MessageMetadata", metadata)),
        requests,
        errors,
    )


def _has_unterminated_delimiter_quote(token: str) -> bool:
    """Return True if a token's value part starts with a quote that is not closed.

    A *delimiter-style* quote is one that opens a value (``reason="text``).  An
    apostrophe embedded mid-word (``reason=user's``) is an intentional character,
    not a delimiter, so it is NOT flagged.  The distinction is: does the value
    start with the quote character?

    Examples::

        _has_unterminated_delimiter_quote('reason="unterminated')  # True
        _has_unterminated_delimiter_quote("reason=user's")         # False
        _has_unterminated_delimiter_quote('reason="done"')         # False
    """
    value = token.split("=", 1)[1] if "=" in token else token
    for quote in ('"', "'"):
        if value.startswith(quote) and not (len(value) >= 2 and value.endswith(quote)):
            return True
    return False


def _rejoin_reason_tokens(tokens: list[str]) -> list[str]:
    """Re-join bare continuation words into the reason= token.

    When ``shlex.split`` fails (e.g. due to an unquoted apostrophe) and we
    fall back to ``payload.split()``, a multi-word reason like
    ``reason=user's request`` becomes ``["reason=user's", "request"]``.  The
    bare token ``"request"`` has no ``=``, so the parser would treat it as an
    unexpected token and reject the whole line.

    This helper scans for the ``reason=`` token and merges any immediately
    following tokens that contain no ``=`` (i.e. are bare words) back into the
    reason value, restoring the intent of the original line.
    """
    result: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("reason="):
            # Collect continuation words (no '=' means they are part of the value)
            j = i + 1
            while j < len(tokens) and "=" not in tokens[j]:
                j += 1
            if j > i + 1:
                tok = " ".join([tok] + tokens[i + 1 : j])
            result.append(tok)
            i = j
        else:
            result.append(tok)
            i += 1
    return result


def _parse_harness_update_line(
    line: str, *, available_tool_names: set[str]
) -> tuple[HarnessUpdateRequest | None, HarnessUpdateError | None]:
    raw = line
    payload = line[len(HARNESS_UPDATE_PREFIX) :].strip()
    if not payload:
        return None, HarnessUpdateError(raw, "missing update payload")

    try:
        tokens = shlex.split(payload)
    except ValueError:
        # shlex.split treats apostrophes as quote characters; an unquoted
        # apostrophe inside a value (e.g. reason=user's request) raises ValueError.
        # Fall back to whitespace splitting, then re-join any bare continuation
        # words after reason= into the reason value.  Without this, a reason
        # like "reason=user's request" is split into ["reason=user's", "request"]
        # and the bare token "request" triggers an unexpected-token error.
        raw_tokens = payload.split()
        tokens = _rejoin_reason_tokens(raw_tokens)
        # Detect delimiter-style quotes that shlex couldn't close
        # (e.g. reason="unterminated).  Record as error so the audit log never
        # contains a request with a malformed reason value.
        for tok in tokens:
            if _has_unterminated_delimiter_quote(tok):
                return None, HarnessUpdateError(
                    raw, f"unterminated quoted value in token '{tok}'"
                )

    if len(tokens) < 2:
        return None, HarnessUpdateError(
            raw, "expected change_type and tool_name after HARNESS_UPDATE:"
        )

    change_type = tokens[0]
    tool_name = tokens[1]
    if change_type not in _VALID_CHANGE_TYPES:
        return None, HarnessUpdateError(raw, f"unknown change_type '{change_type}'")
    if tool_name not in available_tool_names:
        return None, HarnessUpdateError(raw, f"unknown tool '{tool_name}'")

    values: dict[str, str] = {}
    extras: dict[str, str] = {}
    for token in tokens[2:]:
        if "=" not in token:
            return None, HarnessUpdateError(
                raw, f"unexpected token '{token}' (expected key=value)"
            )
        key, value = token.split("=", 1)
        if not key:
            return None, HarnessUpdateError(raw, "empty key in key=value token")
        if key in {"reason", "urgency", "approval", "approval_mode"}:
            values[key] = value
        else:
            extras[key] = value

    reason = values.get("reason", "").strip()
    if not reason:
        return None, HarnessUpdateError(raw, "missing reason=<text>")

    urgency = values.get("urgency", "").strip()
    if urgency not in _VALID_URGENCY:
        return None, HarnessUpdateError(
            raw, "urgency must be one of: low, medium, high"
        )

    approval_mode = (
        values.get("approval") or values.get("approval_mode") or ""
    ).strip()
    if approval_mode not in _VALID_APPROVAL:
        return None, HarnessUpdateError(
            raw, "approval must be one of: auto, log_only, user_confirm"
        )

    return (
        HarnessUpdateRequest(
            change_type=cast(
                Literal["enable_tool", "disable_tool", "configure_tool"],
                change_type,
            ),
            tool_name=tool_name,
            reason=reason,
            urgency=cast(Literal["low", "medium", "high"], urgency),
            approval_mode=cast(
                Literal["auto", "log_only", "user_confirm"], approval_mode
            ),
            raw_line=raw,
            extra=extras,
        ),
        None,
    )


def _get_all_known_tool_names() -> set[str]:
    """Return names of all tools known to gptme, regardless of session state.

    This performs a full module-level discovery (same as
    :func:`~gptme.tools.get_available_tools`) and intentionally includes tools
    that are not currently loaded in the active session.  An ``enable_tool``
    request must be allowed to name a tool that is currently disabled — that is
    the whole point of the request.
    """
    from .tools import get_available_tools

    return {tool.name for tool in get_available_tools()}
