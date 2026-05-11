from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.util.context import _find_potential_paths


def test_find_potential_paths(tmp_path, monkeypatch):
    # Create some test files
    (tmp_path / "test.txt").touch()
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir/file.py").touch()

    # Change to temp directory for testing
    monkeypatch.chdir(tmp_path)

    # Test various path formats
    content = """
Here are some paths:
/absolute/path
~/home/path
./relative/path
test.txt
subdir/file.py
http://example.com
https://example.com/path

```python
# This path should be ignored
ignored_path = "/path/in/codeblock"
```

More text with `wrapped/path` and path.with.dots
        """

    paths = _find_potential_paths(content)

    # Check expected paths are found
    assert "/absolute/path" in paths
    assert "~/home/path" in paths
    assert "./relative/path" in paths
    assert "test.txt" in paths  # exists in tmp_path
    assert "subdir/file.py" in paths  # exists in tmp_path
    assert "http://example.com" in paths
    assert "https://example.com/path" in paths
    assert "wrapped/path" in paths

    # Check paths in codeblocks are ignored
    assert "/path/in/codeblock" not in paths

    # Check non-paths are ignored
    assert "path.with.dots" not in paths


def test_find_potential_paths_empty():
    # Test with empty content
    assert _find_potential_paths("") == []

    # Test with no paths
    assert _find_potential_paths("just some text") == []


def test_find_potential_paths_deduplication():
    """Paths mentioned multiple times should appear only once in the result."""
    content = """
    See `./scripts/foo.py --flag` for details.
    Also `./scripts/foo.py --other` does the same.
    And again: `./scripts/foo.py`
    The plain word /abs/path appears twice: /abs/path
    """
    paths = _find_potential_paths(content)

    # Each unique path must appear exactly once, even if mentioned multiple times
    assert paths.count("./scripts/foo.py --flag") == 1
    assert paths.count("/abs/path") == 1

    # All unique paths should still be present
    assert "./scripts/foo.py --flag" in paths
    assert "/abs/path" in paths


def test_include_paths_no_duplicate_embeddings(tmp_path, monkeypatch):
    """include_paths must not embed the same file content multiple times."""
    from gptme.message import Message
    from gptme.util.context import include_paths

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    data_file = tmp_path / "data.txt"
    data_file.write_text("unique-content-marker")

    # Reference the same file four times
    msg = Message(
        "user",
        "See `data.txt` and also `data.txt`. Then `data.txt` again and data.txt bare.",
    )
    result = include_paths(msg)

    # The unique marker should appear exactly once, not four times
    assert result.content.count("unique-content-marker") == 1


def test_include_paths_disabled_via_env_var(tmp_path, monkeypatch):
    """Test that GPTME_DISABLE_PATH_INCLUDE skips all path expansion."""
    from gptme.message import Message
    from gptme.util.context import include_paths

    monkeypatch.chdir(tmp_path)
    data_file = tmp_path / "data.txt"
    data_file.write_text("sensitive-content-should-not-appear")

    monkeypatch.setenv("GPTME_DISABLE_PATH_INCLUDE", "1")

    msg = Message("user", "See `data.txt` for details")
    result = include_paths(msg)

    # The file content should NOT appear when the env var is set
    assert "sensitive-content-should-not-appear" not in result.content
    assert result.files == []


@pytest.mark.parametrize("falsy_val", ["0", "false", "False", "no", "off", ""])
def test_include_paths_not_disabled_by_falsy_env_var(tmp_path, monkeypatch, falsy_val):
    """Test that GPTME_DISABLE_PATH_INCLUDE=false/0/no/off does NOT disable expansion."""
    from gptme.message import Message
    from gptme.util.context import include_paths

    monkeypatch.chdir(tmp_path)
    data_file = tmp_path / "data.txt"
    data_file.write_text("content-should-appear")

    if falsy_val:
        monkeypatch.setenv("GPTME_DISABLE_PATH_INCLUDE", falsy_val)
    else:
        monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)

    msg = Message("user", "See `data.txt` for details")
    result = include_paths(msg)

    # Content SHOULD appear — falsy values must not disable expansion
    assert "content-should-appear" in result.content


def test_include_paths_skips_system_messages():
    """Test that include_paths skips role=system messages (tool output) entirely."""
    from gptme.message import Message
    from gptme.util.context import include_paths

    # A system message with path-like content (e.g. tool output)
    content = """
<tool_use>
<cmd>cat /path/inside/tool/output.txt</cmd>
</tool_use>

<result>
Content from /path/in/result/data.csv
</result>

Also /some/path/in/system/message.txt
    """

    msg = Message("system", content)
    result = include_paths(msg)

    # system messages should be returned unchanged (no paths extracted)
    assert result == msg
    assert result.files == []


def test_find_potential_paths_punctuation():
    # Test paths with trailing punctuation
    content = """
    Look at ~/file.txt!
    Check /path/to/file?
    See ./local/path.
    Visit https://example.com,
    """

    paths = _find_potential_paths(content)
    assert "~/file.txt" in paths
    assert "/path/to/file" in paths
    assert "./local/path" in paths
    assert "https://example.com" in paths


def test_find_potential_paths_at_prefix(tmp_path, monkeypatch):
    """Test that @-prefixed paths are detected and the @ is stripped."""
    (tmp_path / "main.py").touch()
    (tmp_path / "src").mkdir()
    (tmp_path / "src/utils.py").touch()
    monkeypatch.chdir(tmp_path)

    content = """
    Check @src/utils.py for the implementation.
    Also look at @/absolute/path and @./relative/path.
    In backticks: `@main.py` and `@~/home/config`.
    """

    paths = _find_potential_paths(content)

    # @ prefix should be stripped in the returned paths
    assert "src/utils.py" in paths
    assert "/absolute/path" in paths
    assert "./relative/path" in paths
    assert "main.py" in paths
    assert "~/home/config" in paths

    # Original @-prefixed forms should NOT be in the result
    assert "@src/utils.py" not in paths
    assert "@main.py" not in paths


def test_find_potential_paths_at_prefix_bare_at():
    """Test that bare @ without a path is not detected."""
    content = "Send email to bob@ or use @ symbol"
    paths = _find_potential_paths(content)
    # bare @ or email-like should not be matched
    assert not any("@" in p or p == "" for p in paths)


def test_find_potential_paths_at_prefix_handles(tmp_path, monkeypatch):
    """Test that @username-style social handles are NOT treated as path references."""
    monkeypatch.chdir(tmp_path)  # clean dir with no matching files
    content = "Thanks @alice and @bob, see @charlie for details"
    paths = _find_potential_paths(content)
    # Social handles without slash should not be matched
    assert "alice" not in paths
    assert "bob" not in paths
    assert "charlie" not in paths
    assert "@alice" not in paths


def test_include_paths_at_prefix(tmp_path, monkeypatch):
    """Integration test: @file.txt in user prompt → file content included."""
    from gptme.message import Message
    from gptme.util.context import include_paths

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    test_file = tmp_path / "config.toml"
    test_file.write_text("[settings]\nkey = 'value'\n")

    msg = Message("user", "Please review @config.toml")
    result = include_paths(msg)

    # File content should be included in the message
    assert "key = 'value'" in result.content


def test_embed_attached_file_content_separator(tmp_path):
    """File contents should be separated from message content by double newlines."""
    from gptme.message import Message
    from gptme.util.context import embed_attached_file_content

    # Create a test file
    test_file = tmp_path / "test.py"
    test_file.write_text("print('hello')")

    # Message with content and an attached file
    msg = Message("user", "Check this file", files=[test_file])
    result = embed_attached_file_content(msg, workspace=tmp_path)

    # The file content should be separated from the message content
    assert result.content.startswith("Check this file\n\n")
    assert "print('hello')" in result.content
    # File should be removed from files list (embedded as text)
    assert test_file not in result.files


def test_embed_attached_file_content_multiple_files(tmp_path):
    """Multiple embedded files should each be separated by double newlines."""
    from gptme.message import Message
    from gptme.util.context import embed_attached_file_content

    # Create test files
    file_a = tmp_path / "a.py"
    file_a.write_text("code_a")
    file_b = tmp_path / "b.py"
    file_b.write_text("code_b")

    msg = Message("user", "Review these", files=[file_a, file_b])
    result = embed_attached_file_content(msg, workspace=tmp_path)

    # Both files should be embedded with proper separation
    assert result.content.startswith("Review these\n\n")
    assert "code_a" in result.content
    assert "code_b" in result.content
    # The two codeblocks should be separated by double newlines
    assert "\n\n````" in result.content


def test_embed_attached_file_content_no_files():
    """Message without files should be returned unchanged."""
    from gptme.message import Message
    from gptme.util.context import embed_attached_file_content

    msg = Message("user", "No files here")
    result = embed_attached_file_content(msg)

    assert result.content == "No files here"


def test_parse_prompt_files_long_string():
    """Long strings that exceed filesystem limits should return None, not raise."""
    from gptme.util.context import _parse_prompt_files

    # A string that's too long to be a valid path (most systems limit to ~4096 chars)
    long_string = "/" + "a" * 5000

    # Should return None (not a path), not raise OSError
    result = _parse_prompt_files(long_string)
    assert result is None


def test_find_potential_paths_ignores_xml_tags():
    """Paths inside XML tags should not be extracted (e.g. user pastes tool output)."""
    content = """
Here is some user text mentioning /real/path/to/file.txt.

<tool_use>
<cmd>cat /path/inside/xml/tag.txt</cmd>
</tool_use>

<result>
Contents from /another/xml/path.csv
</result>

Also check `./outside/xml.py` which should be found.
"""
    paths = _find_potential_paths(content)

    # Paths outside XML tags should be found
    assert "/real/path/to/file.txt" in paths
    assert "./outside/xml.py" in paths

    # Paths inside XML tags should be ignored
    assert "/path/inside/xml/tag.txt" not in paths
    assert "/another/xml/path.csv" not in paths


def test_include_paths_image_auto_attach(tmp_path, monkeypatch):
    """Image files in user messages should be auto-attached to msg.files.

    This verifies the full pipeline: _find_potential_paths detects the path,
    _parse_prompt_files validates it as a supported binary format, and
    include_paths adds it to msg.files (not embedded as text content).
    """
    from gptme.message import Message
    from gptme.util.context import include_paths

    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    # Create a minimal PNG file (valid header)
    img_file = tmp_path / "test.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    # User message with a bare image path
    msg = Message("user", str(img_file))
    result = include_paths(msg, workspace=None)

    # Image should be in msg.files (not embedded as text)
    assert len(result.files) == 1
    assert Path(str(result.files[0])).name == "test.png"
    # Original content should be preserved (not modified)
    assert str(img_file) in result.content


def test_include_paths_image_in_text(tmp_path, monkeypatch):
    """Image paths embedded in natural language text should be auto-attached.

    Simulates the scenario where a user types 'View this image ~/test.png'
    or a paste handler inserts 'View this image: /path/to/image.png'.
    """
    from gptme.message import Message
    from gptme.util.context import include_paths

    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    # Create a minimal PNG file
    img_file = tmp_path / "screenshot.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    # User message with image path embedded in text (like paste handler output)
    msg = Message("user", f"View this image: {img_file}")
    result = include_paths(msg, workspace=None)

    # Image should be auto-attached to msg.files
    assert len(result.files) == 1
    assert Path(str(result.files[0])).name == "screenshot.png"


def test_include_paths_per_message_size_budget(tmp_path, monkeypatch, caplog):
    """include_paths must stop embedding files once the per-message budget is exceeded."""
    import logging

    from gptme.message import Message
    from gptme.util.context import include_paths

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)

    # Create several moderately sized files that collectively exceed the budget
    # INCLUDE_PATHS_MAX_CONTENT = 200_000. Create 3 files at ~80KB each (~240KB total)
    chunk_size = 80_000
    files = []
    for i in range(3):
        f = tmp_path / f"doc_{i}.txt"
        # Use unique per-file content so dedup doesn't interfere
        body = f"marker_{i}_" + "x" * (chunk_size - len(f"marker_{i}_"))
        f.write_text(body)
        files.append(f)

    # Reference all three files in the message
    refs = "\n".join(f"`{f.name}`" for f in files)
    msg = Message("user", f"Read these:\n{refs}")
    caplog.set_level(logging.WARNING)

    result = include_paths(msg)

    # First two files should be embedded (~160KB), third should be skipped
    assert "marker_0_" in result.content
    assert "marker_1_" in result.content
    assert "marker_2_" not in result.content

    # Warning should be logged about the skipped paths
    log_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("per-message content budget reached" in m for m in log_messages)
    assert any("doc_2.txt" in m for m in log_messages)

    # Skipped file must not be attached via msg.files (budget bypass via _parse_prompt_files)
    attached_names = [f.name for f in (result.files or []) if isinstance(f, Path)]
    assert "doc_2.txt" not in attached_names


def test_embed_attached_preserves_image_files(tmp_path):
    """Images in msg.files should survive embed_attached_file_content.

    Text files get embedded as codeblocks and removed from msg.files.
    Image files (binary) should remain in msg.files for provider-specific
    handling (base64 encoding in _process_file).
    """
    from gptme.message import Message
    from gptme.util.context import embed_attached_file_content

    # Create both a text file and an image file
    text_file = tmp_path / "readme.txt"
    text_file.write_text("hello world")

    img_file = tmp_path / "photo.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    msg = Message("user", "Check these files", files=[text_file, img_file])
    result = embed_attached_file_content(msg, workspace=tmp_path)

    # Text file should be embedded in content and removed from files
    assert "hello world" in result.content
    assert not any(Path(str(f)).name == "readme.txt" for f in result.files)

    # Image file should remain in files (not embedded)
    assert any(Path(str(f)).name == "photo.png" for f in result.files)


def test_image_auto_attach_end_to_end(tmp_path, monkeypatch):
    """End-to-end test: image path in user text → include_paths → embed → msgs2dicts.

    Verifies that an image mentioned by path in a user message survives the
    full message processing pipeline and appears in the final dict's files list.
    """
    from gptme.message import Message, msgs2dicts
    from gptme.util.context import embed_attached_file_content, include_paths

    # Ensure GPTME_DISABLE_PATH_INCLUDE does not interfere
    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)

    # Create a minimal PNG
    img_file = tmp_path / "paste_20260225.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    # Step 1: include_paths extracts the image path
    msg = Message("user", str(img_file))
    msg = include_paths(msg, workspace=None)
    assert len(msg.files) == 1, "include_paths should detect and attach image"

    # Step 2: embed_attached_file_content preserves images
    msg = embed_attached_file_content(msg, workspace=None)
    assert len(msg.files) == 1, (
        "embed should preserve image in files (not embed as text)"
    )

    # Step 3: msgs2dicts preserves files for provider processing
    dicts = msgs2dicts([msg])
    assert "files" in dicts[0], "files should be present in message dict"
    assert len(dicts[0]["files"]) == 1, "image file should survive serialization"


def test_chained_prompts_continue_after_complete():
    """When the complete tool fires mid-way through chained prompts, remaining
    prompts should still be processed.

    Regression test for: gptme 'prompt1' - 'prompt2' exits after prompt1 if
    the LLM calls the complete tool, never processing prompt2.
    """
    import sys

    from gptme.chat import _run_chat_loop
    from gptme.message import Message
    from gptme.tools.complete import SessionCompleteException

    # gptme/__init__.py does `from .chat import chat`, which shadows the
    # gptme.chat MODULE attribute with the chat FUNCTION on the gptme package.
    # patch("gptme.chat.X") resolves gptme.chat via getattr(gptme, 'chat') and
    # gets the function, not the module. Use sys.modules to get the real module.
    _chat_mod = sys.modules["gptme.chat"]

    manager = MagicMock()
    manager.log = MagicMock()
    manager.workspace = Path("/tmp")
    manager.logdir = Path("/tmp/logdir")

    call_count = 0

    def mock_process(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First prompt: LLM calls complete tool
            raise SessionCompleteException("first prompt done")
        # Second prompt: completes normally

    prompt_queue = [Message("user", "first prompt"), Message("user", "second prompt")]

    with (
        patch.object(
            _chat_mod, "_process_message_conversation", side_effect=mock_process
        ),
        patch.object(_chat_mod, "trigger_hook", return_value=[]),
        patch.object(_chat_mod, "include_paths", side_effect=lambda msg, ws: msg),
        patch.object(_chat_mod, "execute_cmd", return_value=False),
    ):
        # Should NOT raise — queue has a second prompt, so complete should not exit
        _run_chat_loop(
            manager=manager,
            prompt_queue=prompt_queue,
            stream=False,
            tool_format="markdown",
            model=None,
            interactive=False,
        )

    assert call_count == 2, "Both chained prompts should have been processed"


def test_chained_prompts_complete_exits_when_last():
    """When complete fires on the last (or only) chained prompt, exit normally."""
    import sys

    from gptme.chat import _run_chat_loop
    from gptme.message import Message
    from gptme.tools.complete import SessionCompleteException

    # See test_chained_prompts_continue_after_complete for why we use sys.modules.
    _chat_mod = sys.modules["gptme.chat"]

    manager = MagicMock()
    manager.log = MagicMock()
    manager.workspace = Path("/tmp")
    manager.logdir = Path("/tmp/logdir")

    def mock_process(*args, **kwargs):
        raise SessionCompleteException("done")

    prompt_queue = [Message("user", "only prompt")]

    with (
        patch.object(
            _chat_mod, "_process_message_conversation", side_effect=mock_process
        ),
        patch.object(_chat_mod, "trigger_hook", return_value=[]),
        patch.object(_chat_mod, "include_paths", side_effect=lambda msg, ws: msg),
        patch.object(_chat_mod, "execute_cmd", return_value=False),
        pytest.raises(SessionCompleteException),
    ):
        # Should raise — no more prompts in queue after this one
        _run_chat_loop(
            manager=manager,
            prompt_queue=prompt_queue,
            stream=False,
            tool_format="markdown",
            model=None,
            interactive=False,
        )


def test_complete_hook_does_not_refire_on_next_prompt():
    """complete_hook must not raise when the complete tool call is in a prior turn.

    Regression: complete_hook scanned the entire conversation history and would
    re-raise SessionCompleteException on the second chained prompt because the
    last assistant message still contained the `complete` tool call from turn 1.
    Fix: only look at assistant messages AFTER the most recent user message.
    """
    from gptme.message import Message
    from gptme.tools import init_tools
    from gptme.tools.complete import complete_hook

    # complete tool is disabled_by_default — init with it enabled so
    # ToolUse.iter_from_content can recognise the ```complete``` block.
    init_tools(allowlist=["complete"])

    # Simulate message history after processing the first chained prompt:
    #   user1 → assistant1 (calls `complete`) → system ("Task complete") → user2
    # When GENERATION_PRE fires for user2, these are the messages in the log.
    messages = [
        Message("system", "You are an assistant."),
        Message("user", "first prompt"),
        Message("assistant", "```complete\n```"),
        Message("system", "Task complete. Autonomous session finished."),
        Message("user", "second prompt"),  # current turn starts here
    ]

    # complete_hook must NOT raise — the complete call belongs to the previous turn.
    result = list(complete_hook(messages))
    assert result == []


def test_complete_hook_fires_in_current_turn():
    """complete_hook must raise when the complete tool call is in the current turn."""
    from gptme.message import Message
    from gptme.tools import init_tools
    from gptme.tools.complete import SessionCompleteException, complete_hook

    # complete tool is disabled_by_default — init with it enabled.
    init_tools(allowlist=["complete"])

    # Simulate history where LLM has just called `complete` in the current turn:
    #   user1 → assistant1 (calls `complete`) → system ("Task complete")
    # When GENERATION_PRE fires again (loop continuing), these are the messages.
    messages = [
        Message("system", "You are an assistant."),
        Message("user", "first prompt"),
        Message("assistant", "```complete\n```"),
        Message("system", "Task complete. Autonomous session finished."),
    ]

    # complete_hook MUST raise — complete was called in the current turn.
    with pytest.raises(SessionCompleteException):
        list(complete_hook(messages))
