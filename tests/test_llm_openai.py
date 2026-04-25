import pytest

from gptme.config import get_config
from gptme.llm import llm_openai
from gptme.llm.llm_openai import _maybe_apply_verbosity, _prepare_messages_for_api
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


def test_message_conversion_tool_response_with_image():
    """Tool responses with image files should use follow-up user messages for images.

    When a tool response (system + call_id) has image file attachments (e.g. from
    view_image via ipython), the tool message itself must remain text-only (OpenAI
    tool messages only support text content), but the images should be forwarded as
    a follow-up user message so vision-capable models can still see them.
    """
    import tempfile
    from pathlib import Path

    init_tools(allowlist=["save"])

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        # Write minimal PNG header (just needs to exist and be readable)
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        image_path = Path(f.name)

    try:
        messages = [
            Message(role="user", content="Can you view this image?"),
            Message(
                role="assistant",
                content='@ipython(call1): {"code": "view_image(\'/path/image.png\')"}',
            ),
            # Tool response with image file (like view_image returns)
            Message(
                role="system",
                content="Viewing image at `/path/image.png`\nImage size: 100x100, 0.1KB",
                call_id="call1",
                files=[image_path],
            ),
        ]

        tool_save = get_tool("save")
        assert tool_save

        model = get_model("openai/gpt-4o")
        messages_dicts, _ = _prepare_messages_for_api(messages, model.full, [tool_save])

        # The tool response (index 2) should be a "tool" message with text-only content
        tool_msg = messages_dicts[2]
        assert tool_msg["role"] == "tool", "Tool response must have role='tool'"
        assert tool_msg["tool_call_id"] == "call1"
        # Content should be a list of text parts only, no image_url parts
        content = tool_msg["content"]
        assert isinstance(content, list), "Tool message content must be a list"
        for part in content:
            if isinstance(part, dict):
                assert part.get("type") != "image_url", (
                    "Tool messages must not have images"
                )

        # A follow-up user message (index 3) should carry the image for vision models
        assert len(messages_dicts) == 4, (
            "Expected follow-up user message for tool response image"
        )
        followup_msg = messages_dicts[3]
        assert followup_msg["role"] == "user", (
            "Follow-up image message must be user role"
        )
        followup_content = followup_msg["content"]
        assert isinstance(followup_content, list)
        image_parts = [
            p
            for p in followup_content
            if isinstance(p, dict) and p.get("type") == "image_url"
        ]
        assert len(image_parts) == 1, "Follow-up message should have exactly one image"
    finally:
        image_path.unlink(missing_ok=True)


def test_message_conversion_tool_response_with_image_no_vision():
    """Tool responses with images on non-vision models should not generate follow-up messages."""
    import tempfile
    from pathlib import Path

    init_tools(allowlist=["save"])

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        image_path = Path(f.name)

    try:
        messages = [
            Message(role="user", content="Can you view this image?"),
            Message(
                role="assistant",
                content='@ipython(call1): {"code": "view_image(\'/path/image.png\')"}',
            ),
            Message(
                role="system",
                content="Viewing image",
                call_id="call1",
                files=[image_path],
            ),
        ]

        tool_save = get_tool("save")
        assert tool_save

        # Use a model without vision support
        model = get_model("openai/gpt-3.5-turbo")
        messages_dicts, _ = _prepare_messages_for_api(messages, model.full, [tool_save])

        # No follow-up user message should be added for non-vision models
        assert len(messages_dicts) == 3, (
            "Non-vision model should not generate follow-up image message"
        )
        assert messages_dicts[2]["role"] == "tool"
    finally:
        image_path.unlink(missing_ok=True)


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
                assert call_kwargs["timeout"] == 900.0, (
                    f"Provider {provider} didn't receive correct timeout"
                )


def test_timeout_invalid_value(monkeypatch):
    """Test that invalid timeout values raise ValueError with clear message."""
    from unittest.mock import patch

    import pytest

    import gptme.llm.llm_openai as llm_openai
    from gptme.config import get_config

    # Set invalid timeout and dummy API key for test environment
    monkeypatch.setenv("LLM_API_TIMEOUT", "not-a-number")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-invalid-timeout-test")

    # Clear the clients cache
    llm_openai.clients.clear()

    # Get config instance
    config = get_config()

    # Should raise ValueError on invalid config
    with (
        patch("openai.OpenAI"),
        pytest.raises(ValueError, match="Invalid LLM_API_TIMEOUT"),
    ):
        llm_openai.init("openai", config)


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
    content_0 = messages_list[0]["content"]
    assert isinstance(content_0, list)
    first_part = content_0[0]
    assert isinstance(first_part, dict)
    assert "<system>" in first_part["text"]

    assert messages_list[1]["role"] == "user"  # User message stays user

    assert messages_list[2]["role"] == "assistant"  # Assistant with tool call
    assert "tool_calls" in messages_list[2]
    assert messages_list[2]["tool_calls"][0]["id"] == "call_123"

    # The critical assertion: tool result should be role="tool", not role="user"
    assert messages_list[3]["role"] == "tool"  # Tool result preserved!
    assert messages_list[3]["tool_call_id"] == "call_123"
    content_3 = messages_list[3]["content"]
    assert isinstance(content_3, list)
    first_part_3 = content_3[0]
    assert isinstance(first_part_3, dict)
    assert first_part_3["text"] == "Saved to file.txt"


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


# Tests for OpenAI retry logic
class TestOpenAIRetryLogic:
    """Tests for OpenAI API retry logic."""

    def test_handle_openai_transient_error_rate_limit(self):
        """Test that rate limit errors trigger retry."""
        from unittest.mock import MagicMock, patch

        from openai import RateLimitError

        from gptme.llm.llm_openai import _handle_openai_transient_error

        # Create a mock RateLimitError
        mock_response = MagicMock()
        mock_response.status_code = 429
        error = RateLimitError("Rate limit exceeded", response=mock_response, body=None)

        # Should not raise on first attempts (will sleep and return)
        with patch("time.sleep"):
            # On attempt 0 (not last attempt), should just sleep
            _handle_openai_transient_error(
                error, attempt=0, max_retries=3, base_delay=0.1
            )

    def test_handle_openai_transient_error_server_error(self):
        """Test that 5xx server errors trigger retry."""
        from unittest.mock import MagicMock, patch

        from openai import APIStatusError

        from gptme.llm.llm_openai import _handle_openai_transient_error

        # Create a mock 500 error
        mock_response = MagicMock()
        mock_response.status_code = 500
        error = APIStatusError(
            "Internal server error", response=mock_response, body=None
        )

        with patch("time.sleep"):
            # Should retry on 500 error
            _handle_openai_transient_error(
                error, attempt=0, max_retries=3, base_delay=0.1
            )

    def test_handle_openai_transient_error_raises_on_client_error(self):
        """Test that client errors (4xx except 429) are raised immediately."""
        from unittest.mock import MagicMock

        from openai import APIStatusError

        from gptme.llm.llm_openai import _handle_openai_transient_error

        # Create a mock 400 error (client error, not transient)
        mock_response = MagicMock()
        mock_response.status_code = 400
        error = APIStatusError("Bad request", response=mock_response, body=None)

        # Should raise immediately on 400 error (not transient)
        with pytest.raises(APIStatusError):
            _handle_openai_transient_error(
                error, attempt=0, max_retries=3, base_delay=0.1
            )

    def test_handle_openai_transient_error_raises_on_max_retries(self):
        """Test that error is raised after max retries."""
        from unittest.mock import MagicMock

        from openai import RateLimitError

        from gptme.llm.llm_openai import _handle_openai_transient_error

        mock_response = MagicMock()
        mock_response.status_code = 429
        error = RateLimitError("Rate limit exceeded", response=mock_response, body=None)

        # On final attempt, should raise
        with pytest.raises(RateLimitError):
            _handle_openai_transient_error(
                error, attempt=2, max_retries=3, base_delay=0.1
            )

    def test_handle_openai_transient_error_openrouter_overloaded(self):
        """Test that OpenRouter Anthropic 'Overloaded' errors trigger retry.

        OpenRouter proxies Anthropic models and may return overloaded errors
        with the message in the body rather than the message attribute.
        See: https://github.com/ErikBjare/bob/issues/287
        """
        from unittest.mock import MagicMock, patch

        from openai import APIStatusError

        from gptme.llm.llm_openai import _handle_openai_transient_error

        # Test with overload in body dict
        mock_response = MagicMock()
        mock_response.status_code = 400  # Not 5xx, to test body-based detection
        error = APIStatusError(
            "Error", response=mock_response, body={"error": "Overloaded"}
        )

        # Test retry path: on attempt 0, should sleep and return (retry)
        with patch("time.sleep") as mock_sleep:
            _handle_openai_transient_error(
                error, attempt=0, max_retries=3, base_delay=0.1
            )
            # Assert retry path was taken (sleep called = will retry)
            mock_sleep.assert_called_once()

        # Test with overload in string representation
        error_str = APIStatusError("Overloaded", response=mock_response, body=None)

        with patch("time.sleep") as mock_sleep:
            _handle_openai_transient_error(
                error_str, attempt=0, max_retries=3, base_delay=0.1
            )
            # Assert retry path was taken
            mock_sleep.assert_called_once()

        # Test non-retry path: on last attempt, should raise the error
        with patch("time.sleep") as mock_sleep:
            import pytest

            with pytest.raises(APIStatusError):
                _handle_openai_transient_error(
                    error, attempt=2, max_retries=3, base_delay=0.1
                )
            # On last attempt, should not sleep (no retry)
            mock_sleep.assert_not_called()

    def test_retry_decorator_retries_on_transient_error(self, monkeypatch):
        """Test that the retry decorator properly retries on transient errors."""
        from unittest.mock import MagicMock, patch

        from openai import RateLimitError

        from gptme.llm.llm_openai import retry_on_openai_error

        # Clear test max_retries override to test actual retry behavior
        monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)

        call_count = 0
        mock_response = MagicMock()
        mock_response.status_code = 429

        @retry_on_openai_error(max_retries=3, base_delay=0.01)
        def flaky_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RateLimitError(
                    "Rate limit exceeded", response=mock_response, body=None
                )
            return "success"

        with patch("time.sleep"):
            result = flaky_function()

        assert result == "success"
        assert call_count == 3

    def test_retry_generator_decorator_retries_on_transient_error(self, monkeypatch):
        """Test that the generator retry decorator properly retries on transient errors."""
        from unittest.mock import MagicMock, patch

        from openai import RateLimitError

        from gptme.llm.llm_openai import retry_generator_on_openai_error

        # Clear test max_retries override to test actual retry behavior
        monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)

        call_count = 0
        mock_response = MagicMock()
        mock_response.status_code = 429

        @retry_generator_on_openai_error(max_retries=3, base_delay=0.01)
        def flaky_generator():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RateLimitError(
                    "Rate limit exceeded", response=mock_response, body=None
                )
            yield "chunk1"
            yield "chunk2"
            return "metadata"

        with patch("time.sleep"):
            results = list(flaky_generator())

        assert results == ["chunk1", "chunk2"]
        assert call_count == 2

    def test_retry_generator_only_retries_before_yield(self, monkeypatch):
        """Test that retry_generator_on_openai_error only retries if no content has been yielded.

        This prevents duplicate output when an error occurs mid-stream.
        Issue: https://github.com/gptme/gptme/issues/1030 (Finding 6)
        """
        from unittest.mock import MagicMock, patch

        from openai import RateLimitError

        from gptme.llm.llm_openai import retry_generator_on_openai_error

        # Clear test max_retries override to test actual retry behavior
        monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)

        mock_response = MagicMock()
        mock_response.status_code = 429

        def make_rate_limit_error():
            return RateLimitError(
                "Rate limit exceeded", response=mock_response, body=None
            )

        # Test 1: Should retry when error occurs before any yield
        call_count = 0

        @retry_generator_on_openai_error(max_retries=3, base_delay=0.01)
        def gen_fails_before_yield():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise make_rate_limit_error()
            yield "success"

        with patch("time.sleep"):
            result = list(gen_fails_before_yield())

        assert result == ["success"], f"Expected ['success'], got {result}"
        assert call_count == 3, f"Expected 3 calls (2 retries), got {call_count}"

    def test_retry_generator_no_retry_after_yield(self, monkeypatch):
        """Test that retry_generator_on_openai_error does NOT retry after content has been yielded.

        This prevents duplicate output when an error occurs mid-stream.
        Issue: https://github.com/gptme/gptme/issues/1030 (Finding 6)
        """
        from unittest.mock import MagicMock, patch

        from openai import RateLimitError

        from gptme.llm.llm_openai import retry_generator_on_openai_error

        # Clear test max_retries override to test actual retry behavior
        monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)

        mock_response = MagicMock()
        mock_response.status_code = 429

        @retry_generator_on_openai_error(max_retries=3, base_delay=0.01)
        def gen_fails_after_yield():
            yield "chunk1"
            yield "chunk2"
            raise RateLimitError(
                "Rate limit exceeded", response=mock_response, body=None
            )

        # Should NOT retry when error occurs after yielding (would cause duplicates)
        collected = []
        with pytest.raises(RateLimitError), patch("time.sleep"):
            for chunk in gen_fails_after_yield():
                collected.append(chunk)  # noqa: PERF402

        # Should have received chunks before error, and NOT duplicated
        assert collected == [
            "chunk1",
            "chunk2",
        ], f"Expected ['chunk1', 'chunk2'], got {collected}"

    def test_retry_generator_preserves_return_value(self, monkeypatch):
        """Test that retry_generator_on_openai_error preserves generator return values."""
        from gptme.llm.llm_openai import retry_generator_on_openai_error

        # Clear test max_retries override
        monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)

        @retry_generator_on_openai_error(max_retries=3, base_delay=0.01)
        def gen_with_return():
            yield "chunk1"
            yield "chunk2"
            return {"metadata": "value"}

        gen = gen_with_return()
        chunks = []
        return_value = None
        try:
            while True:
                chunks.append(next(gen))
        except StopIteration as e:
            return_value = e.value

        assert chunks == ["chunk1", "chunk2"]
        assert return_value == {"metadata": "value"}


def test_transform_msgs_for_openrouter_reasoning_tool_calls():
    """Test that OpenRouter reasoning models get empty reasoning_content for tool_calls.

    This fixes the error: "thinking is enabled but reasoning_content is missing
    in assistant tool call message" when using models like Moonshot AI Kimi K2.5
    with --tool-format tool.
    """
    from typing import Any

    from gptme.llm.llm_openai import _transform_msgs_for_special_provider
    from gptme.llm.models import ModelMeta

    # Moonshot AI Kimi model accessed via OpenRouter with reasoning support
    openrouter_reasoning_model = ModelMeta(
        provider="openrouter",
        model="moonshotai/kimi-k2.5",
        context=262_144,
        supports_reasoning=True,
    )

    # Assistant message with tool_calls but no content
    messages: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "shell",
                        "arguments": '{"command": "ls"}',
                    },
                }
            ],
        },
    ]

    result = list(
        _transform_msgs_for_special_provider(messages, openrouter_reasoning_model)
    )

    # OpenRouter reasoning models need reasoning_content for assistant messages with tool_calls
    assert "reasoning_content" in result[0]
    assert result[0]["reasoning_content"] == ""
    assert result[0]["tool_calls"] == messages[0]["tool_calls"]


def test_transform_msgs_for_openrouter_non_reasoning():
    """Test that OpenRouter models without reasoning support are unchanged."""
    from typing import Any

    from gptme.llm.llm_openai import _transform_msgs_for_special_provider
    from gptme.llm.models import ModelMeta

    # Regular OpenRouter model without reasoning
    openrouter_model = ModelMeta(
        provider="openrouter",
        model="openai/gpt-4",
        context=128_000,
        supports_reasoning=False,
    )

    messages: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "shell",
                        "arguments": '{"command": "ls"}',
                    },
                }
            ],
        },
    ]

    result = list(_transform_msgs_for_special_provider(messages, openrouter_model))

    # Non-reasoning models should NOT get reasoning_content added
    assert "reasoning_content" not in result[0]
    assert result[0]["tool_calls"] == messages[0]["tool_calls"]


def test_transform_msgs_extracts_reasoning_content():
    """Test that OpenRouter reasoning models extract thinking content from <think> tags."""
    from typing import Any

    from gptme.llm.llm_openai import _transform_msgs_for_special_provider
    from gptme.llm.models import ModelMeta

    openrouter_reasoning_model = ModelMeta(
        provider="openrouter",
        model="moonshotai/kimi-k2.5",
        context=262_144,
        supports_reasoning=True,
    )

    # Message with thinking content in <think> tags
    messages: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "content": "<think>I need to run ls to list files</think>\n\nLet me check the files.",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "shell",
                        "arguments": '{"command": "ls"}',
                    },
                }
            ],
        },
    ]

    result = list(
        _transform_msgs_for_special_provider(messages, openrouter_reasoning_model)
    )

    # Should extract the actual reasoning content
    assert "reasoning_content" in result[0]
    assert result[0]["reasoning_content"] == "I need to run ls to list files"

    # Should remove <think> tags from content to prevent context duplication
    assert result[0]["content"] == "Let me check the files."
    assert "<think>" not in result[0]["content"]


def test_transform_msgs_handles_list_content():
    """Test that OpenRouter reasoning models correctly handle list content (multi-modal messages).

    This fixes the error: "expected string or bytes-like object, got 'list'"
    when content is a list of content parts instead of a string.
    """
    from typing import Any

    from gptme.llm.llm_openai import _transform_msgs_for_special_provider
    from gptme.llm.models import ModelMeta

    openrouter_reasoning_model = ModelMeta(
        provider="openrouter",
        model="moonshotai/kimi-k2.5",
        context=262_144,
        supports_reasoning=True,
    )

    # Message with list content (multi-modal format)
    messages: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "<think>Reasoning here</think>"},
                {"type": "text", "text": "Actual response content"},
            ],
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "shell",
                        "arguments": '{"command": "ls"}',
                    },
                }
            ],
        },
    ]

    result = list(
        _transform_msgs_for_special_provider(messages, openrouter_reasoning_model)
    )

    # Should extract reasoning from list content
    assert "reasoning_content" in result[0]
    assert result[0]["reasoning_content"] == "Reasoning here"

    # Content should be cleaned (reasoning extracted)
    result_content = result[0]["content"]
    assert isinstance(result_content, str)
    assert "<think>" not in result_content
    assert "Actual response content" in result_content


def test_transform_msgs_handles_string_list_content():
    """Test that list content with string items (not dicts) is handled correctly."""
    from typing import Any

    from gptme.llm.llm_openai import _transform_msgs_for_special_provider
    from gptme.llm.models import ModelMeta

    openrouter_reasoning_model = ModelMeta(
        provider="openrouter",
        model="moonshotai/kimi-k2.5",
        context=262_144,
        supports_reasoning=True,
    )

    # Message with list of strings (edge case)
    messages: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "content": ["<think>Thinking</think>", "Response text"],
            "tool_calls": [
                {
                    "id": "call_456",
                    "type": "function",
                    "function": {
                        "name": "ipython",
                        "arguments": '{"code": "1+1"}',
                    },
                }
            ],
        },
    ]

    result = list(
        _transform_msgs_for_special_provider(messages, openrouter_reasoning_model)
    )

    # Should handle string list items
    assert "reasoning_content" in result[0]
    assert result[0]["reasoning_content"] == "Thinking"


def test_transform_msgs_openrouter_removes_empty_content():
    """Test that OpenRouter reasoning models remove content when it's empty after stripping
    <think> tags, to avoid provider errors (e.g. Z.AI/GLM rejects empty text content).

    Regression test for: 'messages[2].content[0].text:text cannot be empty'
    """
    from typing import Any

    from gptme.llm.llm_openai import _transform_msgs_for_special_provider
    from gptme.llm.models import ModelMeta

    # Z.AI GLM model accessed via OpenRouter with reasoning support
    openrouter_reasoning_model = ModelMeta(
        provider="openrouter",
        model="z-ai/glm-5",
        context=131_072,
        supports_reasoning=True,
    )

    # Assistant message where content is ONLY thinking (no actual text after stripping)
    # This happens when GLM outputs <think>reasoning</think> with no text response
    messages: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "content": "<think>I should use IPython to compute primes</think>\n",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "ipython",
                        "arguments": '{"code": "print([p for p in range(2, 50)])"}',
                    },
                }
            ],
        },
    ]

    result = list(
        _transform_msgs_for_special_provider(messages, openrouter_reasoning_model)
    )

    # Reasoning should be extracted
    assert "reasoning_content" in result[0]
    assert "IPython" in result[0]["reasoning_content"]

    # Content should be REMOVED (not set to empty string) to avoid provider rejection
    assert "content" not in result[0], (
        "Empty content should be removed to avoid Z.AI/GLM rejection of empty text"
    )


def test_merge_consecutive_preserves_files():
    """Test that _merge_consecutive preserves files when merging messages.

    This is critical for OpenRouter models with supports_reasoning=True,
    which use _prep_deepseek_reasoner which calls _merge_consecutive.
    If files (like images) are not preserved, vision features break.
    """
    from pathlib import Path

    from gptme.llm.llm_openai import _merge_consecutive
    from gptme.message import Message

    # Simulate what happens with _prep_o1 + _merge_consecutive:
    # System messages become user messages, then get merged with the actual user message
    system_as_user1 = Message(
        "user",
        "<system>You are a helpful assistant</system>",
    )
    system_as_user2 = Message(
        "user",
        "<system>Context files here</system>",
    )
    user_with_image = Message(
        "user",
        "/path/to/image.png",
        files=[Path("/path/to/image.png")],
        file_hashes={"/path/to/image.png": "abc123"},
    )

    # Merge all three consecutive user messages
    msgs = [system_as_user1, system_as_user2, user_with_image]
    merged = list(_merge_consecutive(msgs))

    # Should result in a single merged message
    assert len(merged) == 1
    merged_msg = merged[0]

    # The image file should be preserved
    assert len(merged_msg.files) == 1
    assert Path("/path/to/image.png") in merged_msg.files

    # File hashes should be preserved
    assert merged_msg.file_hashes.get("/path/to/image.png") == "abc123"

    # All content should be merged
    assert "<system>You are a helpful assistant</system>" in merged_msg.content
    assert "<system>Context files here</system>" in merged_msg.content
    assert "/path/to/image.png" in merged_msg.content


def test_prep_deepseek_reasoner_preserves_image_files():
    """Test that _prep_deepseek_reasoner preserves image files.

    This is the actual flow for OpenRouter reasoning models like Claude Opus.
    """
    from pathlib import Path

    from gptme.llm.llm_openai import _prep_deepseek_reasoner
    from gptme.message import Message

    # Simulate a typical conversation start:
    # 1. Main system prompt
    # 2. Context files system message
    # 3. User message with pasted image
    messages = [
        Message("system", "You are a helpful assistant."),
        Message("system", "## Context files\n- README.md"),
        Message("system", "Token budget: 200000"),
        Message(
            "user",
            "/path/to/screenshot.png",
            files=[Path("/path/to/screenshot.png")],
            file_hashes={"/path/to/screenshot.png": "hash123"},
        ),
    ]

    # Apply the deepseek reasoner prep (used for supports_reasoning models)
    result = list(_prep_deepseek_reasoner(messages))

    # Should have: first system message unchanged, then merged user messages
    assert len(result) == 2
    assert result[0].role == "system"  # First message unchanged
    assert result[1].role == "user"  # Merged user messages

    # The image file must be preserved in the merged user message
    assert len(result[1].files) == 1
    assert Path("/path/to/screenshot.png") in result[1].files
    assert result[1].file_hashes.get("/path/to/screenshot.png") == "hash123"


# --- Tests for extra_body (OpenRouter provider routing) ---


class TestExtraBody:
    """Tests for OpenRouter extra_body provider routing preferences."""

    @staticmethod
    def _make_model(model: str, **kwargs):  # type: ignore[no-untyped-def]
        from gptme.llm.models.types import ModelMeta

        return ModelMeta(
            provider=kwargs.pop("provider", "openrouter"),
            model=model,
            context=kwargs.pop("context", 128000),
            **kwargs,
        )

    def test_non_openrouter_returns_empty(self):
        from gptme.llm.llm_openai import extra_body

        meta = self._make_model("gpt-4o", provider="openai")
        result = extra_body("openai", meta)
        assert result == {}

    def test_openrouter_has_require_parameters(self):
        from gptme.llm.llm_openai import extra_body

        meta = self._make_model("anthropic/claude-sonnet-4-20250514")
        result = extra_body("openrouter", meta)
        assert result["provider"]["require_parameters"] is True

    def test_openrouter_has_data_collection_deny_by_default_for_non_reasoning(
        self, monkeypatch
    ):
        """Non-reasoning models default to data_collection='deny' for privacy."""
        from gptme.llm.llm_openai import extra_body

        monkeypatch.delenv("OPENROUTER_DATA_COLLECTION", raising=False)
        monkeypatch.delenv("GPTME_OPENROUTER_DATA_COLLECTION", raising=False)
        meta = self._make_model("anthropic/claude-sonnet-4-20250514")
        result = extra_body("openrouter", meta)
        assert result["provider"]["data_collection"] == "deny"

    def test_openrouter_provider_override_with_at_sign(self):
        from gptme.llm.llm_openai import extra_body

        meta = self._make_model("anthropic/claude-sonnet-4-20250514@anthropic")
        result = extra_body("openrouter", meta)
        prov = result["provider"]
        assert prov["order"] == ["anthropic"]
        assert prov["allow_fallbacks"] is False
        # Should still have require_parameters (non-reasoning model)
        assert prov["require_parameters"] is True

    def test_openrouter_no_provider_override(self):
        from gptme.llm.llm_openai import extra_body

        meta = self._make_model("anthropic/claude-sonnet-4-20250514")
        result = extra_body("openrouter", meta)
        prov = result["provider"]
        assert "order" not in prov
        assert "allow_fallbacks" not in prov

    def test_openrouter_usage_accounting(self):
        from gptme.llm.llm_openai import extra_body

        meta = self._make_model("openai/gpt-4o")
        result = extra_body("openrouter", meta)
        assert result["usage"] == {"include": True}

    def test_openrouter_reasoning_model(self):
        from gptme.llm.llm_openai import extra_body

        meta = self._make_model("openai/o3", supports_reasoning=True)
        result = extra_body("openrouter", meta)
        assert "reasoning" in result
        assert result["reasoning"]["enabled"] is True

    def test_openrouter_reasoning_model_no_require_parameters(self):
        """Reasoning models must not set require_parameters=True.

        The combination of require_parameters + reasoning extension can
        eliminate all available providers — the reasoning body parameter
        is not universally supported by all OpenRouter providers.
        """
        from gptme.llm.llm_openai import extra_body

        meta = self._make_model(
            "anthropic/claude-sonnet-4-20250514", supports_reasoning=True
        )
        result = extra_body("openrouter", meta)
        assert "reasoning" in result
        assert "require_parameters" not in result["provider"]

    def test_openrouter_non_reasoning_model_has_require_parameters(self):
        """Non-reasoning models should still set require_parameters=True."""
        from gptme.llm.llm_openai import extra_body

        meta = self._make_model("anthropic/claude-sonnet-4-20250514")
        result = extra_body("openrouter", meta)
        assert "reasoning" not in result
        assert result["provider"]["require_parameters"] is True

    def test_openrouter_data_collection_env_override_allow(self, monkeypatch):
        from gptme.llm.llm_openai import extra_body

        monkeypatch.setenv("OPENROUTER_DATA_COLLECTION", "allow")
        meta = self._make_model("anthropic/claude-sonnet-4-20250514")
        result = extra_body("openrouter", meta)
        assert result["provider"]["data_collection"] == "allow"

    def test_openrouter_data_collection_env_override_deny(self, monkeypatch):
        from gptme.llm.llm_openai import extra_body

        monkeypatch.setenv("OPENROUTER_DATA_COLLECTION", "deny")
        meta = self._make_model("anthropic/claude-sonnet-4-20250514")
        result = extra_body("openrouter", meta)
        assert result["provider"]["data_collection"] == "deny"

    def test_openrouter_reasoning_model_no_data_collection_by_default(
        self, monkeypatch
    ):
        """Reasoning models don't set data_collection by default.

        The triple constraint (require_parameters + reasoning + data_collection="deny")
        eliminates all available OpenRouter providers, causing 400 errors.
        """
        from gptme.llm.llm_openai import extra_body

        monkeypatch.delenv("OPENROUTER_DATA_COLLECTION", raising=False)
        monkeypatch.delenv("GPTME_OPENROUTER_DATA_COLLECTION", raising=False)
        meta = self._make_model(
            "anthropic/claude-sonnet-4-20250514", supports_reasoning=True
        )
        result = extra_body("openrouter", meta)
        assert "reasoning" in result
        assert "data_collection" not in result["provider"]

    def test_openrouter_data_collection_gptme_prefixed_env(self, monkeypatch):
        """GPTME_OPENROUTER_DATA_COLLECTION takes precedence over bare form."""
        from gptme.llm.llm_openai import extra_body

        monkeypatch.setenv("GPTME_OPENROUTER_DATA_COLLECTION", "allow")
        monkeypatch.delenv("OPENROUTER_DATA_COLLECTION", raising=False)
        meta = self._make_model("anthropic/claude-sonnet-4-20250514")
        result = extra_body("openrouter", meta)
        assert result["provider"]["data_collection"] == "allow"

    # --- Quantization routing tests ---

    def test_openrouter_no_quantization_by_default(self, monkeypatch):
        from gptme.llm.llm_openai import extra_body

        monkeypatch.delenv("OPENROUTER_QUANTIZATION", raising=False)
        monkeypatch.delenv("GPTME_OPENROUTER_QUANTIZATION", raising=False)
        meta = self._make_model("anthropic/claude-sonnet-4-20250514")
        result = extra_body("openrouter", meta)
        assert "quantizations" not in result["provider"]

    def test_openrouter_quantization_single(self, monkeypatch):
        from gptme.llm.llm_openai import extra_body

        monkeypatch.setenv("OPENROUTER_QUANTIZATION", "fp16")
        monkeypatch.delenv("GPTME_OPENROUTER_QUANTIZATION", raising=False)
        meta = self._make_model("anthropic/claude-sonnet-4-20250514")
        result = extra_body("openrouter", meta)
        assert result["provider"]["quantizations"] == ["fp16"]

    def test_openrouter_quantization_multiple(self, monkeypatch):
        from gptme.llm.llm_openai import extra_body

        monkeypatch.setenv("OPENROUTER_QUANTIZATION", "fp16,bf16,fp8")
        monkeypatch.delenv("GPTME_OPENROUTER_QUANTIZATION", raising=False)
        meta = self._make_model("anthropic/claude-sonnet-4-20250514")
        result = extra_body("openrouter", meta)
        assert result["provider"]["quantizations"] == ["fp16", "bf16", "fp8"]

    def test_openrouter_quantization_whitespace_handling(self, monkeypatch):
        from gptme.llm.llm_openai import extra_body

        monkeypatch.setenv("OPENROUTER_QUANTIZATION", " fp16 , int8 , int4 ")
        monkeypatch.delenv("GPTME_OPENROUTER_QUANTIZATION", raising=False)
        meta = self._make_model("anthropic/claude-sonnet-4-20250514")
        result = extra_body("openrouter", meta)
        assert result["provider"]["quantizations"] == ["fp16", "int8", "int4"]

    def test_openrouter_quantization_empty_string_ignored(self, monkeypatch):
        from gptme.llm.llm_openai import extra_body

        monkeypatch.setenv("OPENROUTER_QUANTIZATION", "")
        monkeypatch.delenv("GPTME_OPENROUTER_QUANTIZATION", raising=False)
        meta = self._make_model("anthropic/claude-sonnet-4-20250514")
        result = extra_body("openrouter", meta)
        assert "quantizations" not in result["provider"]

    def test_openrouter_quantization_gptme_prefixed_env(self, monkeypatch):
        """GPTME_OPENROUTER_QUANTIZATION takes precedence over bare form."""
        from gptme.llm.llm_openai import extra_body

        monkeypatch.setenv("GPTME_OPENROUTER_QUANTIZATION", "int4")
        monkeypatch.delenv("OPENROUTER_QUANTIZATION", raising=False)
        meta = self._make_model("anthropic/claude-sonnet-4-20250514")
        result = extra_body("openrouter", meta)
        assert result["provider"]["quantizations"] == ["int4"]


class TestRecordUsageCacheTokens:
    """Tests for _record_usage cache token extraction.

    Regression guard for a bug where OpenRouter-proxied Anthropic calls were
    dropping cache_creation_input_tokens on the floor, causing telemetry and
    cost calculations to under-report cache-write activity.
    """

    @staticmethod
    def _make_usage(
        *,
        prompt_tokens,
        completion_tokens,
        cached_tokens=None,
        cache_creation_input_tokens=None,
    ):
        """Build an OpenAI-SDK CompletionUsage mirroring provider responses.

        OpenAI SDK's pydantic models allow extras, so OpenRouter's Anthropic
        passthrough field `cache_creation_input_tokens` is preserved as a raw
        attribute on the validated object.
        """
        from openai.types.completion_usage import CompletionUsage

        raw: dict = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        if cached_tokens is not None:
            raw["prompt_tokens_details"] = {
                "cached_tokens": cached_tokens,
                "audio_tokens": 0,
            }
        if cache_creation_input_tokens is not None:
            raw["cache_creation_input_tokens"] = cache_creation_input_tokens
        return CompletionUsage.model_validate(raw)

    def test_openai_direct_no_cache_fields(self):
        """Direct OpenAI calls without caching — no cache tokens recorded."""
        from gptme.llm.llm_openai import _record_usage

        usage = self._make_usage(prompt_tokens=1000, completion_tokens=200)
        metadata = _record_usage(usage, "openai/gpt-4o")

        assert metadata is not None
        assert metadata["usage"]["input_tokens"] == 1000
        assert metadata["usage"]["output_tokens"] == 200
        assert "cache_read_tokens" not in metadata["usage"]
        assert "cache_creation_tokens" not in metadata["usage"]

    def test_openai_cached_tokens_only(self):
        """OpenAI-style caching: cached_tokens populated, no cache_creation."""
        from gptme.llm.llm_openai import _record_usage

        usage = self._make_usage(
            prompt_tokens=1500, completion_tokens=100, cached_tokens=500
        )
        metadata = _record_usage(usage, "openai/gpt-4o")

        assert metadata is not None
        # input_tokens should exclude cache_read to avoid double counting
        assert metadata["usage"]["input_tokens"] == 1000
        assert metadata["usage"]["cache_read_tokens"] == 500
        assert "cache_creation_tokens" not in metadata["usage"]

    def test_openrouter_anthropic_cache_creation_extracted(self):
        """OpenRouter-proxied Anthropic: cache_creation_input_tokens extracted.

        Regression test: prior to this fix, cache_creation_input_tokens was
        silently dropped for any model routed through llm_openai.py.
        """
        from gptme.llm.llm_openai import _record_usage

        usage = self._make_usage(
            prompt_tokens=3000,
            completion_tokens=200,
            cached_tokens=500,
            cache_creation_input_tokens=2000,
        )
        metadata = _record_usage(usage, "openrouter/anthropic/claude-sonnet-4.5")

        assert metadata is not None
        # input_tokens = prompt_tokens - cache_read - cache_creation
        # = 3000 - 500 - 2000 = 500
        assert metadata["usage"]["input_tokens"] == 500
        assert metadata["usage"]["cache_read_tokens"] == 500
        assert metadata["usage"]["cache_creation_tokens"] == 2000

    def test_openrouter_anthropic_cache_creation_only(self):
        """First cache-write call: creation tokens but no reads yet."""
        from gptme.llm.llm_openai import _record_usage

        usage = self._make_usage(
            prompt_tokens=2500,
            completion_tokens=150,
            cache_creation_input_tokens=2000,
        )
        metadata = _record_usage(usage, "openrouter/anthropic/claude-haiku-4.5")

        assert metadata is not None
        # No cached_tokens field at all — just cache_creation
        assert metadata["usage"]["input_tokens"] == 500
        assert metadata["usage"]["cache_creation_tokens"] == 2000
        assert "cache_read_tokens" not in metadata["usage"]

    def test_openrouter_anthropic_cache_creation_zero_still_recorded(self):
        """Explicit 0 for cache_creation should still be recorded.

        This distinguishes 'provider returned 0' (cache disabled/miss) from
        'field not present' (legacy/non-supporting provider).
        """
        from gptme.llm.llm_openai import _record_usage

        usage = self._make_usage(
            prompt_tokens=1000,
            completion_tokens=100,
            cached_tokens=0,
            cache_creation_input_tokens=0,
        )
        metadata = _record_usage(usage, "openrouter/anthropic/claude-sonnet-4.5")

        assert metadata is not None
        # Both explicit zeros preserved in metadata (truthy-check would drop them)
        assert metadata["usage"]["cache_read_tokens"] == 0
        assert metadata["usage"]["cache_creation_tokens"] == 0


class TestMaybeApplyVerbosity:
    """Tests for OPENAI_VERBOSITY request-body handling on GPT-5+ models."""

    def test_unset_skips(self, monkeypatch):
        monkeypatch.setattr(llm_openai, "OPENAI_VERBOSITY", None)
        body: dict = {}
        model = get_model("openai/gpt-5")
        _maybe_apply_verbosity(body, model)
        assert "verbosity" not in body

    def test_non_gpt5_model_skipped(self, monkeypatch):
        monkeypatch.setattr(llm_openai, "OPENAI_VERBOSITY", "high")
        body: dict = {}
        model = get_model("openai/gpt-4o")
        _maybe_apply_verbosity(body, model)
        assert "verbosity" not in body

    @pytest.mark.parametrize("level", ["low", "medium", "high"])
    def test_valid_level_applied_to_gpt5(self, monkeypatch, level):
        monkeypatch.setattr(llm_openai, "OPENAI_VERBOSITY", level)
        body: dict = {}
        model = get_model("openai/gpt-5")
        _maybe_apply_verbosity(body, model)
        assert body["verbosity"] == level

    def test_valid_level_applied_to_gpt5_5(self, monkeypatch):
        monkeypatch.setattr(llm_openai, "OPENAI_VERBOSITY", "low")
        body: dict = {}
        model = get_model("openai/gpt-5.5")
        _maybe_apply_verbosity(body, model)
        assert body["verbosity"] == "low"

    def test_invalid_level_ignored(self, monkeypatch, caplog):
        monkeypatch.setattr(llm_openai, "OPENAI_VERBOSITY", "verbose")
        monkeypatch.setattr(llm_openai, "_verbosity_warned", False)
        body: dict = {}
        model = get_model("openai/gpt-5")
        import logging

        with caplog.at_level(logging.WARNING, logger="gptme.llm.llm_openai"):
            _maybe_apply_verbosity(body, model)
        assert "verbosity" not in body
        assert "OPENAI_VERBOSITY" in caplog.text
        assert "verbose" in caplog.text

    def test_invalid_level_warns_only_once(self, monkeypatch, caplog):
        monkeypatch.setattr(llm_openai, "OPENAI_VERBOSITY", "verbose")
        monkeypatch.setattr(llm_openai, "_verbosity_warned", False)
        model = get_model("openai/gpt-5")
        import logging

        with caplog.at_level(logging.WARNING, logger="gptme.llm.llm_openai"):
            _maybe_apply_verbosity({}, model)
            _maybe_apply_verbosity({}, model)
        assert caplog.text.count("OPENAI_VERBOSITY") == 1
