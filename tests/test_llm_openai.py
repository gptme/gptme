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
