"""Tests for gptme server API client."""

import pytest

from gptme.server.client import ConversationEvent, GptmeApiClient

# Skip if flask not installed
pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)


pytestmark = [pytest.mark.timeout(10)]


def test_client_initialization():
    """Test client can be initialized with different configurations."""
    # Default initialization
    client = GptmeApiClient()
    assert client.base_url == "http://localhost:5000"
    assert client.session is not None

    # With custom base URL
    client = GptmeApiClient(base_url="http://example.com:8000/")
    assert client.base_url == "http://example.com:8000"

    # With auth token
    client = GptmeApiClient(auth_token="test-token")
    assert "Authorization" in client.session.headers
    assert client.session.headers["Authorization"] == "Bearer test-token"


def test_conversation_event_creation():
    """Test ConversationEvent dataclass."""
    event = ConversationEvent(type="message", data={"content": "test"})
    assert event.type == "message"
    assert event.data["content"] == "test"


# TODO: Add integration tests using actual FlaskClient
# These would test create_session, take_step, and stream_events
# against a test server instance.
#
# Future enhancement: Support FlaskClient as session backend
# to enable unit tests without spinning up a server.
