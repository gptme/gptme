from pathlib import Path
import random
import time
from datetime import datetime
from typing import cast, Any

import pytest
import tomlkit  # noqa
from gptme.config import ChatConfig, MCPConfig
from gptme.llm.models import ModelMeta, get_default_model
from gptme.prompts import get_prompt
from gptme.tools import get_toolchain

# Skip if flask not installed
pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

# Mark tests that require the server and add timeouts
pytestmark = [pytest.mark.timeout(10)]  # 10 second timeout for all tests


def create_conversation(client: FlaskClient, config: ChatConfig | None = None):
    """Create a V2 conversation with a session and optional config."""
    convname = f"test-server-v2-{random.randint(0, 1000000)}"

    # Create conversation with a custom system prompt
    json: dict[str, Any] = {
        "prompt": "You are an AI assistant for testing.",
    }

    if config:
        json["config"] = config.to_dict()

    response = client.put(
        f"/api/v2/conversations/{convname}",
        json=json,
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert "session_id" in data

    return {"conversation_id": convname, "session_id": data["session_id"]}


@pytest.fixture
def v2_conv(client: FlaskClient):
    """Create a V2 conversation with a session."""
    return create_conversation(client)


@pytest.fixture
def v2_conv_with_config(client: FlaskClient, config: ChatConfig):
    """Create a V2 conversation with a session and config."""
    return create_conversation(client, config)


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

    # Should contain system messages (custom system prompt + possibly workspace prompt)
    assert len(data["log"]) >= 1  # At least custom system prompt
    assert data["log"][0]["role"] == "system"
    assert "testing" in data["log"][0]["content"]


def test_v2_create_conversation_default_system_prompt(
    client: FlaskClient, tmp_path, monkeypatch
):
    """Test creating a V2 conversation with a default system prompt."""
    # Use tmp_path as workspace to avoid workspace context message
    monkeypatch.chdir(tmp_path)

    convname = f"test-server-v2-{random.randint(0, 1000000)}"
    response = client.put(
        f"/api/v2/conversations/{convname}",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "Hello, this is a test message.",
                    "timestamp": datetime.now().isoformat(),
                }
            ]
        },
    )
    assert response.status_code == 200
    conversation_id = response.get_json()["conversation_id"]

    response = client.get(f"/api/v2/conversations/{conversation_id}")
    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert "log" in data
    assert (
        len(data["log"]) == 2
    )  # Only system prompt + user message (no workspace context)
    assert data["log"][0]["role"] == "system"  # Primary system prompt
    assert data["log"][1]["role"] == "user"
    assert data["log"][1]["content"] == "Hello, this is a test message."

    # Check that the system prompt is the default one
    prompt_msgs = get_prompt(
        tools=[t for t in get_toolchain(None)],
        interactive=True,
        tool_format="markdown",
        model=None,
        prompt="full",
        workspace=tmp_path,
    )
    assert data["log"][0]["content"] == prompt_msgs[0].content


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
    # Should have system messages + the user message we just added
    assert len(data["log"]) >= 2  # At least custom system prompt + user message
    # Last message should be the user message we added
    assert data["log"][-1]["role"] == "user"
    assert data["log"][-1]["content"] == "Hello, this is a test message."


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


def test_v2_chat_config_saved_on_conversation_create(client: FlaskClient):
    """Test that the chat config is saved on conversation create."""
    input_config = ChatConfig(model="gpt-4o")
    input_config.tools = [t.name for t in get_toolchain(None) if not t.is_mcp]
    input_config.mcp = MCPConfig()
    conversation_id = create_conversation(client, input_config)["conversation_id"]

    response = client.get(f"/api/v2/conversations/{conversation_id}")
    data = response.get_json()
    assert data is not None
    assert "logfile" in data

    logfile = Path(data["logfile"])
    assert logfile.exists()
    assert logfile.is_file()

    config_path = logfile.parent / "config.toml"
    assert config_path.exists()
    assert config_path.is_file()

    config = ChatConfig.from_logdir(logfile.parent)
    print("old config", input_config.to_dict())
    print("-" * 80)
    print("new config", config.to_dict())
    assert config.to_dict() == input_config.to_dict()


def test_v2_chat_config_saved_separately_for_each_conversation(client: FlaskClient):
    """Test that the chat config is saved separately for each conversation."""
    input_config_1 = ChatConfig(model="gpt-4o")
    input_config_1.tools = [t.name for t in get_toolchain(None) if not t.is_mcp]
    input_config_1.mcp = MCPConfig()
    conversation_id_1 = create_conversation(client, input_config_1)["conversation_id"]

    input_config_2 = ChatConfig(model="gpt-4o-mini")
    input_config_2.tools = [t.name for t in get_toolchain(None) if not t.is_mcp]
    input_config_2.mcp = MCPConfig()
    conversation_id_2 = create_conversation(client, input_config_2)["conversation_id"]

    response_1 = client.get(f"/api/v2/conversations/{conversation_id_1}")
    data_1 = response_1.get_json()
    config_1 = ChatConfig.from_logdir(Path(data_1["logfile"]).parent)
    assert config_1.to_dict() == input_config_1.to_dict()

    response_2 = client.get(f"/api/v2/conversations/{conversation_id_2}")
    data_2 = response_2.get_json()
    config_2 = ChatConfig.from_logdir(Path(data_2["logfile"]).parent)
    assert config_2.to_dict() == input_config_2.to_dict()


def test_v2_chat_config_get_works(client: FlaskClient):
    """Test that the chat config get endpoint works."""
    input_config = ChatConfig(model="gpt-4o")
    input_config.tools = [t.name for t in get_toolchain(None) if not t.is_mcp]
    input_config.mcp = MCPConfig()
    conversation_id = create_conversation(client, input_config)["conversation_id"]

    response = client.get(f"/api/v2/conversations/{conversation_id}/config")
    config = ChatConfig.from_dict(response.get_json())
    print("config", config.to_dict())
    print("input_config", input_config.to_dict())
    assert config.to_dict() == input_config.to_dict()


def test_v2_chat_config_update_works(client: FlaskClient):
    """Test that the chat config update endpoint works."""
    input_config = ChatConfig(model="gpt-4o")
    input_config.tools = [t.name for t in get_toolchain(None) if not t.is_mcp]
    input_config.mcp = MCPConfig()
    conversation_id = create_conversation(client, input_config)["conversation_id"]

    response = client.get(f"/api/v2/conversations/{conversation_id}/config")
    config = ChatConfig.from_dict(response.get_json())
    assert config.to_dict() == input_config.to_dict()

    input_config.model = "gpt-4o-mini"
    response = client.patch(
        f"/api/v2/conversations/{conversation_id}/config", json=input_config.to_dict()
    )
    assert response.status_code == 200

    response = client.get(f"/api/v2/conversations/{conversation_id}/config")
    config = ChatConfig.from_dict(response.get_json())
    assert config.to_dict() == input_config.to_dict()
