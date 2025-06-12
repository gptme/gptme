"""Test configuration and shared fixtures."""

import json
import logging
import os
import queue
import random
import socket
import tempfile
import threading
import time
from contextlib import contextmanager

import pytest
import requests
from gptme.config import get_config
from gptme.init import init  # noqa
from gptme.tools import clear_tools
from gptme.tools.rag import _has_gptme_rag

logger = logging.getLogger(__name__)


def has_api_key() -> bool:
    """Check if any API key is configured."""
    config = get_config()
    # Check for any configured API keys
    return bool(
        config.get_env("OPENAI_API_KEY", "")
        or config.get_env("ANTHROPIC_API_KEY", "")
        or config.get_env("OPENROUTER_API_KEY", "")
        or config.get_env("DEEPSEEK_API_KEY", "")
    )


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "requires_api: mark test as requiring an API key",
    )


def pytest_collection_modifyitems(config, items):
    """Skip tests marked as requiring API key if no key is configured."""
    if not has_api_key():
        # Set environment variables to override LLM provider config
        os.environ["MODEL"] = "local/test"
        os.environ["OPENAI_BASE_URL"] = "http://localhost:666"

        # Skip tests that require an API key if no key is configured
        skip_api = pytest.mark.skip(reason="No API key configured")
        for item in items:
            if "requires_api" in item.keywords:
                item.add_marker(skip_api)


def pytest_sessionstart(session):
    # Download the embedding model before running tests.
    download_model()


def download_model():
    if not _has_gptme_rag():
        return

    try:
        # downloads the model if it doesn't exist
        from chromadb.utils import embedding_functions  # type: ignore # fmt: skip
    except ImportError:
        return

    ef = embedding_functions.DefaultEmbeddingFunction()
    if ef:
        ef._download_model_if_not_exists()  # type: ignore


@pytest.fixture(autouse=True)
def clear_tools_before():
    # Clear all tools and cache to prevent test conflicts
    clear_tools()


@pytest.fixture
def temp_file():
    @contextmanager
    def _temp_file(content):
        # Create a temporary file with the given content
        temporary_file = tempfile.NamedTemporaryFile(delete=False, mode="w+")
        try:
            temporary_file.write(content)
            temporary_file.flush()
            temporary_file.close()
            yield temporary_file.name  # Yield the path to the temporary file
        finally:
            # Delete the temporary file to ensure cleanup
            if os.path.exists(temporary_file.name):
                os.unlink(temporary_file.name)

    return _temp_file


@pytest.fixture(autouse=True)
def init_():
    init(None, interactive=False, tool_allowlist=None)


@pytest.fixture
def server_thread():
    """Start a server in a thread for testing."""
    from gptme.server.api import create_app  # fmt: skip

    app = create_app()

    # Find a free port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("localhost", 0))  # Let OS assign a free port
    port = s.getsockname()[1]
    s.close()

    # Configure the app for testing
    app.config["TESTING"] = True
    app.config["SERVER_NAME"] = f"localhost:{port}"

    # Start the server in a thread
    def run_server():
        with app.app_context():
            app.run(port=port, threaded=True)

    thread = threading.Thread(target=run_server)
    thread.daemon = True
    thread.start()

    # Give server time to start (so we don't get Connection Refused)
    time.sleep(0.5)

    yield port  # Return the port to the test


@pytest.fixture
def client():
    from gptme.server.api import create_app  # fmt: skip

    app = create_app()
    with app.test_client() as client:
        yield client


@pytest.fixture(scope="function")
def setup_conversation(server_thread, tmp_path):
    """Create a conversation and return its ID, session ID, and port."""
    port = server_thread
    conversation_id = f"test-tools-{int(time.time())}-{random.randint(1000, 9999)}"

    # Create conversation with custom system prompt
    resp = requests.put(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        json={
            "prompt": "You are an AI assistant for testing.",
            "config": {
                "workspace": str(tmp_path),
            },
        },
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    return port, conversation_id, session_id


@pytest.fixture(scope="function")
def event_listener(setup_conversation):
    """Set up an event listener for the conversation."""
    port, conversation_id, session_id = setup_conversation
    events: queue.Queue = queue.Queue()
    event_sequence = []
    tool_id = None
    tool_output_received = False
    tool_executing_received = False

    def listen_for_events():
        nonlocal tool_id, tool_output_received, tool_executing_received
        resp = requests.get(
            f"http://localhost:{port}/api/v2/conversations/{conversation_id}/events?session_id={session_id}",
            stream=True,
        )
        try:
            for line in resp.iter_lines():
                if line:
                    line = line.decode("utf-8")
                    if line.startswith("data: "):
                        event_data = json.loads(line[6:])
                        events.put(event_data)

                        # Track event types
                        if "type" in event_data:
                            event_type = event_data["type"]
                            event_sequence.append(event_type)

                            if event_type == "tool_pending":
                                tool_id = event_data.get("tool_id")
                            elif event_type == "tool_executing":
                                tool_executing_received = True
        except Exception as e:
            events.put({"error": str(e)})
        finally:
            resp.close()

    event_thread = threading.Thread(target=listen_for_events)
    event_thread.daemon = True
    event_thread.start()

    return {
        "port": port,
        "conversation_id": conversation_id,
        "session_id": session_id,
        "events": events,
        "event_sequence": event_sequence,
        "get_tool_id": lambda: tool_id,
        "is_tool_executing_received": lambda: tool_executing_received,
    }


@pytest.fixture
def mock_generation():
    """Create a mock generation with customizable output."""

    def create(responses: list[str]):
        response_iter = iter(responses)

        def mock_stream(messages, model, tools=None, max_tokens=None):
            try:
                content = next(response_iter)
                # Yield the content as a single chunk that will be iterated over char by char
                yield [content]  # Wrap in list so it's only iterated once
            except StopIteration:
                yield ["No more responses"]

        return mock_stream

    return create


@pytest.fixture
def wait_for_event():
    """
    Wait for a specific event type in the event listener.

    Waiting for an event will mark all events before it as already awaited,
    so repeated calls don't wait for events before the last awaited one.
    """
    # max index awaited
    already_awaited = 0

    def wait(event_listener, event_type, timeout=10):
        nonlocal already_awaited
        start_time = time.time()
        seq = event_listener["event_sequence"]
        while time.time() - start_time < timeout:
            if event_type in seq[already_awaited:]:
                events_passed = seq[already_awaited:].index(event_type) + 1
                # print(seq[already_awaited : already_awaited + events_passed])
                already_awaited += events_passed
                # print(already_awaited)
                return True
            time.sleep(0.1)
        return False

    return wait
