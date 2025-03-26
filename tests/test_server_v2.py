import random
import time
from datetime import datetime
from typing import cast

import pytest
from flask.testing import FlaskClient  # noqa
from gptme.llm.models import ModelMeta, get_default_model

# Skip if flask not installed
pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

# Mark tests that require the server and add timeouts
pytestmark = [pytest.mark.timeout(10)]  # 10 second timeout for all tests


@pytest.fixture
def v2_conv(client: FlaskClient):
    """Create a V2 conversation with a session."""
    convname = f"test-server-v2-{random.randint(0, 1000000)}"

    # Create conversation with a system message
    response = client.put(
        f"/api/v2/conversations/{convname}",
        json={
            "messages": [
                {
                    "role": "system",
                    "content": "You are an AI assistant for testing.",
                    "timestamp": datetime.now().isoformat(),
                }
            ]
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert "session_id" in data

    return {"conversation_id": convname, "session_id": data["session_id"]}


def test_v2_api_root(client: FlaskClient):
    """Test the V2 API root endpoint."""
    response = client.get("/api/v2")
    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert "message" in data
    assert "gptme v2 API" in data["message"]


def test_v2_conversations_list(client: FlaskClient):
    """Test listing V2 conversations."""
    response = client.get("/api/v2/conversations")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)


def test_v2_conversation_get(v2_conv, client: FlaskClient):
    """Test getting a V2 conversation."""
    conversation_id = v2_conv["conversation_id"]
    response = client.get(f"/api/v2/conversations/{conversation_id}")

    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert "log" in data

    # Should contain the system message we created
    assert len(data["log"]) == 1
    assert data["log"][0]["role"] == "system"


def test_v2_conversation_post(v2_conv, client: FlaskClient):
    """Test posting a message to a V2 conversation."""
    conversation_id = v2_conv["conversation_id"]

    response = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Hello, this is a test message."},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["status"] == "ok"

    # Verify message was added
    response = client.get(f"/api/v2/conversations/{conversation_id}")
    data = response.get_json()
    assert len(data["log"]) == 2
    assert data["log"][1]["role"] == "user"
    assert data["log"][1]["content"] == "Hello, this is a test message."


@pytest.mark.slow
@pytest.mark.requires_api
def test_v2_generate(v2_conv, client: FlaskClient):
    """Test generating a response in a V2 conversation."""
    # Skip if no API key is available
    default_model = get_default_model()
    if default_model is None:
        pytest.skip("No API key available for testing")

    # Use cast to tell mypy that default_model is not None
    model = cast(ModelMeta, default_model)
    model_name = model.full

    conversation_id = v2_conv["conversation_id"]
    session_id = v2_conv["session_id"]

    # Add a user message
    client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "What is 2+2?"},
    )

    # Start generation
    response = client.post(
        f"/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id, "model": model_name},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["status"] == "ok"
    assert data["session_id"] == session_id


@pytest.mark.slow
@pytest.mark.requires_api
def test_v2_interrupt(v2_conv, client: FlaskClient):
    """Test interrupting generation in a V2 conversation."""
    # Skip if no API key is available
    default_model = get_default_model()
    if default_model is None:
        pytest.skip("No API key available for testing")

    # Use cast to tell mypy that default_model is not None
    model = cast(ModelMeta, default_model)
    model_name = model.full

    conversation_id = v2_conv["conversation_id"]
    session_id = v2_conv["session_id"]

    # Add a user message (simple prompt to minimize API usage)
    client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Count from 1 to 10"},
    )

    # Start generation
    client.post(
        f"/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id, "model": model_name},
    )

    # Wait briefly to let generation start (but with a short timeout)
    time.sleep(0.2)

    # Interrupt generation
    response = client.post(
        f"/api/v2/conversations/{conversation_id}/interrupt",
        json={"session_id": session_id},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["status"] == "ok"
    assert "interrupted" in data["message"].lower()
