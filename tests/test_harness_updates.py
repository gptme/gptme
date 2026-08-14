from __future__ import annotations

import unittest.mock
from datetime import timezone

import pytest
import requests

from gptme.harness import (
    HarnessUpdateError,
    HarnessUpdateRequest,
    SessionHarnessState,
    annotate_message_with_harness_updates,
    extract_harness_updates,
)
from gptme.message import Message


def test_extract_harness_updates_parses_valid_request():
    content = (
        "Planning.\n"
        'HARNESS_UPDATE: enable_tool web_fetch reason="Need fresh docs" '
        "urgency=medium approval=auto\n"
    )

    requests_, errors = extract_harness_updates(
        content, available_tool_names={"web_fetch", "shell"}
    )

    assert errors == []
    assert requests_ == [
        HarnessUpdateRequest(
            change_type="enable_tool",
            tool_name="web_fetch",
            reason="Need fresh docs",
            urgency="medium",
            approval_mode="auto",
            raw_line='HARNESS_UPDATE: enable_tool web_fetch reason="Need fresh docs" urgency=medium approval=auto',
            extra={},
        )
    ]


def test_extract_harness_updates_rejects_unknown_tool():
    content = (
        'HARNESS_UPDATE: enable_tool made_up_tool reason="Need it" '
        "urgency=medium approval=auto"
    )

    requests_, errors = extract_harness_updates(
        content, available_tool_names={"web_fetch", "shell"}
    )

    assert requests_ == []
    assert errors == [
        HarnessUpdateError(
            raw_line='HARNESS_UPDATE: enable_tool made_up_tool reason="Need it" urgency=medium approval=auto',
            error="unknown tool 'made_up_tool'",
        )
    ]


def test_extract_harness_updates_rejects_malformed_tokens():
    content = (
        'HARNESS_UPDATE: enable_tool shell reason="Need shell" medium approval=auto'
    )

    requests_, errors = extract_harness_updates(
        content, available_tool_names={"shell", "web_fetch"}
    )

    assert requests_ == []
    assert errors == [
        HarnessUpdateError(
            raw_line='HARNESS_UPDATE: enable_tool shell reason="Need shell" medium approval=auto',
            error="unexpected token 'medium' (expected key=value)",
        )
    ]


def test_extract_harness_updates_accepts_unquoted_apostrophe_in_reason():
    """shlex.split raises ValueError on unquoted apostrophes; we fall back to
    plain whitespace splitting so natural-language reasons are not silently
    dropped from the audit trail."""
    content = (
        "HARNESS_UPDATE: enable_tool shell reason=user's urgency=low approval=auto"
    )

    requests_, errors = extract_harness_updates(
        content, available_tool_names={"shell", "web_fetch"}
    )

    assert errors == []
    assert len(requests_) == 1
    assert requests_[0].reason == "user's"


def test_extract_harness_updates_accepts_apostrophe_with_space_in_reason():
    """The shlex fallback must also handle reasons with both an apostrophe and a
    space (e.g. ``reason=user's request``).  Without the re-join step the bare
    word ``request`` would be mis-classified as an unexpected token and the whole
    line would be rejected instead of recorded."""
    content = "HARNESS_UPDATE: enable_tool shell reason=user's request urgency=low approval=auto"

    requests_, errors = extract_harness_updates(
        content, available_tool_names={"shell", "web_fetch"}
    )

    assert errors == [], f"Unexpected errors: {errors}"
    assert len(requests_) == 1
    assert requests_[0].reason == "user's request"
    assert requests_[0].urgency == "low"
    assert requests_[0].approval_mode == "auto"


def test_annotate_message_with_harness_updates_preserves_existing_metadata():
    msg = Message(
        "assistant",
        'HARNESS_UPDATE: disable_tool shell reason="Done coding" urgency=low approval=log_only',
        metadata={"model": "openai/mock-model"},
    )
    state = SessionHarnessState()

    annotated, requests_, errors = annotate_message_with_harness_updates(
        msg, session_harness=state, available_tool_names={"shell", "web_fetch"}
    )

    assert errors == []
    assert len(requests_) == 1
    assert annotated.metadata is not None
    assert annotated.metadata["model"] == "openai/mock-model"
    assert annotated.metadata["harness_updates"][0]["tool_name"] == "shell"
    assert state.requests == requests_
    # annotate_message_with_harness_updates normalizes naive timestamps to UTC,
    # so last_update_at is the UTC-aware equivalent of the message timestamp.
    assert state.last_update_at == annotated.timestamp.replace(tzinfo=timezone.utc)


def test_server_step_attaches_harness_update_metadata(
    setup_conversation, mock_generation, auth_headers
):
    port, conversation_id, session_id = setup_conversation

    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Plan this task"},
        headers=auth_headers,
    )

    assistant_reply = (
        "I should narrow the harness.\n"
        'HARNESS_UPDATE: disable_tool shell reason="Task is pure analysis" '
        "urgency=low approval=log_only"
    )

    with unittest.mock.patch(
        "gptme.server.session_step._stream", mock_generation([assistant_reply])
    ):
        step_resp = requests.post(
            f"http://localhost:{port}/api/v2/conversations/{conversation_id}/step",
            json={"session_id": session_id, "model": "openai/mock-model"},
            headers=auth_headers,
        )
        assert step_resp.status_code == 200

    resp = requests.get(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200

    assistant_messages = [m for m in resp.json()["log"] if m["role"] == "assistant"]
    assert assistant_messages
    metadata = assistant_messages[-1]["metadata"]
    assert metadata["harness_updates"][0]["change_type"] == "disable_tool"
    assert metadata["harness_updates"][0]["tool_name"] == "shell"
    assert metadata["harness_updates"][0]["approval_mode"] == "log_only"


def test_extract_harness_updates_accepts_known_tool_via_module_discovery():
    """enable_tool requests must pass for tools that exist in gptme's module system
    even when they are not currently loaded in the session.

    The production path (no explicit available_tool_names) discovers all tools via
    get_available_tools(), which includes tools not loaded for the current session.
    This is the core invariant: enable_tool must name a *known* tool, not a
    *currently-enabled* one — otherwise the audit could never capture enable requests.
    """
    from gptme.tools import get_available_tools

    all_known = {t.name for t in get_available_tools()}
    if not all_known:
        pytest.skip("no tools discoverable in this environment")

    # Pick any known tool name; the request should be accepted via the default
    # fallback to _get_all_known_tool_names() even if it is not in the active session.
    any_known_tool = next(iter(sorted(all_known)))

    content = (
        f"HARNESS_UPDATE: enable_tool {any_known_tool} "
        f'reason="Needs tool for next step" urgency=medium approval=auto\n'
    )
    # No available_tool_names override → uses full module discovery.
    requests_, errors = extract_harness_updates(content)

    assert errors == [], f"Unexpected errors: {errors}"
    assert len(requests_) == 1
    assert requests_[0].tool_name == any_known_tool
    assert requests_[0].change_type == "enable_tool"


def test_extract_harness_updates_rejects_unterminated_quote_in_fallback_path():
    """An assistant line with an unterminated quoted value triggers the shlex
    ValueError fallback.  After the fallback to whitespace splitting the
    malformed token (e.g. ``reason="unterminated``) must be detected and
    rejected, not recorded with a corrupted reason value."""
    content = 'HARNESS_UPDATE: enable_tool shell reason="unterminated urgency=low approval=auto'

    requests_, errors = extract_harness_updates(
        content, available_tool_names={"shell", "web_fetch"}
    )

    assert requests_ == [], f"Expected no valid requests, got: {requests_}"
    assert len(errors) == 1
    assert "unterminated" in errors[0].error


def test_extract_harness_updates_rejects_empty_payload():
    """A bare HARNESS_UPDATE: line with no following text is rejected."""
    content = "HARNESS_UPDATE:"
    requests_, errors = extract_harness_updates(content, available_tool_names={"shell"})
    assert requests_ == []
    assert len(errors) == 1
    assert errors[0].error == "missing update payload"


def test_extract_harness_updates_rejects_missing_reason():
    """A HARNESS_UPDATE line that omits reason= is rejected."""
    content = "HARNESS_UPDATE: enable_tool shell urgency=medium approval=auto"
    requests_, errors = extract_harness_updates(content, available_tool_names={"shell"})
    assert requests_ == []
    assert len(errors) == 1
    assert errors[0].error == "missing reason=<text>"


def test_extract_harness_updates_rejects_invalid_urgency():
    """A HARNESS_UPDATE line with an unrecognised urgency value is rejected."""
    content = (
        'HARNESS_UPDATE: enable_tool shell reason="Needs it" '
        "urgency=critical approval=auto"
    )
    requests_, errors = extract_harness_updates(content, available_tool_names={"shell"})
    assert requests_ == []
    assert len(errors) == 1
    assert errors[0].error == "urgency must be one of: low, medium, high"


def test_extract_harness_updates_rejects_invalid_approval():
    """A HARNESS_UPDATE line with an unrecognised approval value is rejected."""
    content = (
        'HARNESS_UPDATE: enable_tool shell reason="Needs it" '
        "urgency=medium approval=manager_confirm"
    )
    requests_, errors = extract_harness_updates(content, available_tool_names={"shell"})
    assert requests_ == []
    assert len(errors) == 1
    assert errors[0].error == "approval must be one of: auto, log_only, user_confirm"
