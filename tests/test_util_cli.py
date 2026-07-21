"""Tests for the gptme-util CLI."""

import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from gptme.cli.util import main
from gptme.logmanager import ConversationMeta
from gptme.profiles import Profile


def test_tokens_count(tmp_path):
    """Test the tokens count command."""
    runner = CliRunner()

    # Test basic token counting
    result = runner.invoke(main, ["tokens", "count", "Hello, world!"])
    assert result.exit_code == 0
    assert "Token count" in result.output
    assert "gpt-4" in result.output  # default model

    # Provider-prefixed model names (gptme's canonical "provider/model" form)
    # must count, not error. Regression: tiktoken doesn't know the "openai/"
    # prefix, so the old raw encoding_for_model() rejected valid models.
    result = runner.invoke(
        main, ["tokens", "count", "--model", "openai/gpt-4o", "Hello, world!"]
    )
    assert result.exit_code == 0
    assert "Token count" in result.output
    m = re.search(r"Token count [^:]+: (\d+)", result.output)
    assert m and int(m.group(1)) > 0

    # Models tiktoken doesn't natively recognize fall back to an estimate
    # (cl100k_base) rather than erroring — a counter should count, not refuse.
    result = runner.invoke(
        main, ["tokens", "count", "--model", "anthropic/claude-3-5-sonnet", "test"]
    )
    assert result.exit_code == 0
    assert "Token count" in result.output
    m = re.search(r"Token count [^:]+: (\d+)", result.output)
    assert m and int(m.group(1)) > 0

    # Provider-prefixed models needing prefix-strip (e.g. openai/o1) should
    # get the correct encoding (o200k_base), not fall back to cl100k_base.
    result = runner.invoke(main, ["tokens", "count", "--model", "openai/o1", "test"])
    assert result.exit_code == 0
    assert "Token count" in result.output
    m = re.search(r"Token count [^:]+: (\d+)", result.output)
    assert m and int(m.group(1)) > 0

    # Test file input
    tmp_file = Path(tmp_path) / "test.txt"
    tmp_file.write_text("Hello from file!")
    result = runner.invoke(main, ["tokens", "count", "-f", str(tmp_file)])
    assert result.exit_code == 0
    assert "Token count" in result.output

    # Test no input: should exit nonzero (error), not silently succeed
    result = runner.invoke(main, ["tokens", "count"], input="")
    assert result.exit_code != 0
    assert "No text provided" in result.output

    # Test stdin via "-" argument (Unix convention: "-" means read from stdin)
    result = runner.invoke(main, ["tokens", "count", "-"], input="Hello, world!")
    assert result.exit_code == 0
    assert "Token count" in result.output
    # Should count actual tokens, not 1 (the dash character)
    count = int(result.output.split(": ", 1)[1].strip())
    assert count > 1

    # Test directory passed to --file: should fail cleanly, not raise
    # IsADirectoryError as an uncaught traceback.
    result = runner.invoke(main, ["tokens", "count", "-f", str(tmp_path)])
    assert result.exit_code == 2
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "is a directory" in result.output.lower()

    # Test `--file -` reads from stdin (Unix convention: '-' as file = stdin).
    # Regression: prior to allow_dash=True on --file, this failed with
    # click.Path validation ("File '-' does not exist") even though the
    # error message advertised '-' for stdin.
    result = runner.invoke(
        main, ["tokens", "count", "-f", "-"], input="stdin via file flag"
    )
    assert result.exit_code == 0
    assert "Token count" in result.output
    count = int(result.output.split(": ", 1)[1].strip())
    assert count > 1

    # Test `--file -` with empty stdin: should still error with the same
    # "No text provided" path, not crash with click validation or
    # FileNotFoundError.
    result = runner.invoke(main, ["tokens", "count", "-f", "-"], input="")
    assert result.exit_code == 1
    assert "No text provided" in result.output


def test_chats_list(tmp_path, mocker):
    """Test the chats list command."""
    runner = CliRunner()

    mocker.patch("gptme.tools.browser.browser", "playwright")

    # Create test conversations
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    # Mock list_conversations at the package level (picked up by lazy imports in tools/chats.py)
    mocker.patch("gptme.logmanager.list_conversations", return_value=[])

    # Test empty list
    result = runner.invoke(main, ["chats", "list"])
    assert result.exit_code == 0
    assert "No conversations found" in result.output
    assert "Using browser tool with" not in result.output

    # Create test conversation files with names that won't be filtered
    conv1_dir = logs_dir / "2024-01-01-chat-one"
    conv1_dir.mkdir()
    (conv1_dir / "conversation.jsonl").write_text(
        '{"role": "user", "content": "hello", "timestamp": "2024-01-01T00:00:00"}\n'
    )

    conv2_dir = logs_dir / "2024-01-01-chat-two"
    conv2_dir.mkdir()
    (conv2_dir / "conversation.jsonl").write_text(
        '{"role": "user", "content": "hello", "timestamp": "2024-01-01T00:00:00"}\n'
        '{"role": "assistant", "content": "hi", "timestamp": "2024-01-01T00:00:01"}\n'
    )

    # Create ConversationMeta objects for our test conversations

    conv1 = ConversationMeta(
        id="2024-01-01-chat-one",
        name="Chat One",
        path=str(conv1_dir / "conversation.jsonl"),
        created=time.time(),
        modified=time.time(),
        messages=1,
        branches=1,
        workspace=".",
    )
    conv2 = ConversationMeta(
        id="2024-01-01-chat-two",
        name="Chat Two",
        path=str(conv2_dir / "conversation.jsonl"),
        created=time.time(),
        modified=time.time(),
        messages=2,
        branches=1,
        workspace=".",
    )

    # Update the mock to return our test conversations
    mocker.patch("gptme.logmanager.list_conversations", return_value=[conv1, conv2])

    # Test with conversations
    result = runner.invoke(main, ["chats", "list"])
    assert result.exit_code == 0
    assert "Chat One" in result.output
    assert "Chat Two" in result.output
    assert "(1 msgs)" in result.output  # First chat has 1 message
    assert "(2 msgs)" in result.output  # Second chat has 2 messages
    assert "Using browser tool with" not in result.output


def test_chats_list_negative_limit():
    """A negative --limit should be rejected cleanly, not crash islice()."""
    runner = CliRunner()

    # Both plain and --json paths funnel through list_conversations(limit),
    # which previously passed a negative limit straight to islice() (ValueError).
    for extra in ([], ["--json"]):
        result = runner.invoke(main, ["chats", "list", "--limit", "-5", *extra])
        assert result.exit_code != 0
        assert "Traceback" not in result.output
        # click.IntRange emits a clear range-violation message
        assert "-5" in result.output


def test_chats_read_nonexistent_exits_nonzero(tmp_path, monkeypatch):
    """Reading a missing conversation should fail (exit!=0), like rename/send/export.

    Previously `chats read` delegated to read_chat() which only printed
    "not found" and returned, so the CLI exited 0 on an error.
    """
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(main, ["chats", "read", "no-such-conversation"])
    assert result.exit_code != 0
    assert "not found" in result.output
    assert "Traceback" not in result.output


def test_chats_read_too_long_id_exits_cleanly(tmp_path, monkeypatch):
    """``chats read`` with a >255-byte ID must exit non-zero without an OSError traceback.

    Passing a 300-character ID previously caused ``OSError: [Errno 36] File name too long``
    to bubble up unhandled from ``Path.exists()`` inside the command.
    """
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))
    runner = CliRunner()

    long_id = "a" * 300
    result = runner.invoke(main, ["chats", "read", long_id])
    assert result.exit_code != 0
    assert "not found" in result.output
    assert "Traceback" not in result.output
    assert "OSError" not in result.output


def test_chats_read_dot_id_exits_cleanly(tmp_path, monkeypatch):
    """``chats read .`` must exit non-zero without accessing the logs root directory.

    A single-dot ID resolves to the logs root directory itself, which can exist and
    cause ``chats_send``/``chats_export`` to operate on the whole log tree.
    """
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(main, ["chats", "read", "."])
    assert result.exit_code != 0
    assert "not found" in result.output
    assert "Traceback" not in result.output


def test_chats_read_finds_conversation_beyond_recent_limit(tmp_path, monkeypatch):
    """`chats read` must find any conversation, not just the 20 most recent.

    read_chat() used list_conversations() (default limit 20), so reading an
    older conversation by id reported it as "not found" even though it existed.
    """
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))
    runner = CliRunner()

    # Create 25 conversations with strictly increasing mtimes so recency order
    # is deterministic; the oldest sits well outside the old 20-conversation window.
    target_id = "0000-01-01-oldest-chat"
    base = time.time() - 10_000
    for i in range(25):
        conv_id = "0000-01-01-oldest-chat" if i == 0 else f"2026-01-01-chat-{i:02d}"
        conv_dir = tmp_path / conv_id
        conv_dir.mkdir()
        jsonl = conv_dir / "conversation.jsonl"
        jsonl.write_text(
            '{"role": "user", "content": "hello", "timestamp": "2026-01-01T00:00:00+00:00"}\n'
        )
        # i=0 (target) is the oldest, i=24 the newest.
        os.utime(jsonl, (base + i, base + i))

    result = runner.invoke(main, ["chats", "read", target_id])
    assert result.exit_code == 0, result.output
    assert "not found" not in result.output
    assert target_id in result.output


def test_chats_search_matches_and_context_options(tmp_path, monkeypatch, mocker):
    """`chats search` should honor --matches and line-based --context."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))
    mocker.patch("gptme.cli.cmd_chats.get_tools", return_value=[SimpleNamespace()])
    runner = CliRunner()

    conv_dir = tmp_path / "2026-01-01-demo"
    conv_dir.mkdir()
    (conv_dir / "conversation.jsonl").write_text(
        '{"role": "user", "content": "before\\nneedle first\\nafter", "timestamp": "2026-01-01T00:00:00+00:00"}\n'
        '{"role": "assistant", "content": "one\\nneedle second\\ntwo", "timestamp": "2026-01-01T00:00:01+00:00"}\n'
    )

    result = runner.invoke(
        main,
        ["chats", "search", "needle", "--matches", "2", "--context", "1"],
    )

    assert result.exit_code == 0, result.output
    assert "Match 1 (message 0, user):" in result.output
    assert "Match 2 (message 1, assistant):" in result.output
    assert "1| before" in result.output
    assert "2| **needle** first" in result.output
    assert "1| one" in result.output
    assert "2| **needle** second" in result.output


def test_chats_read_start_and_context_options(tmp_path, monkeypatch, mocker):
    """`chats read` should include prior messages when --context is requested."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))
    mocker.patch("gptme.cli.cmd_chats.get_tools", return_value=[SimpleNamespace()])
    runner = CliRunner()

    conv_id = "2026-01-01-demo"
    conv_dir = tmp_path / conv_id
    conv_dir.mkdir()
    (conv_dir / "conversation.jsonl").write_text(
        '{"role": "user", "content": "first line", "timestamp": "2026-01-01T00:00:00+00:00"}\n'
        '{"role": "assistant", "content": "second line", "timestamp": "2026-01-01T00:00:01+00:00"}\n'
        '{"role": "user", "content": "third line", "timestamp": "2026-01-01T00:00:02+00:00"}\n'
    )

    result = runner.invoke(
        main,
        ["chats", "read", conv_id, "--start", "2", "--context", "1", "--limit", "2"],
    )

    assert result.exit_code == 0, result.output
    assert f"Reading conversation: {conv_id} ({conv_id})" in result.output
    assert "1. User: first line..." in result.output
    assert "2. Assistant: second line..." in result.output
    assert "3. User: third line..." not in result.output


def test_context_index_and_retrieve(tmp_path):
    """Test the context index and retrieve commands."""
    # Skip if gptme-rag not available
    from gptme.tools.rag import _has_gptme_rag

    if not _has_gptme_rag():
        pytest.skip("gptme-rag not available")

    # Create a test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("Hello, world!")

    runner = CliRunner()
    result = runner.invoke(main, ["context", "index", str(test_file)])

    assert result.exit_code == 0
    assert "indexed 1" in result.output.lower()

    # Test basic retrieve
    result = runner.invoke(main, ["context", "retrieve", "test query"])
    assert result.exit_code == 0
    assert result.output.count("Hello, world!") > 0
    # Check that the output contains the indexed content only once
    # TODO: requires fresh index for gptme-rag (or project/dir-specific index support)
    # assert result.output.count("Hello, world!") == 1

    # Test with --full flag
    result = runner.invoke(main, ["context", "retrieve", "--full", "test query"])
    assert result.exit_code == 0
    assert result.output.count("Hello, world!") > 0
    # assert result.output.count("Hello, world!") == 1


def test_prompts_expand_ignores_disable_path_include(tmp_path, monkeypatch):
    """`prompts expand` should still show path expansion under disabled include env.

    The command is an inspection/debugging surface for path expansion itself, so
    it must not inherit the ambient automation env that disables expansion in
    normal chat prompts.
    """
    runner = CliRunner()
    test_file = tmp_path / "hello.txt"
    test_file.write_text("hello\n")

    monkeypatch.setenv("GPTME_DISABLE_PATH_INCLUDE", "1")
    result = runner.invoke(main, ["prompts", "expand", str(test_file)])

    assert result.exit_code == 0
    assert "hello" in result.output
    assert str(test_file) in result.output
    assert os.environ["GPTME_DISABLE_PATH_INCLUDE"] == "1"


def test_chats_send(tmp_path, monkeypatch):
    """Test queueing a prompt for an existing conversation."""
    runner = CliRunner()

    logs_dir = tmp_path / "logs"
    conv_dir = logs_dir / "chat-123"
    conv_dir.mkdir(parents=True)

    monkeypatch.setattr("gptme.cli.cmd_chats.get_logs_dir", lambda: logs_dir)

    result = runner.invoke(main, ["chats", "send", "chat-123", "follow", "up"])

    assert result.exit_code == 0
    assert "Queued prompt for 'chat-123'" in result.output

    queue_path = conv_dir / "prompt-queue.jsonl"
    records = [json.loads(line) for line in queue_path.read_text().splitlines()]
    assert [record["content"] for record in records] == ["follow up"]


def test_chats_send_help_mentions_queued_follow_up_flow():
    """Test that chats send help explains the queued-follow-up workflow."""
    import re

    runner = CliRunner()

    result = runner.invoke(main, ["chats", "send", "--help"])

    assert result.exit_code == 0
    help_text = " ".join(result.output.lower().split())
    assert re.search(r"queue.*prompt", help_text)
    assert re.search(r"running conversation|gptme process.*busy", help_text)


def test_tools_list(mocker):
    """Test the tools list command."""
    import json

    runner_cls: Any = CliRunner
    try:
        runner = runner_cls(mix_stderr=False)
    except TypeError:
        runner = runner_cls()

    mocker.patch("gptme.tools.browser.browser", "playwright")

    # Test basic list
    result = runner.invoke(main, ["tools", "list"])
    assert "Available tools" in result.output
    assert result.exit_code == 0
    assert "Using browser tool with" not in result.output
    assert "Failed to register hook" not in getattr(result, "stderr", "")

    # Test langtags
    result = runner.invoke(main, ["tools", "list", "--langtags"])
    assert result.exit_code == 0
    assert "language tags" in result.output.lower()

    # Test JSON output
    result = runner.invoke(main, ["tools", "list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) > 0
    # Check required fields (stable schema — all keys always present)
    tool = data[0]
    assert "name" in tool
    assert "desc" in tool
    assert "available" in tool
    assert isinstance(tool["available"], bool)
    assert "block_types" in tool
    assert isinstance(tool["block_types"], list)
    assert "functions" in tool
    assert isinstance(tool["functions"], list)
    assert "commands" in tool
    assert isinstance(tool["commands"], list)
    assert "is_mcp" in tool
    assert isinstance(tool["is_mcp"], bool)
    # Default --available filter: all tools should be available
    assert all(t["available"] for t in data)

    # Test JSON with --all (includes unavailable)
    result = runner.invoke(main, ["tools", "list", "--all", "--json"])
    assert result.exit_code == 0
    data_all = json.loads(result.output)
    assert isinstance(data_all, list)
    assert len(data_all) >= len(data)


def test_tools_info():
    """Test the tools info command."""
    import json

    runner = CliRunner()

    # Test valid tool
    result = runner.invoke(main, ["tools", "info", "ipython"])
    assert result.exit_code == 0
    assert "# ipython" in result.output
    assert "Status:" in result.output
    assert "## Instructions" in result.output

    # Test invalid tool
    result = runner.invoke(main, ["tools", "info", "nonexistent-tool"])
    assert result.exit_code != 0  # returns non-zero for not found tool
    assert "not found" in result.output

    # Test JSON output (stable schema — instructions/examples always present)
    result = runner.invoke(main, ["tools", "info", "shell", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["name"] == "shell"
    assert data["available"] is True
    assert "instructions" in data
    assert isinstance(data["instructions"], str)
    assert len(data["instructions"]) > 0
    assert "examples" in data
    assert isinstance(data["examples"], str)
    assert "is_mcp" in data
    assert isinstance(data["is_mcp"], bool)


def test_tools_info_json_invalid_tool_stays_machine_readable():
    """tools info --json should return a JSON error payload for missing tools."""
    import json

    runner = CliRunner()

    result = runner.invoke(main, ["tools", "info", "nonexistent-tool", "--json"])

    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["tool"] == "nonexistent-tool"
    assert "not found" in data["error"]
    assert "shell" in data["available_tools"]


def test_tools_call_non_default_tool():
    """Non-default tools that expose functions must be callable.

    Tools like ``subagent`` are not loaded by default, but they expose
    callable functions and ``tools call`` should still reach them instead of
    reporting the tool as missing. Regression test for the bare ``init_tools()``
    that left non-default tools uncallable.
    """
    runner = CliRunner()

    # subagent is not in the default toolchain but exposes functions.
    # Calling without the required arg should reach the function (and fail on
    # the missing argument), NOT report the tool as not found.
    result = runner.invoke(main, ["tools", "call", "subagent", "subagent_status"])
    assert result.exit_code != 0
    assert "Tool 'subagent' not found" not in result.output
    assert "Error calling function" in result.output

    # Unknown function on a loaded non-default tool lists its functions.
    result = runner.invoke(main, ["tools", "call", "subagent", "nosuchfn"])
    assert result.exit_code != 0
    assert "not found in tool 'subagent'" in result.output
    assert "Available functions" in result.output

    # Genuinely unknown tool still errors and lists available tools.
    result = runner.invoke(main, ["tools", "call", "definitely-not-a-tool", "fn"])
    assert result.exit_code != 0
    assert "not found" in result.output
    assert "Available tools" in result.output


def test_tools_call_execute_only_tool_message():
    """Tools with an execute entrypoint but no discrete functions get a clear
    message, not the misleading "No functions available for this tool".

    Most core tools (shell, patch, save, read, …) expose behaviour through a
    single ``execute`` entrypoint with an empty ``functions`` list, so they are
    not callable via ``tools call``. The error should say so explicitly.
    """
    runner = CliRunner()

    result = runner.invoke(main, ["tools", "call", "shell", "no_such_fn"])
    assert result.exit_code != 0
    assert "No functions available for this tool" not in result.output
    assert "execute entrypoint" in result.output
    assert "shell" in result.output


def test_models_list():
    """Test the models list command."""
    import json

    runner = CliRunner()

    # Test basic list
    result = runner.invoke(main, ["models", "list"])
    assert result.exit_code == 0
    assert "models" in result.output.lower()

    # Test simple format
    result = runner.invoke(main, ["models", "list", "--simple"])
    assert result.exit_code == 0
    # Simple format should have provider/model on each line
    lines = [line for line in result.output.strip().splitlines() if "/" in line]
    assert len(lines) > 0

    # Test JSON output
    result = runner.invoke(main, ["models", "list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) > 0
    # Check required fields in first model
    model = data[0]
    assert "provider" in model
    assert "model" in model
    assert "full" in model
    assert "context" in model
    assert isinstance(model["context"], int)

    # Test JSON with provider filter
    result = runner.invoke(
        main, ["models", "list", "--json", "--provider", "anthropic"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert all(m["provider"] == "anthropic" for m in data)
    assert len(data) > 0

    # Test JSON with vision filter
    result = runner.invoke(main, ["models", "list", "--json", "--vision"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert all(m["supports_vision"] for m in data)

    # Test JSON with reasoning filter
    result = runner.invoke(main, ["models", "list", "--json", "--reasoning"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert all(m["supports_reasoning"] for m in data)


def test_models_list_json_suppresses_provider_noise(mocker):
    """JSON output should stay parseable even if provider discovery logs to stdio."""
    import json

    runner = CliRunner()

    def noisy_get_model_list(**_kwargs):
        print("[12:34:56] noisy provider warning")
        return [
            SimpleNamespace(
                provider="openai",
                provider_key="openai",
                model="gpt-5",
                full="openai/gpt-5",
                context=400000,
                max_output=None,
                supports_streaming=True,
                supports_vision=True,
                supports_reasoning=True,
                supports_parallel_tool_calls=True,
                supports_responses_api=True,
                price_input=None,
                price_output=None,
                knowledge_cutoff=None,
                deprecated=False,
                preferred_edit_format=None,
                pricing_type="per_token",
            )
        ]

    mocker.patch(
        "gptme.cli.util.get_model_list",
        side_effect=noisy_get_model_list,
    )

    result = runner.invoke(main, ["models", "list", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed[0]["full"] == "openai/gpt-5"


def test_models_list_json_available_keeps_plugin_models(mocker):
    """Plugin models should pass the CLI --json --available filter."""
    import json

    runner = CliRunner()
    mocker.patch(
        "gptme.cli.util.get_model_list",
        return_value=[
            SimpleNamespace(
                provider="unknown",
                provider_key="minimax",
                model="minimax/abab6.5s-chat",
                full="minimax/abab6.5s-chat",
                context=245760,
                max_output=None,
                supports_streaming=True,
                supports_vision=False,
                supports_reasoning=False,
                supports_parallel_tool_calls=False,
                supports_responses_api=False,
                price_input=0,
                price_output=0,
                knowledge_cutoff=None,
                deprecated=False,
                preferred_edit_format=None,
                pricing_type="per_token",
            )
        ],
    )
    mocker.patch(
        "gptme.llm.list_available_providers",
        return_value=[("minimax", True)],
    )

    result = runner.invoke(main, ["models", "list", "--json", "--available"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert [model["model"] for model in parsed] == ["minimax/abab6.5s-chat"]


def test_models_info():
    """Test the models info command."""
    import json

    runner = CliRunner()

    # Test basic info
    result = runner.invoke(main, ["models", "info", "anthropic/claude-sonnet-4-6"])
    assert result.exit_code == 0
    assert "claude-sonnet-4-6" in result.output
    assert "anthropic" in result.output

    # Test JSON output
    result = runner.invoke(
        main, ["models", "info", "anthropic/claude-sonnet-4-6", "--json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["provider"] == "anthropic"
    assert data["model"] == "claude-sonnet-4-6"
    assert data["full"] == "anthropic/claude-sonnet-4-6"
    assert isinstance(data["context"], int)
    assert data["supports_vision"] is True
    assert "price_input" in data
    assert "price_output" in data

    # Test unknown model (falls back to defaults — exit code 0)
    result = runner.invoke(main, ["models", "info", "nonexistent/model"])
    assert result.exit_code == 0
    assert "nonexistent" in result.output


def test_profile_validate_success(mocker):
    """Test profile validate command when all profiles are valid."""
    runner = CliRunner()

    mocker.patch(
        "gptme.profiles.list_profiles",
        return_value={
            "default": Profile(name="default", description="Default", tools=None),
            "reader": Profile(name="reader", description="Reader", tools=["read"]),
        },
    )
    mocker.patch(
        "gptme.tools.get_available_tools",
        return_value=[SimpleNamespace(name="read"), SimpleNamespace(name="shell")],
    )

    result = runner.invoke(main, ["profile", "validate"])

    assert result.exit_code == 0
    assert "Profile 'default': OK (all tools)" in result.output
    assert "Profile 'reader': OK (1 tools)" in result.output
    assert "All profiles valid." in result.output


def test_profile_validate_failure(mocker):
    """Test profile validate command when profiles contain unknown tools."""
    runner = CliRunner()

    mocker.patch(
        "gptme.profiles.list_profiles",
        return_value={
            "broken": Profile(
                name="broken", description="Broken", tools=["read", "reead"]
            ),
        },
    )
    mocker.patch(
        "gptme.tools.get_available_tools",
        return_value=[SimpleNamespace(name="read"), SimpleNamespace(name="shell")],
    )

    result = runner.invoke(main, ["profile", "validate"])

    assert result.exit_code == 1
    assert "Profile 'broken': unknown tools: reead" in result.output
    assert "Available tools: read, shell" in result.output


def test_profile_list_shows_empty_tools_as_empty_not_all(monkeypatch):
    """Profile with tools=[] should not be displayed as "all" in profile list."""
    runner = CliRunner()

    monkeypatch.setattr(
        "gptme.profiles.list_profiles",
        lambda: {
            "no-tools": Profile(name="no-tools", description="No tools", tools=[]),
        },
    )

    result = runner.invoke(main, ["profile", "list"])

    assert result.exit_code == 0
    assert "no-tools" in result.output
    assert "No tools" in result.output
    assert "all" not in result.output.lower()


def test_profile_show_shows_empty_tools_as_empty_not_all_tools(monkeypatch):
    """Profile with tools=[] should not be displayed as "all tools" in profile show."""
    runner = CliRunner()

    monkeypatch.setattr(
        "gptme.profiles.get_profile",
        lambda _name: Profile(name="no-tools", description="No tools", tools=[]),
    )

    result = runner.invoke(main, ["profile", "show", "no-tools"])

    assert result.exit_code == 0
    assert "Name:" in result.output
    assert "no-tools" in result.output
    assert "Tools:" in result.output
    assert "all tools" not in result.output.lower()


# ---------------------------------------------------------------------------
# agents scan tests
# ---------------------------------------------------------------------------


def _make_agent(
    pid=1234,
    runtime="claude-code",
    cwd="/home/user/project",
    model="opus",
    mode="autonomous",
    branch="master",
    uptime_seconds=300,
    stale=False,
    stale_reason=None,
):
    from gptme.hooks.workspace_agents import AgentInfo

    return AgentInfo(
        pid=pid,
        runtime=runtime,
        cwd=cwd,
        model=model,
        mode=mode,
        branch=branch,
        uptime_seconds=uptime_seconds,
        stale=stale,
        stale_reason=stale_reason,
    )


def test_agents_scan_empty(mocker):
    """agents scan returns exit 1 and a clear message when no agents run."""
    mocker.patch("gptme.hooks.workspace_agents.scan_agents", return_value=[])
    runner = CliRunner()
    result = runner.invoke(main, ["agents", "scan"])
    assert result.exit_code == 1
    assert "No active agents" in result.output


def test_agents_scan_active(mocker):
    """agents scan shows active agents and exits 0."""
    agent = _make_agent()
    mocker.patch("gptme.hooks.workspace_agents.scan_agents", return_value=[agent])
    runner = CliRunner()
    result = runner.invoke(main, ["agents", "scan"])
    assert result.exit_code == 0
    assert "claude-code" in result.output
    assert "1234" in result.output  # PID shown


def test_agents_scan_json(mocker):
    """agents scan --json returns valid JSON with expected fields."""
    import json

    agent = _make_agent()
    mocker.patch("gptme.hooks.workspace_agents.scan_agents", return_value=[agent])
    runner = CliRunner()
    result = runner.invoke(main, ["agents", "scan", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["runtime"] == "claude-code"
    assert data[0]["pid"] == 1234
    assert data[0]["mode"] == "autonomous"


def test_agents_scan_hides_stale_by_default(mocker):
    """Stale agents are hidden unless --all is passed."""
    active = _make_agent(pid=100, stale=False)
    stale = _make_agent(
        pid=200, stale=True, stale_reason="too old", uptime_seconds=10000
    )
    mocker.patch(
        "gptme.hooks.workspace_agents.scan_agents", return_value=[active, stale]
    )
    runner = CliRunner()

    # Default: stale hidden
    result = runner.invoke(main, ["agents", "scan"])
    assert result.exit_code == 0
    assert "100" in result.output
    assert "200" not in result.output
    assert "stale" in result.output  # hint shown

    # --all: both shown
    result = runner.invoke(main, ["agents", "scan", "--all"])
    assert result.exit_code == 0
    assert "100" in result.output
    assert "200" in result.output
    assert result.output.count("[STALE]") == 1


def test_agents_scan_workspace_passes_through(mocker, tmp_path):
    """--workspace argument is forwarded to scan_agents."""
    mock_scan = mocker.patch(
        "gptme.hooks.workspace_agents.scan_agents", return_value=[]
    )
    runner = CliRunner()
    runner.invoke(main, ["agents", "scan", "--workspace", str(tmp_path)])
    mock_scan.assert_called_once_with(workspace=str(tmp_path))


def test_llm_generate_unknown_model():
    """gptme-util llm generate --model unknown/model should fail cleanly, not crash."""
    runner = CliRunner()
    result = runner.invoke(
        main, ["llm", "generate", "--model", "notareal/model", "hello"]
    )
    assert result.exit_code != 0
    # Should show clean error, not a raw ValueError traceback
    assert result.exception is None or isinstance(result.exception, SystemExit)
    # Error mentions the unknown provider/model — exact message varies by code path
    assert "notareal" in result.output or "Unknown" in result.output


def test_llm_generate_empty_model():
    """gptme-util llm generate --model '' should error cleanly, not silently fall back to default."""
    runner = CliRunner()
    result = runner.invoke(main, ["llm", "generate", "--model", "", "hello"])
    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert "empty" in result.output.lower()


def test_llm_generate_prepends_system_message(monkeypatch):
    """llm generate must prepend a system message so Anthropic-compatible providers don't reject it."""
    import unittest.mock as mock

    captured: list = []

    def fake_chat_complete(messages, model, tools, **kwargs):
        captured.extend(messages)
        return ("ok", None)

    # Patch at source — _chat_complete is lazily imported inside llm_generate
    monkeypatch.setattr("gptme.llm._chat_complete", fake_chat_complete)
    monkeypatch.setattr("gptme.init.init", lambda *a, **kw: None)
    monkeypatch.setattr("gptme.llm.get_provider_from_model", lambda m: mock.MagicMock())
    monkeypatch.setattr("gptme.llm.init_llm", lambda *a, **kw: None)

    runner = CliRunner()
    result = runner.invoke(
        main, ["llm", "generate", "--model", "anthropic/claude-sonnet-4-6", "hello"]
    )
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "ok", (
        f"Expected plain response text, got: {result.output!r}"
    )
    assert captured, "No messages were passed to _chat_complete"
    assert captured[0].role == "system", (
        f"First message must be system, got {captured[0].role!r}; "
        f"Anthropic rejects conversations without a leading system message"
    )


def test_llm_generate_prepends_system_message_stream(monkeypatch):
    """--stream path must also prepend a system message (same message list is used for both paths)."""
    import unittest.mock as mock

    captured: list = []

    def fake_stream(messages, model, tools, **kwargs):
        captured.extend(messages)
        yield "ok"

    monkeypatch.setattr("gptme.llm._stream", fake_stream)
    monkeypatch.setattr("gptme.init.init", lambda *a, **kw: None)
    monkeypatch.setattr("gptme.llm.get_provider_from_model", lambda m: mock.MagicMock())
    monkeypatch.setattr("gptme.llm.init_llm", lambda *a, **kw: None)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "llm",
            "generate",
            "--stream",
            "--model",
            "anthropic/claude-sonnet-4-6",
            "hello",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured, "No messages were passed to _stream"
    assert captured[0].role == "system", (
        f"First message must be system, got {captured[0].role!r}"
    )


def test_context_git_in_repo(tmp_path):
    """context git outputs branch/commit info inside a git repo."""
    import subprocess

    runner = CliRunner()
    # Minimal git env so global hooks/signing/gpg don't interfere
    git_env = {
        "HOME": str(tmp_path),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    with runner.isolated_filesystem(temp_dir=tmp_path):
        subprocess.run(["git", "init"], check=True, capture_output=True, env=git_env)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            check=True,
            capture_output=True,
            env=git_env,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            check=True,
            capture_output=True,
            env=git_env,
        )
        subprocess.run(
            ["git", "config", "commit.gpgsign", "false"],
            check=True,
            capture_output=True,
            env=git_env,
        )
        Path("readme.txt").write_text("hello")
        subprocess.run(
            ["git", "add", "."], check=True, capture_output=True, env=git_env
        )
        subprocess.run(
            ["git", "commit", "-m", "initial commit", "--no-verify"],
            check=True,
            capture_output=True,
            env=git_env,
        )
        result = runner.invoke(main, ["context", "git"])
    assert result.exit_code == 0, result.output
    assert "## Git" in result.output


def test_context_git_not_in_repo(tmp_path):
    """context git exits nonzero outside a git repo."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(main, ["context", "git"])
    assert result.exit_code != 0


def test_context_tree(tmp_path):
    """context tree prints a workspace tree."""
    runner = CliRunner()
    (tmp_path / "file.txt").write_text("content")
    (tmp_path / "subdir").mkdir()
    result = runner.invoke(main, ["context", "tree", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Workspace structure" in result.output
    assert "\x1b[" not in result.output


def test_context_files(tmp_path):
    """context files reads gptme.toml [prompt] files and prints them."""
    runner = CliRunner()
    prompt_file = tmp_path / "notes.md"
    prompt_file.write_text("hello notes")
    toml = tmp_path / "gptme.toml"
    toml.write_text('[prompt]\nfiles = ["notes.md"]\n')
    result = runner.invoke(main, ["context", "files", "--config", str(toml)])
    assert result.exit_code == 0, result.output
    assert "notes.md" in result.output
    assert "hello notes" in result.output


def test_context_files_missing_toml(tmp_path):
    """context files exits nonzero when no gptme.toml is found."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(main, ["context", "files"])
    assert result.exit_code != 0


def test_context_journal(tmp_path):
    """context journal finds and prints journal entries."""
    from datetime import datetime, timezone

    runner = CliRunner()
    today = datetime.now(tz=timezone.utc).astimezone().strftime("%Y-%m-%d")
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    # flat layout: YYYY-MM-DD-topic.md
    (journal_dir / f"{today}-test.md").write_text("# Today\nsome notes")
    result = runner.invoke(
        main, ["context", "journal", "--path", str(journal_dir), "--days", "1"]
    )
    assert result.exit_code == 0, result.output
    assert "some notes" in result.output


def test_context_journal_subdirectory_layout(tmp_path):
    """context journal handles subdirectory-per-day journal layout."""
    from datetime import datetime, timezone

    runner = CliRunner()
    today = datetime.now(tz=timezone.utc).astimezone().strftime("%Y-%m-%d")
    journal_dir = tmp_path / "journal"
    day_dir = journal_dir / today
    day_dir.mkdir(parents=True)
    (day_dir / "session.md").write_text("# Session\nsubdir notes")
    result = runner.invoke(
        main, ["context", "journal", "--path", str(journal_dir), "--days", "1"]
    )
    assert result.exit_code == 0, result.output
    assert "subdir notes" in result.output


def test_context_journal_uses_local_date_not_utc(tmp_path, monkeypatch):
    """context journal should use the local calendar date when choosing entries."""
    from datetime import datetime, timedelta, timezone

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 1, 23, 0, tzinfo=tz)

        def astimezone(self, tz=None):
            return type(self)(2026, 1, 2, 8, 0, tzinfo=timezone(timedelta(hours=9)))

    monkeypatch.setattr("gptme.cli.util.datetime", FakeDateTime)

    runner = CliRunner()
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    (journal_dir / "2026-01-02-local.md").write_text(
        "# Local day\nfound via local date"
    )

    result = runner.invoke(
        main, ["context", "journal", "--path", str(journal_dir), "--days", "1"]
    )
    assert result.exit_code == 0, result.output
    assert "found via local date" in result.output


def test_context_journal_no_entries(tmp_path):
    """context journal reports nothing found gracefully."""
    runner = CliRunner()
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    result = runner.invoke(
        main, ["context", "journal", "--path", str(journal_dir), "--days", "1"]
    )
    assert result.exit_code == 0, result.output
    assert "No journal entries" in result.output


def test_context_journal_rejects_file_path(tmp_path):
    """context journal should reject a file passed to --path."""
    runner = CliRunner()
    journal_file = tmp_path / "journal.md"
    journal_file.write_text("# Not a directory\n")
    result = runner.invoke(main, ["context", "journal", "--path", str(journal_file)])
    assert result.exit_code != 0
    assert "Directory" in result.output


def test_context_search_conversations(tmp_path):
    """context search-conversations is registered and returns results."""
    from unittest.mock import patch

    runner = CliRunner()
    with (
        patch("gptme.tools.rag._has_gptme_rag", return_value=True),
        patch(
            "gptme.tools.rag.rag_search",
            return_value="snippet from a past conversation",
        ),
    ):
        result = runner.invoke(main, ["context", "search-conversations", "pytest"])
    assert result.exit_code == 0, result.output
    assert "Top 3 relevant conversations" in result.output


def test_context_search_conversations_top_k(tmp_path):
    """context search-conversations --top-k option is forwarded correctly."""
    from unittest.mock import patch

    runner = CliRunner()
    with (
        patch("gptme.tools.rag._has_gptme_rag", return_value=True),
        patch(
            "gptme.tools.rag.rag_search",
            return_value="snippet from a past conversation",
        ) as mock_search,
    ):
        result = runner.invoke(
            main, ["context", "search-conversations", "--top-k", "5", "pytest"]
        )
    assert result.exit_code == 0, result.output
    assert "Top 5 relevant conversations" in result.output
    mock_search.assert_called_once_with("pytest", return_full=True, top_k=5)
