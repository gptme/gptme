"""Local suggestion-decision tracking for ``gptme --diff`` sessions."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

    from ..hooks import ConfirmationResult
    from ..tools.base import ToolUse

_EDIT_TOOLS = frozenset(
    {"append", "hashline_edit", "morph", "patch", "patch_many", "save"}
)
_MAX_PREVIEW_CHARS = 50_000
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$")
logger = logging.getLogger(__name__)


@dataclass
class _DiffSuggestionTracker:
    path: Path
    ref: str
    lock: threading.Lock = field(default_factory=threading.Lock)


_tracker: ContextVar[_DiffSuggestionTracker | None] = ContextVar(
    "diff_suggestion_tracker", default=None
)


def _resolve_tracker() -> _DiffSuggestionTracker | None:
    return _tracker.get()


def is_diff_tracking_active() -> bool:
    """Return whether this thread has an active ``--diff`` suggestion tracker."""
    return _resolve_tracker() is not None


def snapshot_diff_tracker() -> _DiffSuggestionTracker | None:
    """Capture this thread's --diff tracker for a child thread to inherit.

    Thread-mode subagents start with a fresh contextvars context, so they
    cannot see the ContextVar set around the parent chat(). The parent
    snapshots before spawn; the child restores at thread start. There is no
    process-global fallback: an unrelated worker without a restored tracker
    must not write to another session's ledger.
    """
    return _resolve_tracker()


def restore_diff_tracker(tracker: _DiffSuggestionTracker | None) -> None:
    """Bind a parent --diff tracker in this thread's context."""
    if tracker is not None:
        _tracker.set(tracker)


@contextmanager
def track_diff_suggestions(logdir: Path, ref: str) -> Generator[Path, None, None]:
    """Record edit-tool confirmation decisions for one ``--diff`` session."""
    path = logdir / "diff-suggestions.jsonl"
    tracker = _DiffSuggestionTracker(path=path, ref=ref)
    token = _tracker.set(tracker)
    try:
        yield path
    finally:
        _tracker.reset(token)


def _targets(tool_use: ToolUse) -> list[str]:
    targets: list[str] = []
    if tool_use.kwargs:
        for key in ("path", "paths"):
            value = tool_use.kwargs.get(key)
            if value:
                targets.append(value)
    if tool_use.args:
        targets.extend(arg for arg in tool_use.args if arg)
    return list(dict.fromkeys(targets))


def _range_end(start: int, count: int) -> int:
    """Return an inclusive range end.

    Empty hunks (count=0, e.g. ``@@ -10,0 +12,2 @@``) use ``end == start`` so
    consumers never see an inverted ``end < start`` range. Pair with ``*_count``
    to distinguish an empty range from a one-line change at the same start.
    """
    return start if count <= 0 else start + count - 1


def _diff_header_path(line: str) -> str | None:
    """Extract the path from a unified-diff file header."""
    path = line[4:].split("\t", 1)[0]
    return None if path == "/dev/null" else path


def _native_patch_line_ranges(preview: str, target: str) -> list[dict[str, int | str]]:
    """Map headerless ``patch`` previews back to their target file.

    ``patch.preview_patch()`` deliberately removes synthetic unified-diff hunk
    headers. Its old-side lines still identify the changed span, provided that
    span occurs exactly once in the current file. Ambiguous previews stay
    unmapped instead of inventing a location.

    Positions are resolved against the pristine file and hunks are ordered by
    their original location, so ranges do not depend on the order the model
    wrote the hunks in the preview.
    """
    try:
        path = Path(target).expanduser()
        original = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []

    located: list[tuple[int, int, int]] = []
    for hunk in preview.split("\n@@@\n"):
        diff_lines = hunk.splitlines()
        if not diff_lines or any(
            line and line[0] not in {" ", "+", "-", "\\"} for line in diff_lines
        ):
            return []

        old_lines = [line[1:] for line in diff_lines if line.startswith((" ", "-"))]
        new_lines = [line[1:] for line in diff_lines if line.startswith((" ", "+"))]
        if not old_lines:
            return []

        matches = [
            index
            for index in range(len(original) - len(old_lines) + 1)
            if original[index : index + len(old_lines)] == old_lines
        ]
        if len(matches) != 1:
            return []

        located.append((matches[0], len(old_lines), len(new_lines)))

    located.sort(key=lambda item: item[0])
    ranges: list[dict[str, int | str]] = []
    line_delta = 0
    prev_end = -1
    for index, old_count, new_count in located:
        # Overlapping spans cannot be attributed to the original file.
        if index <= prev_end:
            return []
        prev_end = index + old_count - 1
        old_start = index + 1
        new_start = old_start + line_delta
        ranges.append(
            {
                "file": target,
                "old_start": old_start,
                "old_end": _range_end(old_start, old_count),
                "old_count": old_count,
                "new_start": new_start,
                "new_end": _range_end(new_start, new_count),
                "new_count": new_count,
            }
        )
        line_delta += new_count - old_count

    return ranges


def _line_ranges(
    preview: str, targets: list[str], tool: str
) -> list[dict[str, int | str]]:
    """Parse file and line ranges from unified-diff hunk headers.

    Minimal previews omit ``---``/``+++`` headers, so a sole tool target is a
    safe fallback. Multi-target previews without file headers are ambiguous and
    deliberately remain unmapped; the raw preview in the event stays available.
    """
    fallback_file = targets[0] if len(targets) == 1 else None
    old_file: str | None = None
    new_file: str | None = None
    ranges: list[dict[str, int | str]] = []

    for line in preview.splitlines():
        if line.startswith("--- "):
            old_file = _diff_header_path(line)
            continue
        if line.startswith("+++ "):
            new_file = _diff_header_path(line)
            continue

        match = _HUNK_HEADER_RE.match(line)
        if match is None:
            continue

        file = new_file or old_file or fallback_file
        if file is None:
            continue

        old_start, old_count, new_start, new_count = match.groups()
        old_start_int = int(old_start)
        old_count_int = int(old_count or 1)
        new_start_int = int(new_start)
        new_count_int = int(new_count or 1)
        ranges.append(
            {
                "file": file,
                "old_start": old_start_int,
                "old_end": _range_end(old_start_int, old_count_int),
                "old_count": old_count_int,
                "new_start": new_start_int,
                "new_end": _range_end(new_start_int, new_count_int),
                "new_count": new_count_int,
            }
        )

    if ranges or tool != "patch" or fallback_file is None:
        return ranges
    return _native_patch_line_ranges(preview, fallback_file)


def diff_suggestion_line_ranges(
    tool_use: ToolUse | None, preview: str | None
) -> list[dict[str, int | str]]:
    """Resolve a suggestion's ranges against the pre-execution workspace."""
    if tool_use is None:
        return []
    return _line_ranges(
        preview or tool_use.preview_content or "",
        _targets(tool_use),
        tool_use.tool,
    )


def record_diff_suggestion(
    tool_use: ToolUse | None,
    result: ConfirmationResult,
    preview: str | None,
    *,
    edited_by_user: bool = False,
    confirmation_automatic: bool = False,
    decision_override: str | None = None,
    execution_status: str | None = None,
    execution_error: str | None = None,
    line_ranges_override: list[dict[str, int | str]] | None = None,
) -> None:
    """Append one accepted/skipped edit suggestion to the active session ledger.

    The full preview is retained locally so later measurement can identify the
    proposed hunk. The tool payload remains in the ordinary conversation log;
    ``suggestion_id`` provides a stable correlation key without duplicating it.
    """
    tracker = _resolve_tracker()
    if tracker is None or tool_use is None or tool_use.tool not in _EDIT_TOOLS:
        return

    try:
        _write_diff_suggestion(
            tracker,
            tool_use,
            result,
            preview,
            edited_by_user=edited_by_user,
            confirmation_automatic=confirmation_automatic,
            decision_override=decision_override,
            execution_status=execution_status,
            execution_error=execution_error,
            line_ranges_override=line_ranges_override,
        )
    except Exception as e:
        # Measurement must never prevent the edit the user just accepted.
        logger.warning("Failed to record --diff suggestion decision: %s", e)


def _write_diff_suggestion(
    tracker: _DiffSuggestionTracker,
    tool_use: ToolUse,
    result: ConfirmationResult,
    preview: str | None,
    *,
    edited_by_user: bool,
    confirmation_automatic: bool,
    decision_override: str | None,
    execution_status: str | None,
    execution_error: str | None,
    line_ranges_override: list[dict[str, int | str]] | None,
) -> None:
    """Serialize one decision; caller owns fail-open error handling."""

    from ..hooks import ConfirmAction, HookType, get_hooks

    decision = decision_override or (
        "skipped" if result.action == ConfirmAction.SKIP else "accepted"
    )
    if execution_status is None:
        execution_status = "not_run" if decision == "skipped" else "unknown"
    preview_text = preview or tool_use.preview_content or ""
    payload = json.dumps(
        {
            "tool": tool_use.tool,
            "args": tool_use.args,
            "kwargs": tool_use.kwargs,
            "content": tool_use.content,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    event = {
        "schema": "gptme.diff-suggestion.v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "suggestion_id": uuid.uuid4().hex[:16],
        "content_id": hashlib.sha256(payload.encode()).hexdigest()[:16],
        "diff_ref": tracker.ref,
        "tool": tool_use.tool,
        "call_id": tool_use.call_id,
        "targets": _targets(tool_use),
        "decision": decision,
        "execution_status": execution_status,
        "execution_error": execution_error,
        "edited_by_user": edited_by_user,
        "confirmation_mode": "automatic" if confirmation_automatic else "user",
        "confirmation_hooks": [
            hook.name for hook in get_hooks(HookType.TOOL_CONFIRM) if hook.enabled
        ],
        "line_ranges": (
            line_ranges_override
            if line_ranges_override is not None
            else diff_suggestion_line_ranges(tool_use, preview_text)
        ),
        "preview": preview_text[:_MAX_PREVIEW_CHARS],
        "preview_truncated": len(preview_text) > _MAX_PREVIEW_CHARS,
    }

    tracker.path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, sort_keys=True) + "\n"
    with tracker.lock, tracker.path.open("a", encoding="utf-8") as f:
        f.write(line)


_USER_CONFIRM_HOOKS = frozenset({"cli_confirm", "server_confirm"})


def confirmation_is_automatic() -> bool:
    """Return whether the last confirmation lacked an explicit user decision.

    Call after ``get_confirmation()``. Interactive CLI/server prompts count as
    user decisions; guardrail skips, auto-confirm, and no-hook defaults do not.
    """
    if _resolve_tracker() is None:
        return False

    try:
        from ..hooks.confirm import (
            last_confirm_hook_name,
            last_confirm_was_auto,
        )

        if last_confirm_was_auto():
            return True
        return last_confirm_hook_name() not in _USER_CONFIRM_HOOKS
    except Exception as e:
        logger.warning("Failed to inspect --diff confirmation mode: %s", e)
        return False
