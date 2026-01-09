import random

import pytest

flask = pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

from gptme.llm.models import get_default_model, get_recommended_model  # fmt: skip


@pytest.fixture
def conv(client: FlaskClient):
    convname = f"test-server-{random.randint(0, 1000000)}"
    response = client.put(f"/api/conversations/{convname}", json={})
    assert response.status_code == 200
    return convname


def test_root(client: FlaskClient):
    response = client.get("/")
    assert response.status_code == 200


def test_api_root(client: FlaskClient):
    response = client.get("/api")
    assert response.status_code == 200
    assert response.get_json() == {"message": "Hello World!"}


def test_api_conversation_list(client: FlaskClient):
    response = client.get("/api/conversations")
    assert response.status_code == 200


def test_api_conversation_get(conv, client: FlaskClient):
    response = client.get(f"/api/conversations/{conv}")
    assert response.status_code == 200


def test_api_conversation_post(conv, client: FlaskClient):
    response = client.post(
        f"/api/conversations/{conv}",
        json={"role": "user", "content": "hello"},
    )
    assert response.status_code == 200


@pytest.mark.slow
@pytest.mark.requires_api
@pytest.mark.xfail(
    reason="sometimes gets {'error': \"'Mock' object is not iterable\"} in CI"
)
def test_api_conversation_generate(conv: str, client: FlaskClient):
    # Ask the assistant to generate a test response
    response = client.post(
        f"/api/conversations/{conv}",
        json={"role": "user", "content": "hello, just testing"},
    )
    assert response.status_code == 200

    model = m.full if (m := get_default_model()) else get_recommended_model("anthropic")

    # Test regular (non-streaming) response
    response = client.post(
        f"/api/conversations/{conv}/generate",
        json={"model": model},
    )
    assert response.status_code == 200
    data = response.get_data(as_text=True)
    assert data  # Ensure we got some response
    msgs_resps = response.get_json()
    assert msgs_resps is not None  # Ensure we got valid JSON
    # Make sure it is a list and not an error
    assert isinstance(
        msgs_resps, list
    ), f"Response should be a list of messages, got: {msgs_resps}"
    # Assistant message + possible tool output
    assert len(msgs_resps) >= 1

    # First message should be the assistant's response
    assert msgs_resps[0]["role"] == "assistant"


@pytest.mark.slow
def test_api_conversation_generate_stream(conv: str, client: FlaskClient):
    # Ask the assistant to generate a test response
    response = client.post(
        f"/api/conversations/{conv}",
        json={"role": "user", "content": "hello, just testing"},
    )
    assert response.status_code == 200

    model = m.full if (m := get_default_model()) else get_recommended_model("anthropic")

    # Test streaming response
    response = client.post(
        f"/api/conversations/{conv}/generate",
        json={
            "model": model,
            "stream": True,
        },
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["Content-Type"]

    # Read and validate the streamed response
    chunks = list(response.iter_encoded())
    assert len(chunks) > 0

    # Each chunk should be a Server-Sent Event
    for chunk in chunks:
        chunk_str = chunk.decode("utf-8")
        assert chunk_str.startswith("data: ")
        # Skip empty chunks (heartbeats)
        if chunk_str.strip() == "data: ":
            continue
        data = chunk_str.replace("data: ", "").strip()
        assert data  # Non-empty data


def test_debug_errors_disabled(monkeypatch):
    """Test that debug errors are disabled by default."""
    from gptme.server.api import _is_debug_errors_enabled

    # Clear the env var to test default behavior
    monkeypatch.delenv("GPTME_DEBUG_ERRORS", raising=False)
    assert _is_debug_errors_enabled() is False


def test_debug_errors_enabled(monkeypatch):
    """Test that debug errors can be enabled via environment variable."""
    from gptme.server.api import _is_debug_errors_enabled

    # Test various truthy values
    for value in ["1", "true", "TRUE", "yes", "YES"]:
        monkeypatch.setenv("GPTME_DEBUG_ERRORS", value)
        assert _is_debug_errors_enabled() is True, f"Failed for value: {value}"

    # Test falsy values
    for value in ["0", "false", "no", ""]:
        monkeypatch.setenv("GPTME_DEBUG_ERRORS", value)
        assert _is_debug_errors_enabled() is False, f"Should be False for value: {value}"
