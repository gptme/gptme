from gptme.llm.llm_anthropic import _prepare_messages_for_api
from gptme.message import Message
from gptme.tools import get_tool, init_tools


def test_message_conversion():
    messages = [
        Message(role="system", content="Initial Message", pinned=True, hide=True),
        Message(role="system", content="Project prompt", hide=True),
        Message(role="user", content="First user prompt"),
    ]

    messages_dicts, system_messages, tools = _prepare_messages_for_api(messages, None)

    assert tools is None

    assert system_messages == [
        {
            "type": "text",
            "text": "Initial Message",
            "cache_control": {"type": "ephemeral"},
        }
    ]

    assert list(messages_dicts) == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "<system>Project prompt</system>\n\nFirst user prompt",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
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

    messages_dicts, _, _ = _prepare_messages_for_api(messages, None)

    assert messages_dicts == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "<system>Project prompt</system>\n\nFirst user prompt",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
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
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "<system>Saved to toto.txt</system>",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
    ]


def test_message_conversion_with_tools():
    init_tools(allowlist=["save"])

    messages = [
        Message(role="system", content="Initial Message", pinned=True, hide=True),
        Message(role="system", content="Project prompt", hide=True),
        Message(role="user", content="First user prompt"),
        Message(
            role="assistant",
            content='<thinking>\nSomething\n</thinking>\n@save(tool_call_id): {"path": "path.txt", "content": "file_content"}',
        ),
        Message(role="system", content="Saved to toto.txt", call_id="tool_call_id"),
        Message(role="system", content="(Modified by user)", call_id="tool_call_id"),
    ]

    tool_save = get_tool("save")

    assert tool_save

    messages_dicts, _, tools = _prepare_messages_for_api(messages, [tool_save])

    assert tools == [
        {
            "name": "save",
            "description": "Create or overwrite a file with the given content.\n\n"
            "The path can be relative to the current directory, or absolute.\n"
            "If the current directory changes, the path will be relative to the "
            "new directory.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The path of the file"},
                    "content": {"type": "string", "description": "The content to save"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        }
    ]

    assert list(messages_dicts) == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "<system>Project prompt</system>\n\nFirst user prompt",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "<thinking>\nSomething\n</thinking>"},
                {
                    "type": "tool_use",
                    "id": "tool_call_id",
                    "name": "save",
                    "input": {"path": "path.txt", "content": "file_content"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "content": [
                        {
                            "type": "text",
                            "text": "Saved to toto.txt\n\n(Modified by user)",
                        }
                    ],
                    "tool_use_id": "tool_call_id",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
    ]


def test_message_conversion_with_tool_and_non_tool():
    init_tools(allowlist=["save", "shell"])

    messages = [
        Message(role="system", content="Initial Message", pinned=True, hide=True),
        Message(role="system", content="Project prompt", hide=True),
        Message(
            role="assistant",
            content='\n@save(tool_call_id): {"path": "path.txt", "content": "file_content"}',
        ),
        Message(role="system", content="Saved to toto.txt", call_id="tool_call_id"),
        Message(
            role="assistant",
            content=(
                "The script `hello.py` has been created. "
                "Run it using the command:\n\n```shell\npython hello.py\n```"
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

    messages_dicts, _, _ = _prepare_messages_for_api(messages, [tool_save, tool_shell])

    assert messages_dicts == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "<system>Project prompt</system>"}],
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool_call_id",
                    "name": "save",
                    "input": {"path": "path.txt", "content": "file_content"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "content": [{"type": "text", "text": "Saved to toto.txt"}],
                    "tool_use_id": "tool_call_id",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "The script `hello.py` has been created. Run it using the command:\n\n```shell\npython hello.py\n```",
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "<system>Ran command: `python hello.py`\n\n `Hello, world!`\n\n</system>",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
    ]


# Updated tests for generator retry behavior


def test_retry_generator_only_retries_before_yield():
    """Test that retry_generator_on_overloaded only retries if no content has been yielded.

    This prevents duplicate output when an error occurs mid-stream.
    Issue: https://github.com/gptme/gptme/issues/1030 (Finding 4)
    """
    import os

    from gptme.llm.llm_anthropic import retry_generator_on_overloaded

    # Create a mock that looks like an Anthropic API 500 error
    def make_api_error():
        from anthropic import APIStatusError
        from httpx import Request, Response

        request = Request("POST", "https://api.anthropic.com/v1/messages")
        response = Response(500, request=request)
        return APIStatusError("Internal server error", response=response, body=None)

    # Clear the test max retries env var for this test
    old_val = os.environ.pop("GPTME_TEST_MAX_RETRIES", None)
    try:
        # Track call count to verify retry behavior
        call_count = 0

        @retry_generator_on_overloaded(max_retries=3, base_delay=0.01)
        def gen_fails_before_yield():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise make_api_error()
            yield "success"

        @retry_generator_on_overloaded(max_retries=3, base_delay=0.01)
        def gen_fails_after_yield():
            yield "chunk1"
            yield "chunk2"
            raise make_api_error()

        # Test 1: Should retry when error occurs before any yield
        call_count = 0
        result = list(gen_fails_before_yield())
        assert result == ["success"], f"Expected ['success'], got {result}"
        assert call_count == 3, f"Expected 3 calls (2 retries), got {call_count}"

        # Test 2: Should NOT retry when error occurs after yielding
        # (would cause duplicate output)
        collected = []
        try:
            for chunk in gen_fails_after_yield():
                collected.append(chunk)
        except Exception:
            pass  # Expected to raise

        # Should have received chunks before error, and NOT duplicated
        assert collected == [
            "chunk1",
            "chunk2",
        ], f"Expected ['chunk1', 'chunk2'], got {collected}"
    finally:
        if old_val is not None:
            os.environ["GPTME_TEST_MAX_RETRIES"] = old_val


def test_retry_generator_preserves_return_value():
    """Test that retry_generator_on_overloaded preserves generator return values."""
    from gptme.llm.llm_anthropic import retry_generator_on_overloaded

    @retry_generator_on_overloaded(max_retries=3, base_delay=0.01)
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
