from __future__ import annotations

import unittest.mock

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
    assert state.last_update_at == annotated.timestamp


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
