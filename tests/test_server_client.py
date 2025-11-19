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


def test_create_session_success(monkeypatch):
    """Test successful session creation with proper resource cleanup."""
    from unittest.mock import MagicMock, Mock

    client = GptmeApiClient()

    # Mock response with connected event
    mock_response = Mock()
    mock_response.iter_lines.return_value = [
        b'data: {"type": "connected", "session_id": "test-session-123"}'
    ]
    mock_response.raise_for_status = Mock()
    mock_response.close = Mock()

    # Mock session.get to return our mock response
    mock_get = MagicMock(return_value=mock_response)
    monkeypatch.setattr(client.session, "get", mock_get)

    # Test successful creation
    session_id = client.create_session("test-conv")
    assert session_id == "test-session-123"

    # Verify response was closed (resource cleanup)
    mock_response.close.assert_called_once()


def test_create_session_null_session_id(monkeypatch):
    """Test that null session_id raises ValueError."""
    from unittest.mock import MagicMock, Mock

    client = GptmeApiClient()

    # Mock response with null session_id
    mock_response = Mock()
    mock_response.iter_lines.return_value = [
        b'data: {"type": "connected", "session_id": null}'
    ]
    mock_response.raise_for_status = Mock()
    mock_response.close = Mock()

    mock_get = MagicMock(return_value=mock_response)
    monkeypatch.setattr(client.session, "get", mock_get)

    # Test null validation
    with pytest.raises(ValueError, match="null session_id"):
        client.create_session("test-conv")

    # Verify response was closed even on error (resource cleanup)
    mock_response.close.assert_called_once()


def test_create_session_missing_event(monkeypatch):
    """Test resource cleanup when connected event not received."""
    from unittest.mock import MagicMock, Mock

    client = GptmeApiClient()

    # Mock response without connected event
    mock_response = Mock()
    mock_response.iter_lines.return_value = [b'data: {"type": "other", "data": "test"}']
    mock_response.raise_for_status = Mock()
    mock_response.close = Mock()

    mock_get = MagicMock(return_value=mock_response)
    monkeypatch.setattr(client.session, "get", mock_get)

    # Test missing event handling
    with pytest.raises(ValueError, match="Failed to get session_id"):
        client.create_session("test-conv")

    # Verify response was closed even when no connected event (resource cleanup)
    mock_response.close.assert_called_once()


# TODO: Add integration tests using actual FlaskClient
# These would test create_session, take_step, and stream_events
# against a test server instance.
#
# Future enhancement: Support FlaskClient as session backend
# to enable unit tests without spinning up a server.
