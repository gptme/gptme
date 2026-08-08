"""Tests for V2 session API endpoints.

Tests validation paths, error handling, and request/response contracts
for step, interrupt, rerun, elicit/respond, and tool/confirm endpoints.
These are unit-level tests using the Flask test client — they don't
require API keys or LLM calls.
"""

import threading
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


@pytest.fixture
def conv_pair(client: FlaskClient):
    """Create two conversations with sessions for cross-conversation tests."""
    conv1 = create_conversation(client)
    conv2 = create_conversation(client)
    return conv1, conv2


# --- Cross-conversation ownership tests ---


class TestCrossConversationOwnership:
    """Session from conversation A must be rejected on conversation B's endpoints."""

    def test_step_cross_conversation_rejected(self, conv_pair, client: FlaskClient):
        """Using a session from conv A on conv B's step endpoint returns 403."""
        conv1, conv2 = conv_pair
        response = client.post(
            f"/api/v2/conversations/{conv2['conversation_id']}/step",
            json={"session_id": conv1["session_id"]},
        )
        assert response.status_code == 403
        assert "does not belong to conversation" in response.get_json()["error"]

    def test_events_cross_conversation_rejected(self, conv_pair, client: FlaskClient):
        """Using a session from conv A on conv B's events endpoint returns 403."""
        conv1, conv2 = conv_pair
        response = client.get(
            f"/api/v2/conversations/{conv2['conversation_id']}/events"
            f"?session_id={conv1['session_id']}"
        )
        assert response.status_code == 403
        assert "does not belong to conversation" in response.get_json()["error"]

    def test_tool_confirm_cross_conversation_rejected(
        self, conv_pair, client: FlaskClient
    ):
        """Using a session from conv A on conv B's tool/confirm returns 403."""
        conv1, conv2 = conv_pair
        response = client.post(
            f"/api/v2/conversations/{conv2['conversation_id']}/tool/confirm",
            json={
                "session_id": conv1["session_id"],
                "tool_id": "some-id",
                "action": "confirm",
            },
        )
        assert response.status_code == 403
        assert "does not belong to conversation" in response.get_json()["error"]

    def test_rerun_cross_conversation_rejected(self, conv_pair, client: FlaskClient):
        """Using a session from conv A on conv B's rerun endpoint returns 403."""
        conv1, conv2 = conv_pair
        response = client.post(
            f"/api/v2/conversations/{conv2['conversation_id']}/rerun",
            json={"session_id": conv1["session_id"]},
        )
        assert response.status_code == 403
        assert "does not belong to conversation" in response.get_json()["error"]

    def test_interrupt_cross_conversation_rejected(
        self, conv_pair, client: FlaskClient
    ):
        """Using a session from conv A on conv B's interrupt returns 403."""
        conv1, conv2 = conv_pair
        response = client.post(
            f"/api/v2/conversations/{conv2['conversation_id']}/interrupt",
            json={"session_id": conv1["session_id"]},
        )
        assert response.status_code == 403
        assert "does not belong to conversation" in response.get_json()["error"]

    def test_step_nonexistent_conversation_404(self, conv_pair, client: FlaskClient):
        """Valid session + nonexistent conversation path returns 404, not 403."""
        conv1, _ = conv_pair
        response = client.post(
            "/api/v2/conversations/nonexistent-conv-id/step",
            json={"session_id": conv1["session_id"]},
        )
        assert response.status_code == 404

    def test_events_nonexistent_conversation_404(self, conv_pair, client: FlaskClient):
        """Valid session + nonexistent conversation path returns 404, not 403."""
        conv1, _ = conv_pair
        response = client.get(
            "/api/v2/conversations/nonexistent-conv-id/events"
            f"?session_id={conv1['session_id']}"
        )
        assert response.status_code == 404

    def test_tool_confirm_nonexistent_conversation_404(
        self, conv_pair, client: FlaskClient
    ):
        """Valid session + nonexistent conversation path returns 404, not 403."""
        conv1, _ = conv_pair
        response = client.post(
            "/api/v2/conversations/nonexistent-conv-id/tool/confirm",
            json={
                "session_id": conv1["session_id"],
                "tool_id": "some-id",
                "action": "confirm",
            },
        )
        assert response.status_code == 404

    def test_interrupt_nonexistent_conversation_404(
        self, conv_pair, client: FlaskClient
    ):
        """Valid session + nonexistent conversation path returns 404, not 403."""
        conv1, _ = conv_pair
        response = client.post(
            "/api/v2/conversations/nonexistent-conv-id/interrupt",
            json={"session_id": conv1["session_id"]},
        )
        assert response.status_code == 404


# --- Nonexistent conversation tests for endpoints missing coverage ---


class TestStepNonexistentConversation:
    """Step endpoint: nonexistent conversation returns 404."""

    def test_step_nonexistent_conversation_returns_404(self, conv, client: FlaskClient):
        """Step on nonexistent conversation returns 404."""
        response = client.post(
            "/api/v2/conversations/nonexistent-conv-id/step",
            json={"session_id": conv["session_id"]},
        )
        assert response.status_code == 404


class TestEventsNonexistentConversation:
    """Events endpoint: nonexistent conversation returns 404."""

    def test_events_nonexistent_conversation_returns_404(
        self, conv, client: FlaskClient
    ):
        """Events on nonexistent conversation returns 404."""
        response = client.get(
            "/api/v2/conversations/nonexistent-conv-id/events"
            f"?session_id={conv['session_id']}"
        )
        assert response.status_code == 404


class TestToolConfirmNonexistentConversation:
    """Tool/confirm endpoint: nonexistent conversation returns 404."""

    def test_tool_confirm_nonexistent_conversation_returns_404(
        self, conv, client: FlaskClient
    ):
        """Tool/confirm on nonexistent conversation returns 404."""
        response = client.post(
            "/api/v2/conversations/nonexistent-conv-id/tool/confirm",
            json={
                "session_id": conv["session_id"],
                "tool_id": "some-id",
                "action": "confirm",
            },
        )
        assert response.status_code == 404


class TestInterruptNonexistentConversation:
    """Interrupt endpoint: nonexistent conversation returns 404."""

    def test_interrupt_nonexistent_conversation_returns_404(
        self, conv, client: FlaskClient
    ):
        """Interrupt on nonexistent conversation returns 404."""
        response = client.post(
            "/api/v2/conversations/nonexistent-conv-id/interrupt",
            json={"session_id": conv["session_id"]},
        )
        assert response.status_code == 404


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

    @pytest.mark.parametrize("bad_session_id", [["boom"], {"boom": 1}, 0, False])
    def test_non_string_session_id(
        self, conv, client: FlaskClient, bad_session_id: object
    ):
        """Truthy/falsy non-string session_id values must return 400, not 500."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/step",
            json={"session_id": bad_session_id},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert data["error"] == "session_id must be a string"

    @pytest.mark.parametrize(
        "whitespace_id",
        ["   ", "\t", "\n", " \t\n "],
    )
    def test_whitespace_only_session_id(
        self, conv, client: FlaskClient, whitespace_id: str
    ):
        """Whitespace-only session_id should be rejected with 400, not 404."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/step",
            json={"session_id": whitespace_id},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert data["error"] == "session_id is required"

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

    def test_invalid_stream_type(self, conv, client: FlaskClient):
        """Step with non-boolean stream returns 400."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/step",
            json={
                "session_id": conv["session_id"],
                "stream": "false",  # string, not bool
            },
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "stream" in data["error"]

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

    @pytest.mark.parametrize("bad_model", [["bad-model"], {"bad": "model"}, 123])
    def test_invalid_model_type(self, conv, client: FlaskClient, bad_model: object):
        """Step with non-string model returns 400 without starting generation."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        assert session.generating is False

        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/step",
            json={
                "session_id": conv["session_id"],
                "model": bad_model,
            },
        )

        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "model" in data["error"]
        assert session.generating is False

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

    def test_step_loads_config_and_reserves_under_conversation_lock(
        self, conv, client: FlaskClient
    ):
        """Config snapshot and generation reservation share the mutation lock."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        lock = MagicMock()
        lock.__enter__.side_effect = lambda: setattr(lock, "held", True)
        lock.__exit__.side_effect = lambda *_: setattr(lock, "held", False)

        with (
            patch.object(SessionManager, "conversation_lock", return_value=lock),
            patch.object(
                session,
                "step_lock",
                MagicMock(
                    __enter__=MagicMock(
                        side_effect=lambda: setattr(
                            lock, "step_entered_while_held", lock.held
                        )
                    )
                ),
            ),
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

            def load_config(*_args, **_kwargs):
                lock.config_loaded_while_held = lock.held
                return cfg

            mock_config.side_effect = load_config
            mock_ws_config.return_value = MagicMock(
                get_env=MagicMock(return_value=None)
            )
            response = client.post(
                f"/api/v2/conversations/{conv['conversation_id']}/step",
                json={"session_id": conv["session_id"]},
            )

        assert response.status_code == 400
        assert lock.config_loaded_while_held is True
        assert lock.step_entered_while_held is True


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


@pytest.mark.parametrize(
    "endpoint", ["step", "tool/confirm", "rerun", "elicit/respond", "interrupt"]
)
def test_session_endpoints_reject_malformed_json(
    conv, client: FlaskClient, endpoint: str
):
    """Malformed JSON should return a structured 400 before field validation."""
    response = client.post(
        f"/api/v2/conversations/{conv['conversation_id']}/{endpoint}",
        data="{bad:",
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Malformed JSON in request body"}


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

    @pytest.mark.parametrize("bad_session_id", [["boom"], {"boom": 1}, 0, False])
    def test_non_string_session_id(
        self, conv, client: FlaskClient, bad_session_id: object
    ):
        """Interrupt must reject non-string session_id values with 400."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/interrupt",
            json={"session_id": bad_session_id},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert data["error"] == "session_id must be a string"

    @pytest.mark.parametrize(
        "whitespace_id",
        ["   ", "\t", "\n", " \t\n "],
    )
    def test_whitespace_only_session_id(
        self, conv, client: FlaskClient, whitespace_id: str
    ):
        """Whitespace-only session_id should be rejected with 400, not 404."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/interrupt",
            json={"session_id": whitespace_id},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert data["error"] == "session_id is required"

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

    def test_interrupt_marks_epoch_revoked(self, conv, client: FlaskClient):
        """Interrupt revokes workers queued under the current generation epoch."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        session.generating = True
        initial_seq = session.step_seq

        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/interrupt",
            json={"session_id": conv["session_id"]},
        )

        assert response.status_code == 200
        assert session.interrupted is True
        assert session.step_seq == initial_seq + 1

    def test_failed_step_preserves_generation_epoch(
        self, conv, client: FlaskClient, monkeypatch
    ):
        """A setup error does not revoke an existing tool worker's epoch."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        session.step_seq = 7
        session.interrupted = True
        monkeypatch.setattr(
            "gptme.server.api_v2_sessions.get_default_model", lambda: None
        )
        monkeypatch.setattr(
            "gptme.server.api_v2_sessions.Config.from_workspace",
            lambda **_kwargs: MagicMock(get_env=MagicMock(return_value=None)),
        )

        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/step",
            json={"session_id": conv["session_id"]},
        )

        assert response.status_code == 400
        assert session.step_seq == 7
        assert session.interrupted is True
        assert session.generating is False

    def test_interrupt_idle_session_does_not_poison_next_chain(
        self, conv, client: FlaskClient
    ):
        """An idempotent interrupt must not leave stale interrupt state."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        initial_seq = session.step_seq

        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/interrupt",
            json={"session_id": conv["session_id"]},
        )

        assert response.status_code == 200
        assert "already interrupted" in response.get_json()["message"].lower()
        assert session.interrupted is False
        assert session.step_seq == initial_seq


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
        data = response.get_json()
        assert data is not None
        assert data["error"] == "action is required"

    @pytest.mark.parametrize("bad_action", [["confirm"], {"confirm": True}, 0, False])
    def test_non_string_action(self, conv, client: FlaskClient, bad_action: object):
        """action must be a string before any pending-tool lookup."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/tool/confirm",
            json={
                "session_id": conv["session_id"],
                "tool_id": "some-tool",
                "action": bad_action,
            },
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert data["error"] == "action must be a string"

    @pytest.mark.parametrize(
        "whitespace_session_id",
        ["", "   ", "\t", "\n", "  \t\n  "],
    )
    def test_whitespace_session_id(
        self, conv, client: FlaskClient, whitespace_session_id: str
    ):
        """Whitespace-only session_id returns 400 in tool confirm endpoint."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/tool/confirm",
            json={
                "session_id": whitespace_session_id,
                "tool_id": "some-tool",
                "action": "confirm",
            },
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "session_id" in data["error"]

    @pytest.mark.parametrize("bad_session_id", [["boom"], {"boom": 1}, 0, False])
    def test_non_string_session_id(
        self, conv, client: FlaskClient, bad_session_id: object
    ):
        """Optional session_id must still be a string when provided."""
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
                    "session_id": bad_session_id,
                    "tool_id": tool_id,
                    "action": "skip",
                },
            )
            assert response.status_code == 400
            data = response.get_json()
            assert data is not None
            assert data["error"] == "session_id must be a string"
        finally:
            session.pending_tools.pop(tool_id, None)

    @pytest.mark.parametrize("bad_tool_id", [["boom"], {"boom": 1}, 0, False])
    def test_non_string_tool_id(self, conv, client: FlaskClient, bad_tool_id: object):
        """tool_id must be a string before pending-tool lookup."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/tool/confirm",
            json={
                "session_id": conv["session_id"],
                "tool_id": bad_tool_id,
                "action": "skip",
            },
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert data["error"] == "tool_id must be a string"

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

    def test_unknown_action_checked_before_tool_lookup(self, conv, client: FlaskClient):
        """Unknown actions should return 400 before the tool_id lookup."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/tool/confirm",
            json={
                "tool_id": "nonexistent-tool",
                "action": "invalid_action",
            },
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "unknown action" in data["error"].lower()

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

    def test_skip_loads_config_after_reserving_continuation(
        self, conv, client: FlaskClient
    ):
        """Skip captures model/workspace while holding the mutation lock."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        tool_id = str(uuid.uuid4())
        session.pending_tools[tool_id] = ToolExecution(
            tool_id=tool_id,
            tooluse=ToolUse("bash", [], "rm -rf /"),
        )
        lock = MagicMock()
        lock.__enter__.side_effect = lambda: setattr(lock, "held", True)
        lock.__exit__.side_effect = lambda *_: setattr(lock, "held", False)
        from gptme.config import ChatConfig

        cfg = ChatConfig(model="fresh/model")

        def load_config(*_args, **_kwargs):
            lock.config_loaded_while_held = lock.held
            return cfg

        with (
            patch.object(SessionManager, "conversation_lock", return_value=lock),
            patch(
                "gptme.server.api_v2_sessions.ChatConfig.load_or_create",
                side_effect=load_config,
            ),
            patch(
                "gptme.server.api_v2_sessions._start_step_thread", return_value=True
            ) as start_step,
            patch("gptme.server.api_v2_sessions._append_and_notify"),
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
        assert lock.config_loaded_while_held is True
        assert start_step.call_args.args[2] == "fresh/model"
        assert start_step.call_args.args[3] == cfg.workspace

    def test_skip_action(self, conv, client: FlaskClient):
        """Skip consumes the tool only after reserving its continuation."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        tool_id = str(uuid.uuid4())
        tool_exec = ToolExecution(
            tool_id=tool_id,
            tooluse=ToolUse("bash", [], "rm -rf /"),
        )
        session.pending_tools[tool_id] = tool_exec

        with (
            patch("gptme.server.api_v2_sessions._append_and_notify"),
            patch(
                "gptme.server.api_v2_sessions._start_step_thread", return_value=True
            ) as start_step,
            patch("gptme.server.api_v2_sessions.resolve_hook_confirmation") as resolve,
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
        assert tool_exec.status.value == "skipped"
        resolve.assert_called_once_with(tool_id, "skip", None)
        assert start_step.call_count == 1
        assert start_step.call_args.args[:2] == (conv["conversation_id"], session)
        assert start_step.call_args.kwargs == {"branch": "main", "reserved": True}

    def test_skip_preserves_tool_when_step_reserved_generation(
        self, conv, client: FlaskClient
    ):
        """A rejected skip remains retryable while another generation runs."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        tool_id = str(uuid.uuid4())
        tool_exec = ToolExecution(
            tool_id=tool_id,
            tooluse=ToolUse("bash", [], "rm -rf /"),
        )
        session.pending_tools[tool_id] = tool_exec
        session.generating = True

        with (
            patch("gptme.server.api_v2_sessions._append_and_notify") as append,
            patch("gptme.server.api_v2_sessions._start_step_thread") as start_step,
            patch("gptme.server.api_v2_sessions.resolve_hook_confirmation") as resolve,
        ):
            response = client.post(
                f"/api/v2/conversations/{conv['conversation_id']}/tool/confirm",
                json={
                    "session_id": conv["session_id"],
                    "tool_id": tool_id,
                    "action": "skip",
                },
            )

        assert response.status_code == 409
        assert session.pending_tools[tool_id] is tool_exec
        assert tool_exec.status.value == "pending"
        append.assert_not_called()
        start_step.assert_not_called()
        resolve.assert_not_called()

    def test_skip_releases_reservation_when_dispatch_fails(
        self, conv, client: FlaskClient
    ):
        """A failed continuation dispatch must not strand generating=True."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        tool_id = str(uuid.uuid4())
        session.pending_tools[tool_id] = ToolExecution(
            tool_id=tool_id,
            tooluse=ToolUse("bash", [], "rm -rf /"),
        )

        with (
            patch("gptme.server.api_v2_sessions._append_and_notify"),
            patch(
                "gptme.server.api_v2_sessions._start_step_thread",
                side_effect=RuntimeError("thread start failed"),
            ),
        ):
            response = client.post(
                f"/api/v2/conversations/{conv['conversation_id']}/tool/confirm",
                json={
                    "session_id": conv["session_id"],
                    "tool_id": tool_id,
                    "action": "skip",
                },
            )

        assert response.status_code == 500
        assert session.generating is False
        assert session.generating_since is None

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


# --- Concurrent tool confirmation tests (issue #3479) ---


class TestConcurrentToolConfirmation:
    """Regression tests for #3479: concurrent non-auto-confirm tool confirmation
    must not trigger the continuation step before all tool threads finish writing."""

    def test_executing_tools_field_exists(self, conv, client: FlaskClient):
        """ConversationSession has _executing_tools set, initially empty."""
        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        assert hasattr(session, "_executing_tools")
        assert len(session._executing_tools) == 0

    def test_no_premature_step_with_concurrent_confirmations(
        self, conv, client: FlaskClient
    ):
        """Fast tool must not trigger continuation while slow tool is still writing.

        Simulates issue #3479: two non-auto-confirm tools confirmed simultaneously.
        The fast tool finishes first; without the _executing_tools guard it would
        see pending_tools empty and fire _start_step_thread prematurely, before the
        slow tool has written its output.
        """
        from gptme.config import ChatConfig
        from gptme.server.session_step import start_tool_execution

        session = SessionManager.get_session(conv["session_id"])
        assert session is not None

        tool1_id = str(uuid.uuid4())
        tool2_id = str(uuid.uuid4())

        # slow_can_proceed gates tool2's write — held until we release it
        slow_can_proceed = threading.Event()

        step_calls: list[dict] = []

        def mock_start_step(conv_id, sess, *args, **kwargs):
            # Record whether slow tool is still in _executing_tools
            step_calls.append(
                {
                    "tool2_still_executing": tool2_id in sess._executing_tools,
                }
            )
            return True

        def make_execute(delay_event: threading.Event | None):
            """Return a tooluse.execute side-effect that optionally waits."""

            def execute(log, workspace, on_result_message=None):
                if delay_event is not None:
                    delay_event.wait(timeout=5)
                return []

            return execute

        # Register two tools: tool1 (fast), tool2 (slow — waits for slow_can_proceed)
        tool1_exec = ToolExecution(
            tool_id=tool1_id,
            tooluse=ToolUse("bash", [], "echo fast"),
            auto_confirm=False,
        )
        tool2_exec = ToolExecution(
            tool_id=tool2_id,
            tooluse=ToolUse("bash", [], "echo slow"),
            auto_confirm=False,
        )
        session.pending_tools[tool1_id] = tool1_exec
        session.pending_tools[tool2_id] = tool2_exec

        tool1_exec.tooluse = MagicMock(
            tool="bash",
            args=[],
            content="echo fast",
            call_id=tool1_id,
        )
        tool1_exec.tooluse.execute = make_execute(None)

        tool2_exec.tooluse = MagicMock(
            tool="bash",
            args=[],
            content="echo slow",
            call_id=tool2_id,
        )
        tool2_exec.tooluse.execute = make_execute(slow_can_proceed)

        chat_config = ChatConfig(model="mock/model")

        with (
            patch("gptme.server.session_step.prepare_execution_environment"),
            patch(
                "gptme.server.session_step.LogManager.load",
                return_value=MagicMock(
                    log=MagicMock(messages=[]),
                    workspace=MagicMock(),
                ),
            ),
            patch("gptme.server.session_step._append_and_notify"),
            patch("gptme.server.session_step._attach_tool_timings"),
            patch(
                "gptme.server.session_step._start_step_thread",
                side_effect=mock_start_step,
            ),
        ):
            # Confirm both tools concurrently (same as the UI "confirm all" action)
            t1 = start_tool_execution(
                conv["conversation_id"],
                session,
                tool1_id,
                None,
                "mock/model",
                chat_config,
                branch="main",
            )
            t2 = start_tool_execution(
                conv["conversation_id"],
                session,
                tool2_id,
                None,
                "mock/model",
                chat_config,
                branch="main",
            )

            # Let tool1 finish immediately (it doesn't wait)
            # Give it time to run and see if it tries to start the step
            t1.join(timeout=2)

            # Verify tool1 did NOT trigger the step (tool2 is still "executing")
            assert step_calls == [], (
                "Fast tool triggered step continuation before slow tool finished writing"
            )

            # Now let tool2 proceed to write its result
            slow_can_proceed.set()
            t2.join(timeout=5)

        # After both threads finish, exactly one step trigger should have fired
        assert len(step_calls) == 1, (
            f"Expected exactly 1 step trigger, got {len(step_calls)}"
        )
        # And at that point, tool2 must have already been removed from _executing_tools
        assert not step_calls[0]["tool2_still_executing"], (
            "Step was triggered while tool2 was still in _executing_tools"
        )
        # Both sets are clean
        assert len(session._executing_tools) == 0
        assert len(session.pending_tools) == 0

    def test_interrupt_suppresses_tool_continuation(self, conv, client: FlaskClient):
        """A tool already executing when interrupted must not continue the loop."""
        from gptme.config import ChatConfig
        from gptme.server.session_step import start_tool_execution

        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        tool_id = str(uuid.uuid4())
        started = threading.Event()
        finish = threading.Event()

        def execute(*args, **kwargs):
            started.set()
            finish.wait(timeout=5)
            return []

        tooluse = MagicMock(tool="bash", args=[], content="echo slow", call_id=tool_id)
        tooluse.execute = execute
        tool_exec = ToolExecution(
            tool_id=tool_id,
            tooluse=tooluse,
            auto_confirm=True,
        )
        session.pending_tools[tool_id] = tool_exec
        session.generating = True
        session.step_seq = 1

        with (
            patch("gptme.server.session_step.prepare_execution_environment"),
            patch(
                "gptme.server.session_step.LogManager.load",
                return_value=MagicMock(
                    log=MagicMock(messages=[]), workspace=MagicMock()
                ),
            ),
            patch("gptme.server.session_step._append_and_notify"),
            patch("gptme.server.session_step._attach_tool_timings"),
            patch("gptme.server.session_step._start_step_thread") as start_step,
        ):
            thread = start_tool_execution(
                conv["conversation_id"],
                session,
                tool_id,
                None,
                "mock/model",
                ChatConfig(model="mock/model"),
                reserved=True,
            )
            assert started.wait(timeout=2)

            response = client.post(
                f"/api/v2/conversations/{conv['conversation_id']}/interrupt",
                json={"session_id": conv["session_id"]},
            )
            assert response.status_code == 200
            finish.set()
            thread.join(timeout=5)

        assert not thread.is_alive()
        start_step.assert_not_called()
        assert session.generating is False

    def test_stale_worker_does_not_clear_new_step_reservation(
        self, conv, client: FlaskClient
    ):
        """A stale worker cannot release generating owned by a newer epoch."""
        from gptme.config import ChatConfig
        from gptme.server.session_step import start_tool_execution

        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        session.generating = True
        session.step_seq = 4

        with (
            patch("gptme.server.session_step.prepare_execution_environment"),
            patch("gptme.server.session_step._start_step_thread"),
        ):
            # No pending tool forces the worker's reservation-release path.
            thread = start_tool_execution(
                conv["conversation_id"],
                session,
                "already-removed",
                None,
                "mock/model",
                ChatConfig(model="mock/model"),
                reserved=True,
            )
            # Simulate a newer /step taking ownership before this worker runs.
            with session.step_lock:
                session.step_seq += 1
                session.generating = True
            thread.join(timeout=5)

        assert not thread.is_alive()
        assert session.generating is True

    def test_reserved_tool_dispatch_failure_releases_generation(
        self, conv, client: FlaskClient
    ):
        """A thread-start failure cannot strand a reserved rerun slot."""
        from gptme.config import ChatConfig
        from gptme.server.session_step import start_tool_execution

        session = SessionManager.get_session(conv["session_id"])
        assert session is not None
        session.generating = True
        session.step_seq = 3

        with (
            patch(
                "gptme.server.session_step.threading.Thread",
                side_effect=RuntimeError("thread start failed"),
            ),
            pytest.raises(RuntimeError, match="thread start failed"),
        ):
            start_tool_execution(
                conv["conversation_id"],
                session,
                "tool",
                None,
                "mock/model",
                ChatConfig(model="mock/model"),
                reserved=True,
            )

        assert session.generating is False
        assert session.generating_since is None

    def test_completion_bookkeeping_finishes_before_continuation(
        self, conv, client: FlaskClient
    ):
        """The final execution claim covers completion events and timing writes."""
        from gptme.config import ChatConfig
        from gptme.server.session_step import start_tool_execution

        session = SessionManager.get_session(conv["session_id"])
        assert session is not None

        tool_id = str(uuid.uuid4())
        tool_exec = ToolExecution(
            tool_id=tool_id,
            tooluse=ToolUse("bash", [], "echo done"),
            auto_confirm=False,
        )
        tool_exec.tooluse = MagicMock(
            tool="bash", args=[], content="echo done", call_id=tool_id
        )
        tool_exec.tooluse.execute = lambda *args, **kwargs: []
        session.pending_tools[tool_id] = tool_exec

        timing_entered = threading.Event()
        allow_timing = threading.Event()
        step_calls: list[bool] = []

        def blocking_attach_timings(*args, **kwargs):
            timing_entered.set()
            assert tool_id in session._executing_tools
            allow_timing.wait(timeout=5)

        chat_config = ChatConfig(model="mock/model")
        with (
            patch("gptme.server.session_step.prepare_execution_environment"),
            patch(
                "gptme.server.session_step.LogManager.load",
                return_value=MagicMock(
                    log=MagicMock(messages=[]), workspace=MagicMock()
                ),
            ),
            patch("gptme.server.session_step._append_and_notify"),
            patch(
                "gptme.server.session_step._attach_tool_timings",
                side_effect=blocking_attach_timings,
            ),
            patch(
                "gptme.server.session_step._start_step_thread",
                side_effect=lambda *args, **kwargs: step_calls.append(True),
            ),
        ):
            thread = start_tool_execution(
                conv["conversation_id"],
                session,
                tool_id,
                None,
                "mock/model",
                chat_config,
                branch="main",
            )
            assert timing_entered.wait(timeout=5)
            assert step_calls == []
            assert tool_id in session._executing_tools
            allow_timing.set()
            thread.join(timeout=5)

        assert not thread.is_alive()
        assert step_calls == [True]
        assert session._executing_tools == set()

    def test_claim_released_when_setup_raises_before_execution(
        self, conv, client: FlaskClient
    ):
        """A failure between claiming the tool and entering the execute block
        must still release the claim.

        The claim is added under conversation_lock, but the try/finally that
        releases it starts further down. If anything in between raises — most
        plausibly add_event, which iterates sessions and trims their event
        buffers — the tool id would be stranded in _executing_tools forever.
        Nothing else ever clears that set and the continuation gate requires it
        to be empty, so every later tool in this session would silently stop
        producing an assistant reply.
        """
        from gptme.config import ChatConfig
        from gptme.server.session_step import start_tool_execution

        session = SessionManager.get_session(conv["session_id"])
        assert session is not None

        tool_id = str(uuid.uuid4())
        tool_exec = ToolExecution(
            tool_id=tool_id,
            tooluse=ToolUse("bash", [], "echo hi"),
            auto_confirm=False,
        )
        session.pending_tools[tool_id] = tool_exec
        tool_exec.tooluse = MagicMock(
            tool="bash", args=[], content="echo hi", call_id=tool_id
        )
        tool_exec.tooluse.execute = lambda *a, **kw: []

        real_add_event = SessionManager.add_event

        def failing_add_event(conversation_id, event):
            # Fail exactly on the setup-phase event, after the claim is taken
            # but before the execute try/finally is entered.
            if event.get("type") == "tool_executing":
                raise RuntimeError("event bus exploded")
            return real_add_event(conversation_id, event)

        chat_config = ChatConfig(model="mock/model")

        with (
            patch("gptme.server.session_step.prepare_execution_environment"),
            patch(
                "gptme.server.session_step.LogManager.load",
                return_value=MagicMock(
                    log=MagicMock(messages=[]), workspace=MagicMock()
                ),
            ),
            patch("gptme.server.session_step._append_and_notify"),
            patch("gptme.server.session_step._attach_tool_timings"),
            patch("gptme.server.session_step._start_step_thread"),
            patch.object(SessionManager, "add_event", staticmethod(failing_add_event)),
        ):
            thread = start_tool_execution(
                conv["conversation_id"],
                session,
                tool_id,
                None,
                "mock/model",
                chat_config,
                branch="main",
            )
            thread.join(timeout=5)

        assert tool_id not in session._executing_tools, (
            "Tool claim was stranded in _executing_tools after a setup failure — "
            "the session can never start another continuation step"
        )
        assert len(session._executing_tools) == 0


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

    @pytest.mark.parametrize("bad_session_id", [["boom"], {"boom": 1}, 0, False])
    def test_non_string_session_id(
        self, conv, client: FlaskClient, bad_session_id: object
    ):
        """Rerun must reject non-string session_id values with 400."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/rerun",
            json={"session_id": bad_session_id},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert data["error"] == "session_id must be a string"

    @pytest.mark.parametrize(
        "whitespace_id",
        ["   ", "\t", "\n", " \t\n "],
    )
    def test_whitespace_only_session_id(
        self, conv, client: FlaskClient, whitespace_id: str
    ):
        """Whitespace-only session_id should be rejected with 400, not 404."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/rerun",
            json={"session_id": whitespace_id},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert data["error"] == "session_id is required"

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

            # Regression: rerun-created ToolExecutions must carry the
            # originating assistant message's timestamp so timing gets
            # attached to the correct step, not misattributed to whatever
            # assistant message happens to be last when the tool finishes.
            for tool_id in data["tool_ids"]:
                tool_exec = session.pending_tools[tool_id]
                assert tool_exec.assistant_msg_timestamp is not None
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

    def test_non_string_elicit_id(self, conv, client: FlaskClient):
        """Truthy non-string elicit_id values must return 400, not crash the registry lookup."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/elicit/respond",
            json={"elicit_id": ["boom"], "action": "accept"},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert data["error"] == "elicit_id must be a string"

    @patch("gptme.server.api_v2_sessions.resolve_hook_elicitation")
    def test_whitespace_only_elicit_id_rejected(
        self, mock_resolve, conv, client: FlaskClient
    ):
        """Whitespace-only elicit_id values return 400 instead of a false success."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/elicit/respond",
            json={"elicit_id": "   ", "action": "accept"},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert data["error"] == "elicit_id must not be blank"
        mock_resolve.assert_not_called()

    @patch("gptme.server.api_v2_sessions.resolve_hook_elicitation")
    def test_padded_elicit_id_rejected(self, mock_resolve, conv, client: FlaskClient):
        """Padded elicit_id values are rejected instead of silently normalized."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/elicit/respond",
            json={"elicit_id": " test-elicit-id ", "action": "accept"},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert data["error"] == (
            "elicit_id must not contain leading or trailing whitespace"
        )
        mock_resolve.assert_not_called()

    @pytest.mark.parametrize("elicit_id", [0, False, []])
    def test_falsy_non_string_elicit_id(self, conv, client: FlaskClient, elicit_id):
        """Falsy non-string elicit_id values must return the type error, not the required-fields error."""
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/elicit/respond",
            json={"elicit_id": elicit_id, "action": "accept"},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert data["error"] == "elicit_id must be a string"

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


class TestTranscriptEndpointInputValidation:
    """Test that /transcript rejects invalid turn field types with 400, not 500."""

    @pytest.mark.parametrize(
        "bad_text",
        [12345, True, {"nested": "obj"}, [1, 2, 3]],
        ids=["integer", "bool", "object", "array"],
    )
    def test_transcript_non_string_text_returns_400(
        self, bad_text, client: FlaskClient
    ):
        """turns[i].text must be a string; non-strings must return 400, not 500."""
        convname = f"test-transcript-{uuid.uuid4().hex[:8]}"
        response = client.post(
            f"/api/v2/conversations/{convname}/transcript",
            json={
                "turns": [{"role": "user", "text": bad_text}],
                "call_metadata": {"call_sid": "CA-test-text-type"},
            },
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "text" in data["error"]

    def test_transcript_null_text_is_skipped(self, client: FlaskClient):
        """turns with null text are silently skipped (treated as empty)."""
        convname = f"test-transcript-{uuid.uuid4().hex[:8]}"
        response = client.post(
            f"/api/v2/conversations/{convname}/transcript",
            json={
                "turns": [
                    {"role": "user", "text": None},
                    {"role": "user", "text": "hello"},
                ],
                "call_metadata": {"call_sid": "CA-test-null-text"},
            },
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["messages_added"] == 1

    def test_transcript_valid_turns_succeed(self, client: FlaskClient):
        """Baseline: valid turns with string text return 200."""
        convname = f"test-transcript-{uuid.uuid4().hex[:8]}"
        response = client.post(
            f"/api/v2/conversations/{convname}/transcript",
            json={
                "turns": [
                    {"role": "user", "text": "hello"},
                    {"role": "assistant", "text": "hi there"},
                ],
                "call_metadata": {"call_sid": "CA-test-valid"},
            },
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["messages_added"] == 2

    @pytest.mark.parametrize(
        "bad_call_sid",
        [12345, 0, True, False, {"nested": "obj"}, {}, [1, 2, 3], []],
        ids=[
            "truthy-integer",
            "falsy-integer",
            "truthy-bool",
            "falsy-bool",
            "truthy-object",
            "falsy-object",
            "truthy-array",
            "falsy-array",
        ],
    )
    def test_transcript_non_string_call_sid_returns_400(
        self, bad_call_sid, client: FlaskClient
    ):
        """call_metadata.call_sid must be a string; non-strings must return 400."""
        convname = f"test-transcript-{uuid.uuid4().hex[:8]}"
        response = client.post(
            f"/api/v2/conversations/{convname}/transcript",
            json={
                "turns": [{"role": "user", "text": "hello"}],
                "call_metadata": {"call_sid": bad_call_sid},
            },
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data is not None
        assert "call_sid" in data["error"]
