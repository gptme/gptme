"""Tests for V2 session API endpoints.

Tests validation paths, error handling, and request/response contracts
for step, interrupt, rerun, elicit/respond, and tool/confirm endpoints.
These are unit-level tests using the Flask test client — they don't
require API keys or LLM calls.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

# Skip if flask not installed
pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

from gptme.server.session_models import (  # fmt: skip
    SessionManager,
    ToolExecution,
)
from gptme.tools import ToolUse  # fmt: skip

pytestmark = [pytest.mark.timeout(10)]


def create_conversation(client: FlaskClient) -> dict:
    """Create a V2 conversation with a session."""
    convname = f"test-sessions-{uuid.uuid4().hex[:8]}"
    response = client.put(
        f"/api/v2/conversations/{convname}",
        json={"prompt": "You are an AI assistant for testing."},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    return {"conversation_id": convname, "session_id": data["session_id"]}


@pytest.fixture
def conv(client: FlaskClient):
    """Create a conversation with session."""
    return create_conversation(client)


# --- Step endpoint tests ---


class TestStepEndpoint:
    """Test POST /api/v2/conversations/<id>/step validation."""

    def test_missing_session_id(self, conv, client: FlaskClient):
        """Step without session_id returns 400."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/step",
            json={},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "session_id" in data["error"]

    def test_invalid_session_id(self, conv, client: FlaskClient):
        """Step with nonexistent session_id returns 404."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/step",
            json={"session_id": "nonexistent-session-id"},
        )
        assert response.status_code == 404

    def test_invalid_use_acp_type(self, conv, client: FlaskClient):
        """Step with non-boolean use_acp returns 400."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/step",
            json={
                "session_id": conv["session_id"],
                "use_acp": "true",  # string, not bool
            },
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "use_acp" in data["error"]

    def test_invalid_auto_confirm_type(self, conv, client: FlaskClient):
        """Step with invalid auto_confirm type returns 400."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/step",
            json={
                "session_id": conv["session_id"],
                "auto_confirm": "yes",  # string, not bool/int
            },
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "auto_confirm" in data["error"]

    def test_no_model_returns_400(self, conv, client: FlaskClient):
        """Step without model when no default model set returns 400."""
        with (
            patch("gptme.server.api_v2_sessions.get_default_model", return_value=None),
            patch(
                "gptme.server.api_v2_sessions.ChatConfig.load_or_create"
            ) as mock_config,
            patch(
                "gptme.server.api_v2_sessions.Config.from_workspace"
            ) as mock_ws_config,
        ):
            from gptme.config import ChatConfig

            cfg = ChatConfig()
            cfg.model = None
            mock_config.return_value = cfg
            mock_ws_config.return_value = MagicMock(
                get_env=MagicMock(return_value=None)
            )

            response = client.post(
                f"/api/v2/conversations/{conv['conversation_id']}/step",
                json={"session_id": conv["session_id"]},
            )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "model" in data["error"].lower()

    def test_generation_already_in_progress(self, conv, client: FlaskClient):
        """Step while already generating returns 409."""
        # Set the session to generating state
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        session.generating = True

        try:
            response = client.post(
                f"/api/v2/conversations/{conv['conversation_id']}/step",
                json={
                    "session_id": conv["session_id"],
                    "model": "test/model",
                },
            )
            assert response.status_code == 409
            data = response.get_json()
            assert data is not None
            assert "already in progress" in data["error"].lower()
        finally:
            session.generating = False


# --- Interrupt endpoint tests ---


@pytest.mark.parametrize(
    "endpoint", ["step", "tool/confirm", "rerun", "elicit/respond", "interrupt"]
)
@pytest.mark.parametrize(
    "body",
    [
        [],
        [1, 2, 3],
        "string",
        42,
    ],
)
def test_session_endpoints_reject_non_object_json(
    conv, client: FlaskClient, endpoint: str, body: object
):
    """Session endpoints should reject non-object JSON bodies with 400."""
    response = client.post(
        f"/api/v2/conversations/{conv['conversation_id']}/{endpoint}",
        json=body,
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "JSON body must be an object"}


class TestInterruptEndpoint:
    """Test POST /api/v2/conversations/<id>/interrupt validation."""

    def test_missing_session_id(self, conv, client: FlaskClient):
        """Interrupt without session_id returns 400."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/interrupt",
            json={},
        )
        assert response.status_code == 400

    def test_invalid_session_id(self, conv, client: FlaskClient):
        """Interrupt with nonexistent session returns 404."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/interrupt",
            json={"session_id": "nonexistent"},
        )
        assert response.status_code == 404

    def test_interrupt_when_not_generating(self, conv, client: FlaskClient):
        """Interrupt when not generating is idempotent (returns 200)."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/interrupt",
            json={"session_id": conv["session_id"]},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data is not None
        assert "already interrupted" in data["message"].lower()

    def test_interrupt_clears_generating_flag(self, conv, client: FlaskClient):
        """Interrupt sets generating=False and clears pending tools."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        session.generating = True
        session.pending_tools["fake-tool"] = ToolExecution(
            tool_id="fake-tool",
            tooluse=ToolUse("bash", [], "echo hi"),
        )

        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/interrupt",
            json={"session_id": conv["session_id"]},
        )

        assert response.status_code == 200
        assert session.generating is False
        assert len(session.pending_tools) == 0


# --- Tool confirm endpoint tests ---


class TestToolConfirmEndpoint:
    """Test POST /api/v2/conversations/<id>/tool/confirm validation."""

    def test_missing_tool_id_and_action(self, conv, client: FlaskClient):
        """Confirm without tool_id and action returns 400."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/tool/confirm",
            json={"session_id": conv["session_id"]},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "tool_id" in data["error"]

    def test_missing_action(self, conv, client: FlaskClient):
        """Confirm without action returns 400."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/tool/confirm",
            json={
                "session_id": conv["session_id"],
                "tool_id": "some-tool",
            },
        )
        assert response.status_code == 400

    def test_tool_not_found_in_session(self, conv, client: FlaskClient):
        """Confirm with unknown tool_id in specific session returns 404."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/tool/confirm",
            json={
                "session_id": conv["session_id"],
                "tool_id": "nonexistent-tool",
                "action": "confirm",
            },
        )
        assert response.status_code == 404

    def test_tool_not_found_without_session(self, conv, client: FlaskClient):
        """Confirm without session_id, tool not found in any session, returns 404."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/tool/confirm",
            json={
                "tool_id": "nonexistent-tool",
                "action": "confirm",
            },
        )
        assert response.status_code == 404

    def test_unknown_action(self, conv, client: FlaskClient):
        """Confirm with unknown action returns 400."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        tool_id = str(uuid.uuid4())
        session.pending_tools[tool_id] = ToolExecution(
            tool_id=tool_id,
            tooluse=ToolUse("bash", [], "echo test"),
        )

        try:
            response = client.post(
                f"/api/v2/conversations/{conv['conversation_id']}/tool/confirm",
                json={
                    "session_id": conv["session_id"],
                    "tool_id": tool_id,
                    "action": "invalid_action",
                },
            )
            assert response.status_code == 400
            data = response.get_json()
            assert data is not None
            assert "unknown action" in data["error"].lower()
        finally:
            session.pending_tools.pop(tool_id, None)

    def test_skip_action(self, conv, client: FlaskClient):
        """Skip action removes pending tool and appends system message."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        tool_id = str(uuid.uuid4())
        session.pending_tools[tool_id] = ToolExecution(
            tool_id=tool_id,
            tooluse=ToolUse("bash", [], "rm -rf /"),
        )

        with (
            patch("gptme.server.api_v2_sessions._append_and_notify"),
            patch("gptme.server.api_v2_sessions._start_step_thread"),
        ):
            response = client.post(
                f"/api/v2/conversations/{conv['conversation_id']}/tool/confirm",
                json={
                    "session_id": conv["session_id"],
                    "tool_id": tool_id,
                    "action": "skip",
                },
            )

        assert response.status_code == 200
        assert tool_id not in session.pending_tools

    def test_edit_requires_content(self, conv, client: FlaskClient):
        """Edit action without content returns 400."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        tool_id = str(uuid.uuid4())
        session.pending_tools[tool_id] = ToolExecution(
            tool_id=tool_id,
            tooluse=ToolUse("bash", [], "echo old"),
        )

        try:
            response = client.post(
                f"/api/v2/conversations/{conv['conversation_id']}/tool/confirm",
                json={
                    "session_id": conv["session_id"],
                    "tool_id": tool_id,
                    "action": "edit",
                    # content intentionally omitted
                },
            )
            assert response.status_code == 400
            data = response.get_json()
            assert data is not None
            assert "content" in data["error"].lower()
        finally:
            session.pending_tools.pop(tool_id, None)

    def test_auto_with_invalid_count(self, conv, client: FlaskClient):
        """Auto action with count <= 0 returns 400."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        tool_id = str(uuid.uuid4())
        session.pending_tools[tool_id] = ToolExecution(
            tool_id=tool_id,
            tooluse=ToolUse("bash", [], "echo test"),
        )

        try:
            response = client.post(
                f"/api/v2/conversations/{conv['conversation_id']}/tool/confirm",
                json={
                    "session_id": conv["session_id"],
                    "tool_id": tool_id,
                    "action": "auto",
                    "count": 0,
                },
            )
            assert response.status_code == 400
            data = response.get_json()
            assert data is not None
            assert "count" in data["error"].lower()
        finally:
            session.pending_tools.pop(tool_id, None)

    def test_session_not_found(self, conv, client: FlaskClient):
        """Confirm with nonexistent session_id returns 404."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/tool/confirm",
            json={
                "session_id": "nonexistent",
                "tool_id": "some-tool",
                "action": "confirm",
            },
        )
        assert response.status_code == 404

    def test_find_tool_across_sessions(self, conv, client: FlaskClient):
        """Without session_id, finds tool across all sessions for conversation."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        tool_id = str(uuid.uuid4())
        session.pending_tools[tool_id] = ToolExecution(
            tool_id=tool_id,
            tooluse=ToolUse("bash", [], "echo cross-session"),
        )

        try:
            with (
                patch("gptme.server.api_v2_sessions.start_tool_execution"),
                patch("gptme.server.api_v2_sessions.resolve_hook_confirmation"),
            ):
                response = client.post(
                    f"/api/v2/conversations/{conv['conversation_id']}/tool/confirm",
                    json={
                        # No session_id — should find tool by scanning sessions
                        "tool_id": tool_id,
                        "action": "confirm",
                    },
                )

            assert response.status_code == 200
        finally:
            session.pending_tools.pop(tool_id, None)


# --- Rerun endpoint tests ---


class TestRerunEndpoint:
    """Test POST /api/v2/conversations/<id>/rerun."""

    def test_missing_session_id(self, conv, client: FlaskClient):
        """Rerun without session_id returns 400."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/rerun",
            json={},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "session_id" in data["error"]

    def test_invalid_session_id(self, conv, client: FlaskClient):
        """Rerun with nonexistent session returns 404."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/rerun",
            json={"session_id": "nonexistent"},
        )
        assert response.status_code == 404

    def test_rerun_while_generating(self, conv, client: FlaskClient):
        """Rerun while generation in progress returns 409."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        session.generating = True

        try:
            response = client.post(
                f"/api/v2/conversations/{conv['conversation_id']}/rerun",
                json={"session_id": conv["session_id"]},
            )
            assert response.status_code == 409
        finally:
            session.generating = False

    def test_rerun_no_assistant_message(self, conv, client: FlaskClient):
        """Rerun with no assistant message returns 400."""
        # The conversation was just created with only a system message
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/rerun",
            json={"session_id": conv["session_id"]},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "no assistant message" in data["error"].lower()

    def test_rerun_no_tool_uses_in_message(self, conv, client: FlaskClient):
        """Rerun with assistant message that has no tool uses returns 400."""
        conversation_id = conv["conversation_id"]

        # Add a user message
        client.post(
            f"/api/v2/conversations/{conversation_id}",
            json={"role": "user", "content": "Hello"},
        )
        # Add an assistant message with no tool uses
        client.post(
            f"/api/v2/conversations/{conversation_id}",
            json={"role": "assistant", "content": "Hi there! How can I help?"},
        )

        response = client.post(
            f"/api/v2/conversations/{conversation_id}/rerun",
            json={"session_id": conv["session_id"]},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "no tool uses" in data["error"].lower()

    def test_rerun_with_tool_uses(self, conv, client: FlaskClient):
        """Rerun with assistant message containing tool uses creates pending tools."""
        conversation_id = conv["conversation_id"]

        # Add a user message
        client.post(
            f"/api/v2/conversations/{conversation_id}",
            json={"role": "user", "content": "List files"},
        )
        # Add an assistant message with a tool use (bash codeblock)
        client.post(
            f"/api/v2/conversations/{conversation_id}",
            json={
                "role": "assistant",
                "content": "Let me list the files:\n```shell\nls -la\n```",
            },
        )

        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        initial_pending = len(session.pending_tools)

        try:
            response = client.post(
                f"/api/v2/conversations/{conversation_id}/rerun",
                json={"session_id": conv["session_id"]},
            )

            assert response.status_code == 200
            data = response.get_json()
            assert data is not None
            assert data["status"] == "ok"
            assert "re-running" in data["message"].lower()
            assert "tool_ids" in data
            assert len(data["tool_ids"]) > initial_pending
        finally:
            session.pending_tools.clear()

    def test_rerun_nonexistent_conversation(self, conv, client: FlaskClient):
        """Rerun on nonexistent conversation returns 404."""
        response = client.post(
            "/api/v2/conversations/nonexistent-conv-id/rerun",
            json={"session_id": conv["session_id"]},
        )
        assert response.status_code == 404


# --- Elicit respond endpoint tests ---


class TestElicitRespondEndpoint:
    """Test POST /api/v2/conversations/<id>/elicit/respond validation."""

    def test_missing_elicit_id(self, conv, client: FlaskClient):
        """Respond without elicit_id returns 400."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/elicit/respond",
            json={"action": "accept"},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "elicit_id" in data["error"]

    def test_missing_action(self, conv, client: FlaskClient):
        """Respond without action returns 400."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/elicit/respond",
            json={"elicit_id": "some-id"},
        )
        assert response.status_code == 400

    def test_invalid_action(self, conv, client: FlaskClient):
        """Respond with invalid action returns 400."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/elicit/respond",
            json={"elicit_id": "some-id", "action": "invalid"},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "unknown action" in data["error"].lower()

    @patch("gptme.server.api_v2_sessions.resolve_hook_elicitation")
    def test_accept_action(self, mock_resolve, conv, client: FlaskClient):
        """Accept action calls resolve_hook_elicitation correctly."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/elicit/respond",
            json={
                "elicit_id": "test-elicit-id",
                "action": "accept",
                "value": "user input",
            },
        )
        assert response.status_code == 200
        mock_resolve.assert_called_once_with(
            "test-elicit-id", "accept", "user input", None
        )

    @patch("gptme.server.api_v2_sessions.resolve_hook_elicitation")
    def test_decline_action(self, mock_resolve, conv, client: FlaskClient):
        """Decline action works correctly."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/elicit/respond",
            json={"elicit_id": "test-id", "action": "decline"},
        )
        assert response.status_code == 200
        mock_resolve.assert_called_once_with("test-id", "decline", None, None)

    @patch("gptme.server.api_v2_sessions.resolve_hook_elicitation")
    def test_cancel_action(self, mock_resolve, conv, client: FlaskClient):
        """Cancel action works correctly."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/elicit/respond",
            json={"elicit_id": "test-id", "action": "cancel"},
        )
        assert response.status_code == 200
        mock_resolve.assert_called_once_with("test-id", "cancel", None, None)

    @patch("gptme.server.api_v2_sessions.resolve_hook_elicitation")
    def test_values_passed_through(self, mock_resolve, conv, client: FlaskClient):
        """Multi-choice values are passed through correctly."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/elicit/respond",
            json={
                "elicit_id": "multi-id",
                "action": "accept",
                "values": {"option_a": True, "option_b": False},
            },
        )
        assert response.status_code == 200
        mock_resolve.assert_called_once_with(
            "multi-id",
            "accept",
            None,
            {"option_a": True, "option_b": False},
        )


# --- Events endpoint tests ---


class TestEventsEndpoint:
    """Test GET /api/v2/conversations/<id>/events validation."""

    def test_invalid_session_id_returns_404(self, conv, client: FlaskClient):
        """Events with nonexistent session_id returns 404."""
        response = client.get(
            f"/api/v2/conversations/{conv['conversation_id']}/events?session_id=nonexistent"
        )
        assert response.status_code == 404

    def test_no_session_id_creates_session(self, conv, client: FlaskClient):
        """Events without session_id creates a new session and streams."""
        response = client.get(f"/api/v2/conversations/{conv['conversation_id']}/events")
        # SSE endpoint returns 200 with streaming content
        assert response.status_code == 200
        assert response.content_type.startswith("text/event-stream")


class TestConversationGetSessionState:
    """Test GET /api/v2/conversations/<id> exposes session state.

    REST polling clients need to see generation status and the last step
    error without subscribing to SSE — otherwise they can't tell that an
    LLM call failed (issue gptme/gptme-cloud#172).
    """

    def test_session_field_present_when_session_exists(self, conv, client: FlaskClient):
        """GET conversation includes session.id, generating, last_error."""
        response = client.get(f"/api/v2/conversations/{conv['conversation_id']}")
        assert response.status_code == 200
        data = response.get_json()
        assert "session" in data
        assert data["session"]["id"] == conv["session_id"]
        assert data["session"]["generating"] is False
        assert data["session"]["last_error"] is None

    def test_last_error_surfaces_in_get_response(self, conv, client: FlaskClient):
        """A session.last_error set by a failed step is visible via GET."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        session.last_error = "LLM call failed: rate limit exceeded"

        response = client.get(f"/api/v2/conversations/{conv['conversation_id']}")
        assert response.status_code == 200
        data = response.get_json()
        assert data["session"]["last_error"] == "LLM call failed: rate limit exceeded"

    def test_session_field_omitted_when_no_session(self, client: FlaskClient):
        """Conversations without an active session omit the session field."""
        # Create a conversation via PUT, then manually remove the session to simulate no-session case
        convname = f"test-no-session-{uuid.uuid4().hex[:8]}"
        response = client.put(
            f"/api/v2/conversations/{convname}",
            json={"prompt": "test"},
        )
        assert response.status_code == 200
        # Manually remove the session to simulate the no-session case
        session_id = response.get_json()["session_id"]
        SessionManager.remove_session(session_id)

        response = client.get(f"/api/v2/conversations/{convname}")
        assert response.status_code == 200
        data = response.get_json()
        assert "session" not in data
