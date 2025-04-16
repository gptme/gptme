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
                {"type": "text", "text": "<thinking>\nSomething\n</thinking>\n"},
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
                    "text": "The script `hello.py` has been created. Run it using the command:\n\n```shell\npython hello.py\n```\n",
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
