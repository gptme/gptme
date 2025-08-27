import threading
from unittest.mock import Mock, patch

import pytest
from gptme.config import get_config
from gptme.llm.llm_openai import _prepare_messages_for_api, stream
from gptme.llm.models import get_default_model, get_model, set_default_model
from gptme.message import Message
from gptme.tools import get_tool, init_tools


@pytest.fixture(autouse=True)
def reset_default_model():
    default_model = get_default_model() or get_config().get_env("MODEL")
    assert default_model, "No default model set in config or environment"
    yield
    set_default_model(default_model)


def test_message_conversion():
    messages = [
        Message(role="system", content="Initial Message", pinned=True, hide=True),
        Message(role="system", content="Project prompt", hide=True),
        Message(role="user", content="First user prompt"),
    ]

    model = get_model("openai/gpt-4o")
    messages_dict, tools_dict = _prepare_messages_for_api(messages, model.full, None)

    assert tools_dict is None
    assert messages_dict == [
        {"role": "system", "content": [{"type": "text", "text": "Initial Message"}]},
        {"role": "system", "content": [{"type": "text", "text": "Project prompt"}]},
        {"role": "user", "content": [{"type": "text", "text": "First user prompt"}]},
    ]


def test_message_conversion_o1():
    messages = [
        Message(role="system", content="Initial Message", pinned=True, hide=True),
        Message(role="system", content="Project prompt", hide=True),
        Message(role="user", content="First user prompt"),
    ]

    model = get_model("openai/o1-mini")
    messages_dict, _ = _prepare_messages_for_api(messages, model.full, None)

    assert messages_dict == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "<system>\nInitial Message\n</system>"}
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "<system>\nProject prompt\n</system>"}
            ],
        },
        {"role": "user", "content": [{"type": "text", "text": "First user prompt"}]},
    ]


def test_message_conversion_without_tools():
    init_tools(allowlist=["save"])

    messages = [
        Message(role="system", content="Initial Message", pinned=True, hide=True),
        Message(role="system", content="Project prompt", hide=True),
        Message(role="user", content="First user prompt"),
        Message(
            role="assistant",
            content="<thinking>\nSomething\n</thinking>\n```save path.txt\nfile_content\n```",
        ),
        Message(role="system", content="Saved to toto.txt"),
    ]

    model = get_model("openai/gpt-4o")
    messages_dicts, _ = _prepare_messages_for_api(messages, model.full, None)

    assert messages_dicts == [
        {"role": "system", "content": [{"type": "text", "text": "Initial Message"}]},
        {"role": "system", "content": [{"type": "text", "text": "Project prompt"}]},
        {"role": "user", "content": [{"type": "text", "text": "First user prompt"}]},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "<thinking>\nSomething\n</thinking>\n```save path.txt\nfile_content\n```",
                }
            ],
        },
        {
            "role": "system",
            "content": [{"type": "text", "text": "Saved to toto.txt"}],
        },
    ]


def test_message_conversion_with_tools():
    init_tools(allowlist=["save"])

    messages = [
        Message(role="user", content="First user prompt"),
        Message(
            role="assistant",
            content='<thinking>\nSomething\n</thinking>\n@save(tool_call_id): {"path": "path.txt", "content": "file_content"}',
        ),
        Message(role="system", content="Saved to toto.txt", call_id="tool_call_id"),
        Message(role="user", content="Second user prompt"),
        Message(
            role="assistant",
            content='\n@save(tool_call_id): {"path": "path.txt", "content": "file_content"}',
        ),
        Message(role="system", content="Saved to toto.txt", call_id="tool_call_id"),
        Message(role="system", content="(Modified by user)", call_id="tool_call_id"),
    ]

    tool_save = get_tool("save")
    assert tool_save

    model = get_model("openai/gpt-4o")
    messages_dicts, tools_dict = _prepare_messages_for_api(
        messages, model.full, [tool_save]
    )

    assert tools_dict == [
        {
            "type": "function",
            "function": {
                "name": "save",
                "description": "Create or overwrite a file with the given content.\n\n"
                "The path can be relative to the current directory, or absolute.\n"
                "If the current directory changes, the path will be relative to the "
                "new directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The path of the file",
                        },
                        "content": {
                            "type": "string",
                            "description": "The content to save",
                        },
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            },
        }
    ]

    assert messages_dicts == [
        {"role": "user", "content": [{"type": "text", "text": "First user prompt"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "<thinking>\nSomething\n</thinking>\n"}
            ],
            "tool_calls": [
                {
                    "id": "tool_call_id",
                    "type": "function",
                    "function": {
                        "name": "save",
                        "arguments": '{"path": "path.txt", "content": "file_content"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": [{"type": "text", "text": "Saved to toto.txt"}],
            "tool_call_id": "tool_call_id",
        },
        {"role": "user", "content": [{"type": "text", "text": "Second user prompt"}]},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "tool_call_id",
                    "type": "function",
                    "function": {
                        "name": "save",
                        "arguments": '{"path": "path.txt", "content": "file_content"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": [
                {"type": "text", "text": "Saved to toto.txt"},
                {"type": "text", "text": "(Modified by user)"},
            ],
            "tool_call_id": "tool_call_id",
        },
    ]


def test_message_conversion_with_tool_and_non_tool():
    init_tools(allowlist=["save", "shell"])

    messages = [
        Message(role="user", content="First user prompt"),
        Message(
            role="assistant",
            content='\n@save(tool_call_id): {"path": "path.txt", "content": "file_content"}',
        ),
        Message(role="system", content="Saved to toto.txt", call_id="tool_call_id"),
        Message(
            role="assistant",
            content=(
                "The script `hello.py` has been created. "
                "Run it using the command:\n\n```shell\npython hello.py\n```\n"
            ),
        ),
        Message(
            role="system",
            content="Ran command: `python hello.py`\n\n `Hello, world!`\n\n",
        ),
    ]

    tool_save = get_tool("save")
    tool_shell = get_tool("shell")
    assert tool_save and tool_shell

    model = get_model("openai/gpt-4o")
    messages_dicts, _ = _prepare_messages_for_api(
        messages, model.full, [tool_save, tool_shell]
    )

    assert messages_dicts == [
        {"role": "user", "content": [{"type": "text", "text": "First user prompt"}]},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "tool_call_id",
                    "type": "function",
                    "function": {
                        "name": "save",
                        "arguments": '{"path": "path.txt", "content": "file_content"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": [{"type": "text", "text": "Saved to toto.txt"}],
            "tool_call_id": "tool_call_id",
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "The script `hello.py` has been created. Run it using the command:\n\n```shell\npython hello.py\n```\n",
                }
            ],
        },
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "Ran command: `python hello.py`\n\n `Hello, world!`\n\n",
                }
            ],
        },
    ]


def test_stream_cancellation():
    """Test that stream function respects cancellation events."""
    cancel_event = threading.Event()
    messages = [Message(role="user", content="Test prompt")]

    # Mock streaming response chunks
    mock_chunks = []
    for i in range(10):
        mock_chunk = Mock()
        mock_chunk.choices = [Mock()]
        mock_chunk.choices[0].finish_reason = None if i < 9 else "stop"
        mock_chunk.choices[0].delta = Mock()
        mock_chunk.choices[0].delta.content = f"chunk_{i} "
        # No reasoning content for simplicity
        mock_chunk.choices[0].delta.reasoning_content = None
        mock_chunk.choices[0].delta.reasoning = None
        mock_chunk.choices[0].delta.tool_calls = None
        mock_chunks.append(mock_chunk)

    # Mock the OpenAI client
    with patch("gptme.llm.llm_openai.get_client") as mock_get_client:
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock the streaming response
        mock_stream_response = Mock()
        mock_stream_response.__iter__ = lambda self: iter(mock_chunks)
        mock_stream_response.close = Mock()

        mock_client.chat.completions.create.return_value = mock_stream_response

        # Test normal streaming (no cancellation)
        chunks = list(stream(messages, "openai/gpt-4o", None, None))
        assert len(chunks) == 10
        assert "chunk_0" in chunks[0]

        # Test cancellation after 3 chunks
        cancel_event.clear()
        chunks_cancelled = []

        def cancel_after_chunks():
            # Cancel after receiving a few chunks
            import time

            time.sleep(0.01)  # Small delay to let streaming start
            cancel_event.set()

        cancel_thread = threading.Thread(target=cancel_after_chunks)
        cancel_thread.start()

        for chunk in stream(messages, "openai/gpt-4o", None, cancel_event):
            chunks_cancelled.append(chunk)
            # If we get too many chunks, cancellation didn't work
            if len(chunks_cancelled) > 5:
                break

        cancel_thread.join()

        # Should have received fewer chunks due to cancellation
        assert len(chunks_cancelled) < 10
        # Verify cleanup was called
        mock_stream_response.close.assert_called()
