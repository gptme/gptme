"""Tests for path traversal validation on conversation_id.

Ensures all endpoints that accept conversation_id reject path traversal
attempts (CWE-22) before any file system operations occur.
"""

import pytest

# Skip if flask not installed
pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

pytestmark = [pytest.mark.timeout(10)]


# Payloads that bypass Flask's URL routing (no '/' so <string:> matches them)
# Payloads with '/' are already blocked by Flask routing (<string:> doesn't match '/')
TRAVERSAL_PAYLOADS = [
    "..",
    "test\\..\\..\\secret",
    "..\\..\\etc\\passwd",
]


def _assert_traversal_rejected(response):
    """Assert the response is a 400 with the path traversal error message."""
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert data["error"] == "Invalid conversation_id"


class TestSessionEndpointValidation:
    """Session API endpoints must reject path traversal in conversation_id."""

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_events_rejects_traversal(self, client: FlaskClient, payload: str):
        response = client.get(f"/api/v2/conversations/{payload}/events")
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_step_rejects_traversal(self, client: FlaskClient, payload: str):
        response = client.post(
            f"/api/v2/conversations/{payload}/step",
            json={"session_id": "fake"},
        )
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_tool_confirm_rejects_traversal(self, client: FlaskClient, payload: str):
        response = client.post(
            f"/api/v2/conversations/{payload}/tool/confirm",
            json={"tool_id": "fake", "action": "approve"},
        )
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_rerun_rejects_traversal(self, client: FlaskClient, payload: str):
        response = client.post(f"/api/v2/conversations/{payload}/rerun")
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_elicit_respond_rejects_traversal(self, client: FlaskClient, payload: str):
        response = client.post(
            f"/api/v2/conversations/{payload}/elicit/respond",
            json={"elicit_id": "fake", "response": "test"},
        )
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_interrupt_rejects_traversal(self, client: FlaskClient, payload: str):
        response = client.post(
            f"/api/v2/conversations/{payload}/interrupt",
            json={"session_id": "fake"},
        )
        _assert_traversal_rejected(response)


class TestWorkspaceEndpointValidation:
    """Workspace API endpoints must reject path traversal in conversation_id."""

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_browse_rejects_traversal(self, client: FlaskClient, payload: str):
        response = client.get(f"/api/v2/conversations/{payload}/workspace")
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_upload_rejects_traversal(self, client: FlaskClient, payload: str):
        response = client.post(f"/api/v2/conversations/{payload}/workspace/upload")
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_file_rejects_traversal(self, client: FlaskClient, payload: str):
        # Route is /files/<path:filepath>, not /workspace/file/...
        response = client.get(f"/api/v2/conversations/{payload}/files/test.py")
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_preview_rejects_traversal(self, client: FlaskClient, payload: str):
        # Route is /workspace/<path:filepath>/preview
        response = client.get(
            f"/api/v2/conversations/{payload}/workspace/test.py/preview"
        )
        _assert_traversal_rejected(response)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_download_rejects_traversal(self, client: FlaskClient, payload: str):
        # Route is /workspace/<path:filepath>/download
        response = client.get(
            f"/api/v2/conversations/{payload}/workspace/test.py/download"
        )
        _assert_traversal_rejected(response)


class TestValidConversationIdAccepted:
    """Valid conversation_ids must not be rejected by path traversal checks."""

    def test_simple_name(self, client: FlaskClient):
        """A valid name passes validation (may fail later with 404/etc, not 400 traversal)."""
        response = client.get("/api/v2/conversations/valid-conv-name/workspace")
        # Should not be rejected as traversal — the error (if any) will be
        # about the conversation not existing, not about invalid ID
        if response.status_code == 400:
            data = response.get_json()
            assert data["error"] != "Invalid conversation_id"

    def test_name_with_numbers(self, client: FlaskClient):
        response = client.get("/api/v2/conversations/test-2026-04-03/workspace")
        if response.status_code == 400:
            data = response.get_json()
            assert data["error"] != "Invalid conversation_id"

    def test_name_with_dots_no_traversal(self, client: FlaskClient):
        """A single dot is fine (not '..')."""
        response = client.get("/api/v2/conversations/test.conv.name/workspace")
        if response.status_code == 400:
            data = response.get_json()
            assert data["error"] != "Invalid conversation_id"
