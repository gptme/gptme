import pytest

from gptme.config import get_config
from gptme.llm.llm_openai import _prepare_messages_for_api
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


def test_timeout_default(monkeypatch):
    """Test that timeout uses NOT_GIVEN (client default) when LLM_API_TIMEOUT is not set."""
    from unittest.mock import Mock, patch

    from openai._types import NOT_GIVEN

    import gptme.llm.llm_openai as llm_openai
    from gptme.config import get_config

    # Set dummy API key for validation (client is mocked anyway)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    # Clear any existing LLM_API_TIMEOUT config
    monkeypatch.delenv("LLM_API_TIMEOUT", raising=False)

    # Clear the clients cache to force re-initialization
    llm_openai.clients.clear()

    # Get config instance
    config = get_config()

    with patch("openai.OpenAI") as mock_openai:
        mock_client = Mock()
        mock_openai.return_value = mock_client

        # Initialize OpenAI provider
        llm_openai.init("openai", config)

        # Verify OpenAI was called with NOT_GIVEN (uses client default)
        mock_openai.assert_called_once()
        call_kwargs = mock_openai.call_args[1]
        assert call_kwargs["timeout"] is NOT_GIVEN


def test_timeout_custom(monkeypatch):
    """Test that custom timeout is used when LLM_API_TIMEOUT is set."""
    from unittest.mock import Mock, patch

    import gptme.llm.llm_openai as llm_openai
    from gptme.config import get_config

    # Set dummy API key for validation (client is mocked anyway)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    # Set custom timeout
    monkeypatch.setenv("LLM_API_TIMEOUT", "1800")

    # Clear the clients cache to force re-initialization
    llm_openai.clients.clear()

    # Get config instance
    config = get_config()

    with patch("openai.OpenAI") as mock_openai:
        mock_client = Mock()
        mock_openai.return_value = mock_client

        # Initialize OpenAI provider
        llm_openai.init("openai", config)

        # Verify OpenAI was called with custom timeout
        mock_openai.assert_called_once()
        call_kwargs = mock_openai.call_args[1]
        assert call_kwargs["timeout"] == 1800.0


def test_timeout_all_providers(monkeypatch):
    """Test that timeout is passed to all OpenAI-compatible providers."""
    from typing import cast
    from unittest.mock import Mock, patch

    import gptme.llm.llm_openai as llm_openai
    from gptme.config import get_config
    from gptme.llm.models import Provider

    # Set custom timeout
    monkeypatch.setenv("LLM_API_TIMEOUT", "900")

    # Get config instance
    config = get_config()

    providers_to_test = ["openai", "openrouter", "groq", "deepseek", "xai"]

    for provider_str in providers_to_test:
        # Clear the clients cache
        llm_openai.clients.clear()

        # Cast to Provider type for mypy
        provider = cast(Provider, provider_str)

        with patch("openai.OpenAI") as mock_openai:
            mock_client = Mock()
            mock_openai.return_value = mock_client

            # Initialize provider
            try:
                llm_openai.init(provider, config)
            except Exception:
                # Skip providers that require additional config
                continue

            # Verify timeout was passed
            if mock_openai.called:
                call_kwargs = mock_openai.call_args[1]
                assert (
                    call_kwargs["timeout"] == 900.0
                ), f"Provider {provider} didn't receive correct timeout"


def test_timeout_invalid_value(monkeypatch, caplog):
    """Test that invalid timeout values log a warning and use default."""
    import logging
    from unittest.mock import patch

    import gptme.llm.llm_openai as llm_openai
    from gptme.config import get_config

    # Set invalid timeout
    monkeypatch.setenv("LLM_API_TIMEOUT", "not-a-number")

    # Clear the clients cache
    llm_openai.clients.clear()

    # Get config instance
    config = get_config()

    with caplog.at_level(logging.WARNING):
        with patch("openai.OpenAI"):
            # Should NOT raise - instead logs warning and uses default
            llm_openai.init("openai", config)

    # Verify warning was logged about invalid timeout
    assert any("Invalid LLM_API_TIMEOUT" in record.message for record in caplog.records)


def test_message_conversion_gpt5_with_tool_results():
    """Test that gpt-5 models preserve tool result messages (system with call_id).

    This is a regression test for issue #650 where tool results were being
    incorrectly converted to user messages by _prep_o1, causing the API
    to fail with "No tool output found for function call".
    """
    init_tools(allowlist=["save"])

    messages = [
        Message(role="system", content="System prompt", hide=True),
        Message(role="user", content="Save something to file.txt"),
        # Tool call from assistant (in tool format)
        Message(
            role="assistant",
            content='@save(call_123): {"path": "file.txt", "content": "test"}',
        ),
        # Tool result (system message with call_id)
        Message(role="system", content="Saved to file.txt", call_id="call_123"),
    ]

    # Test with gpt-5 model (uses _prep_o1)
    model = get_model("openai/gpt-5")
    save_tool = get_tool("save")
    assert save_tool is not None, "save tool not found"
    messages_dict, tools_dict = _prepare_messages_for_api(
        messages, model.full, [save_tool]
    )

    # Convert to list for indexing
    messages_list = list(messages_dict)

    # Verify that:
    # 1. Regular system message is converted to user message
    # 2. Tool result (system with call_id) is converted to tool message
    assert messages_list[0]["role"] == "user"  # System prompt -> user
    assert "<system>" in messages_list[0]["content"][0]["text"]

    assert messages_list[1]["role"] == "user"  # User message stays user

    assert messages_list[2]["role"] == "assistant"  # Assistant with tool call
    assert "tool_calls" in messages_list[2]
    assert messages_list[2]["tool_calls"][0]["id"] == "call_123"

    # The critical assertion: tool result should be role="tool", not role="user"
    assert messages_list[3]["role"] == "tool"  # Tool result preserved!
    assert messages_list[3]["tool_call_id"] == "call_123"
    assert messages_list[3]["content"][0]["text"] == "Saved to file.txt"


def test_transform_msgs_for_groq():
    """Test that _transform_msgs_for_special_provider handles mixed content types."""
    from typing import Any

    from gptme.llm.llm_openai import _transform_msgs_for_special_provider
    from gptme.llm.models import ModelMeta

    # Create a mock Groq model
    groq_model = ModelMeta(
        provider="groq",
        model="llama-3.1-8b-instant",
        context=8192,
    )

    # Test with list content containing only text parts
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "You are a helpful assistant."},
                {"type": "text", "text": "Be concise."},
            ],
        },
        {
            "role": "user",
            "content": "Hello",
        },
    ]

    result = list(_transform_msgs_for_special_provider(messages, groq_model))
    assert result[0]["content"] == "You are a helpful assistant.\n\nBe concise."
    assert result[1]["content"] == "Hello"

    # Test with mixed content (text and image) - images should be filtered out
    messages_with_image: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is in this image?"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,abc"},
                },
            ],
        },
    ]

    result = list(_transform_msgs_for_special_provider(messages_with_image, groq_model))
    assert result[0]["content"] == "What is in this image?"


def test_transform_msgs_for_groq_no_content():
    """Test that messages without content key are passed through unchanged."""
    from typing import Any

    from gptme.llm.llm_openai import _transform_msgs_for_special_provider
    from gptme.llm.models import ModelMeta

    groq_model = ModelMeta(
        provider="groq",
        model="llama-3.1-8b-instant",
        context=8192,
    )

    # Tool call message without content key
    messages: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": "{}"},
                }
            ],
        },
    ]

    result = list(_transform_msgs_for_special_provider(messages, groq_model))
    assert "content" not in result[0]
    assert result[0]["tool_calls"] == messages[0]["tool_calls"]


def test_transform_msgs_for_groq_images_only():
    """Test that messages with only non-text content use placeholder."""
    from typing import Any

    from gptme.llm.llm_openai import _transform_msgs_for_special_provider
    from gptme.llm.models import ModelMeta

    groq_model = ModelMeta(
        provider="groq",
        model="llama-3.1-8b-instant",
        context=8192,
    )

    # Message with only image content
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,abc"},
                },
            ],
        },
    ]

    result = list(_transform_msgs_for_special_provider(messages, groq_model))
    assert result[0]["content"] == "[non-text content]"


def test_transform_msgs_for_deepseek_tool_calls():
    """Test that DeepSeek assistant messages with tool_calls get empty content field."""
    from typing import Any

    from gptme.llm.llm_openai import _transform_msgs_for_special_provider
    from gptme.llm.models import ModelMeta

    deepseek_model = ModelMeta(
        provider="deepseek",
        model="deepseek-reasoner",
        context=8192,
    )

    # Assistant message with tool_calls but no content (typical for deepseek-reasoner)
    messages: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"location": "NYC"}',
                    },
                }
            ],
        },
    ]

    result = list(_transform_msgs_for_special_provider(messages, deepseek_model))

    # DeepSeek requires reasoning_content for assistant messages with tool_calls
    # Since we don't store reasoning_content, we add an empty reasoning_content field
    assert "reasoning_content" in result[0]
    assert result[0]["reasoning_content"] == ""
    assert result[0]["tool_calls"] == messages[0]["tool_calls"]


def test_transform_msgs_for_deepseek_tool_results():
    """Test that DeepSeek tool result messages are not affected."""
    from typing import Any

    from gptme.llm.llm_openai import _transform_msgs_for_special_provider
    from gptme.llm.models import ModelMeta

    deepseek_model = ModelMeta(
        provider="deepseek",
        model="deepseek-reasoner",
        context=8192,
    )

    # Tool result message without content
    messages: list[dict[str, Any]] = [
        {
            "role": "tool",
            "tool_call_id": "call_123",
            "content": "Weather is sunny",
        },
    ]

    result = list(_transform_msgs_for_special_provider(messages, deepseek_model))

    # Tool messages should pass through unchanged
    assert result[0] == messages[0]
