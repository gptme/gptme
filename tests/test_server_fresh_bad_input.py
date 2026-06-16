"""Probe fresh API surfaces for bad-input handling.

Surfaces: SSE/events, tool confirm (auto action), audio transcription,
agent fork/creation, workspace preview, conversation edit/delete message.
"""

import io
import json
import uuid

import pytest

flask = pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)


def _bad_json_bodies():
    """Common malformed request bodies to test."""
    return [
        None,
        "",
        "not-json-at-all",
        "{{broken json]]",
        "[1, 2, 3]",  # array instead of object
        "true",  # boolean
        "42",  # number
        '"string"',  # plain string
    ]


class TestSSEEdgeCases:
    """SSE events endpoint bad-input handling."""

    def test_events_nonexistent_conversation(self, client):
        """Events for nonexistent conversation without session_id."""
        resp = client.get("/api/v2/conversations/nonexistent-xyz/events")
        assert resp.status_code == 404, (
            f"Expected 404, got {resp.status_code}: {resp.get_json()}"
        )

    def test_events_nonexistent_session(self, client):
        """Events with nonexistent session_id."""
        cid = f"test-{uuid.uuid4().hex[:12]}"
        client.put(f"/api/v2/conversations/{cid}", json={})
        resp = client.get(
            f"/api/v2/conversations/{cid}/events?session_id=nonexistent-foo"
        )
        assert resp.status_code == 404, (
            f"Expected 404, got {resp.status_code}: {resp.get_json()}"
        )

    def test_events_garbage_session_id(self, client):
        """Events with binary/garbage in session_id."""
        cid = f"test-{uuid.uuid4().hex[:12]}"
        client.put(f"/api/v2/conversations/{cid}", json={})
        for garbage in ["\x00\x01\x02", "../../../etc/passwd", "a" * 10000]:
            resp = client.get(
                f"/api/v2/conversations/{cid}/events?session_id={garbage}"
            )
            # Should get 404 (not found) or 400 (bad request), not 500
            assert resp.status_code in (400, 404), (
                f"Expected 400/404 for session_id={garbage!r}, "
                f"got {resp.status_code}: {resp.get_json()}"
            )

    def test_events_wrong_conversation_session(self, client):
        """Session that belongs to a different conversation."""
        cid1 = f"test-{uuid.uuid4().hex[:12]}"
        cid2 = f"test-{uuid.uuid4().hex[:12]}"
        client.put(f"/api/v2/conversations/{cid1}", json={})
        client.put(f"/api/v2/conversations/{cid2}", json={})

        # Create session via step on cid1
        resp = client.post(
            f"/api/v2/conversations/{cid1}/step",
            json={"session_id": "shared-sess-1", "stream": False},
        )
        # Try events from cid2 with cid1's session
        resp = client.get(
            f"/api/v2/conversations/{cid2}/events?session_id=shared-sess-1"
        )
        assert resp.status_code in (403, 404), (
            f"Expected 403/404 for wrong-conversation session, "
            f"got {resp.status_code}: {resp.get_json()}"
        )


class TestToolConfirmEdgeCases:
    """Tool confirmation endpoint bad-input handling."""

    def test_tool_confirm_auto_bad_counts(self, client):
        """Tool confirm 'auto' action with invalid count values."""
        cid = f"test-{uuid.uuid4().hex[:12]}"
        client.put(f"/api/v2/conversations/{cid}", json={})

        # Create a session
        client.post(
            f"/api/v2/conversations/{cid}/step",
            json={"session_id": "sess-tc-1", "stream": False},
        )

        bad_counts = [0, -1, 1.5, True, False, "three", {}, [], None, ""]
        for count in bad_counts:
            resp = client.post(
                f"/api/v2/conversations/{cid}/tool/confirm",
                json={"tool_id": "nonexistent-tool", "action": "auto", "count": count},
            )
            # Should get 400 (bad count) or 404 (tool not found), not 500
            assert resp.status_code in (400, 404), (
                f"Expected 400/404 for count={count!r}, "
                f"got {resp.status_code}: {resp.get_json()}"
            )

    def test_tool_confirm_bad_actions(self, client):
        """Tool confirm with unknown actions."""
        cid = f"test-{uuid.uuid4().hex[:12]}"
        client.put(f"/api/v2/conversations/{cid}", json={})

        for bad_action in ["nuke", "execute", "", "  ", "\x00", None]:
            resp = client.post(
                f"/api/v2/conversations/{cid}/tool/confirm",
                json={"tool_id": "t1", "action": bad_action},
            )
            assert resp.status_code in (400, 404), (
                f"Expected 400/404 for action={bad_action!r}, "
                f"got {resp.status_code}: {resp.get_json()}"
            )


class TestAudioTranscriptionEdgeCases:
    """Audio transcription endpoint bad-input handling."""

    def test_audio_no_file(self, client):
        """Audio transcription with no file."""
        resp = client.post("/api/v2/audio/transcriptions", data={})
        assert resp.status_code == 400, (
            f"Expected 400 for no file, got {resp.status_code}: {resp.get_json()}"
        )

    def test_audio_empty_file(self, client):
        """Audio transcription with empty file."""
        data = {"file": (io.BytesIO(b""), "test.wav")}
        resp = client.post(
            "/api/v2/audio/transcriptions",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code in (400, 413), (
            f"Expected 400/413 for empty file, got {resp.status_code}: {resp.get_json()}"
        )

    def test_audio_unsupported_format(self, client):
        """Audio transcription with unsupported format."""
        data = {"file": (io.BytesIO(b"\x00\x01\x02\x03"), "test.exe")}
        resp = client.post(
            "/api/v2/audio/transcriptions",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400, (
            f"Expected 400 for unsupported format, got {resp.status_code}: {resp.get_json()}"
        )

    def test_audio_bad_language_code(self, client):
        """Audio transcription with excessively long language code."""
        data = {"file": (io.BytesIO(b"\x00" * 1024), "test.wav"), "language": "x" * 100}
        resp = client.post(
            "/api/v2/audio/transcriptions",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code in (400, 413), (
            f"Expected 400/413 for bad language code, got {resp.status_code}: {resp.get_json()}"
        )


class TestConversationMutationEdgeCases:
    """Conversation edit/delete message endpoints."""

    def test_edit_message_bad_index(self, client):
        """Edit message with invalid index."""
        cid = f"test-{uuid.uuid4().hex[:12]}"
        client.put(f"/api/v2/conversations/{cid}", json={})

        # Edit message at non-existent index
        resp = client.patch(
            f"/api/v2/conversations/{cid}/messages/999",
            json={"content": "edited"},
        )
        assert resp.status_code in (400, 404), (
            f"Expected 400/404 for bad index, got {resp.status_code}: {resp.get_json()}"
        )

    def test_delete_message_bad_index(self, client):
        """Delete message with invalid index."""
        cid = f"test-{uuid.uuid4().hex[:12]}"
        client.put(f"/api/v2/conversations/{cid}", json={})

        resp = client.delete(f"/api/v2/conversations/{cid}/messages/999")
        assert resp.status_code in (400, 404), (
            f"Expected 400/404 for bad delete index, got {resp.status_code}: {resp.get_json()}"
        )

    def test_edit_message_bad_body(self, client):
        """Edit message with bad request body."""
        cid = f"test-{uuid.uuid4().hex[:12]}"
        client.put(f"/api/v2/conversations/{cid}", json={})
        # Add a message first
        client.post(
            f"/api/v2/conversations/{cid}", json={"role": "user", "content": "hello"}
        )

        for body in _bad_json_bodies():
            resp = client.patch(
                f"/api/v2/conversations/{cid}/messages/0",
                data=json.dumps(body) if body is not None else None,
                content_type="application/json",
            )
            assert resp.status_code in (400, 404, 405), (
                f"Expected 400/404/405 for body={body!r}, "
                f"got {resp.status_code}: {resp.get_json()}"
            )


class TestWorkspaceEdgeCases:
    """Workspace endpoint edge cases."""

    def test_workspace_browse_garbage(self, client):
        """Workspace browse with garbage paths."""
        cid = f"test-{uuid.uuid4().hex[:12]}"
        client.put(f"/api/v2/conversations/{cid}", json={})

        garbage_paths = [
            "/dev/null",
            "/proc/self/environ",
            "//etc//passwd",
            "\x00\x01\x02",
            "a" * 10000,  # overly long path
        ]
        for path in garbage_paths:
            resp = client.get(f"/api/v2/conversations/{cid}/workspace/{path}")
            # Should not 500
            assert resp.status_code != 500, (
                f"500 for workspace path={path!r}: {resp.get_json()}"
            )


class TestAgentCreationEdgeCases:
    """Agent creation endpoint bad-input handling."""

    def test_agent_create_missing_fields(self, client):
        """Agent create without required fields."""
        # No body at all
        resp = client.put("/api/v2/agents", data=None, content_type="application/json")
        assert resp.status_code in (400, 415), (
            f"Expected 400/415 for no body, got {resp.status_code}: {resp.get_json()}"
        )
        # Empty object
        resp = client.put("/api/v2/agents", json={})
        assert resp.status_code == 400, (
            f"Expected 400 for empty body, got {resp.status_code}: {resp.get_json()}"
        )

    def test_agent_create_bad_types(self, client):
        """Agent create with bad field types."""
        for bad_name in [42, True, [], {}, None]:
            resp = client.put("/api/v2/agents", json={"name": bad_name})
            assert resp.status_code == 400, (
                f"Expected 400 for name={bad_name!r}, "
                f"got {resp.status_code}: {resp.get_json()}"
            )

    def test_agent_create_bad_json(self, client):
        """Agent create with malformed JSON."""
        for body in _bad_json_bodies():
            if body is None:
                resp = client.put(
                    "/api/v2/agents",
                    data=None,
                    content_type="application/json",
                )
            else:
                resp = client.put(
                    "/api/v2/agents",
                    data=json.dumps(body),
                    content_type="application/json",
                )
            assert resp.status_code in (400, 415), (
                f"Expected 400/415 for body={body!r}, "
                f"got {resp.status_code}: {resp.get_json()}"
            )
