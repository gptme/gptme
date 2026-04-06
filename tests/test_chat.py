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


def test_include_paths_image_auto_attach(tmp_path):
    """Image files in user messages should be auto-attached to msg.files.

    This verifies the full pipeline: _find_potential_paths detects the path,
    _parse_prompt_files validates it as a supported binary format, and
    include_paths adds it to msg.files (not embedded as text content).
    """
    from gptme.message import Message
    from gptme.util.context import include_paths

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


def test_include_paths_image_in_text(tmp_path):
    """Image paths embedded in natural language text should be auto-attached.

    Simulates the scenario where a user types 'View this image ~/test.png'
    or a paste handler inserts 'View this image: /path/to/image.png'.
    """
    from gptme.message import Message
    from gptme.util.context import include_paths

    # Create a minimal PNG file
    img_file = tmp_path / "screenshot.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    # User message with image path embedded in text (like paste handler output)
    msg = Message("user", f"View this image: {img_file}")
    result = include_paths(msg, workspace=None)

    # Image should be auto-attached to msg.files
    assert len(result.files) == 1
    assert Path(str(result.files[0])).name == "screenshot.png"


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


def test_image_auto_attach_end_to_end(tmp_path):
    """End-to-end test: image path in user text → include_paths → embed → msgs2dicts.

    Verifies that an image mentioned by path in a user message survives the
    full message processing pipeline and appears in the final dict's files list.
    """
    from gptme.message import Message, msgs2dicts
    from gptme.util.context import embed_attached_file_content, include_paths

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


def test_run_chat_loop_clears_status_line_on_normal_exit(monkeypatch):
    import importlib

    chat_module = importlib.import_module("gptme.chat")

    class _Manager:
        def append(self, _msg):
            return None

    cleared: list[bool] = []

    monkeypatch.setattr(chat_module, "trigger_hook", lambda *args, **kwargs: [])
    monkeypatch.setattr(chat_module, "clear_status_line", lambda: cleared.append(True))

    chat_module._run_chat_loop(
        manager=_Manager(),
        prompt_queue=[],
        stream=False,
        tool_format="markdown",
        interactive=False,
        logdir=None,
        output_schema=None,
    )

    assert cleared == [True]


def test_chat_clears_status_line_on_session_complete(monkeypatch, tmp_path):
    import importlib

    from gptme.message import Message
    from gptme.tools.complete import SessionCompleteException

    chat_module = importlib.import_module("gptme.chat")
    cleared: list[bool] = []

    monkeypatch.setattr(chat_module, "init", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        chat_module,
        "get_default_model",
        lambda: type("M", (), {"full": "test/model"})(),
    )
    monkeypatch.setattr(
        chat_module,
        "get_model",
        lambda _model: type(
            "Meta",
            (),
            {"supports_streaming": True, "provider": "test", "model": "model"},
        )(),
    )

    class _FakeLog:
        def print(self, show_hidden=False):
            return None

    class _FakeManager:
        def __init__(self):
            self.log = _FakeLog()
            self.workspace = tmp_path
            self.logdir = tmp_path / "logs"

        def append(self, _msg):
            return None

    monkeypatch.setattr(
        chat_module.LogManager, "load", lambda *args, **kwargs: _FakeManager()
    )
    monkeypatch.setattr(chat_module, "trigger_hook", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        chat_module,
        "_run_chat_loop",
        lambda *args, **kwargs: (_ for _ in ()).throw(SessionCompleteException("done")),
    )
    monkeypatch.setattr(chat_module, "clear_status_line", lambda: cleared.append(True))

    chat_module.chat(
        prompt_msgs=[Message("user", "hi")],
        initial_msgs=[],
        logdir=tmp_path / "conversation",
        workspace=tmp_path,
        model="test/model",
        stream=False,
        no_confirm=True,
        interactive=False,
        show_hidden=False,
        tool_allowlist=[],
        tool_format="markdown",
        output_schema=None,
    )

    assert cleared == [True]


def test_chat_uses_saved_display_name_on_start(monkeypatch, tmp_path):
    import importlib

    from gptme.message import Message

    chat_module = importlib.import_module("gptme.chat")
    conv_names: list[str | None] = []

    monkeypatch.setattr(chat_module, "init", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        chat_module,
        "get_default_model",
        lambda: type("M", (), {"full": "test/model"})(),
    )
    monkeypatch.setattr(
        chat_module,
        "get_model",
        lambda _model: type(
            "Meta",
            (),
            {"supports_streaming": True, "provider": "test", "model": "model"},
        )(),
    )

    class _FakeLog:
        def print(self, show_hidden=False):
            return None

    class _FakeManager:
        def __init__(self):
            self.log = _FakeLog()
            self.workspace = tmp_path
            self.logdir = tmp_path / "logs"

        def append(self, _msg):
            return None

    monkeypatch.setattr(
        chat_module.LogManager, "load", lambda *args, **kwargs: _FakeManager()
    )
    monkeypatch.setattr(chat_module, "trigger_hook", lambda *args, **kwargs: [])
    monkeypatch.setattr(chat_module, "_run_chat_loop", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_module, "clear_status_line", lambda: None)
    monkeypatch.setattr(
        chat_module.ChatConfig,
        "from_logdir",
        lambda _logdir: type("Cfg", (), {"name": "Pretty Title"})(),
    )
    monkeypatch.setattr(
        chat_module,
        "set_current_conv_name",
        lambda name, refresh_status_line=True: conv_names.append(name),
    )

    chat_module.chat(
        prompt_msgs=[Message("user", "hi")],
        initial_msgs=[],
        logdir=tmp_path / "conversation",
        workspace=tmp_path,
        model="test/model",
        stream=False,
        no_confirm=True,
        interactive=False,
        show_hidden=False,
        tool_allowlist=[],
        tool_format="markdown",
        output_schema=None,
    )

    assert conv_names == ["Pretty Title", None]


def test_chat_clears_status_line_after_conv_name_reset(monkeypatch, tmp_path):
    import importlib

    from gptme.message import Message

    chat_module = importlib.import_module("gptme.chat")
    events: list[tuple[str, str | None]] = []

    monkeypatch.setattr(chat_module, "init", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        chat_module,
        "get_default_model",
        lambda: type("M", (), {"full": "test/model"})(),
    )
    monkeypatch.setattr(
        chat_module,
        "get_model",
        lambda _model: type(
            "Meta",
            (),
            {"supports_streaming": True, "provider": "test", "model": "model"},
        )(),
    )

    class _FakeLog:
        def print(self, show_hidden=False):
            return None

    class _FakeManager:
        def __init__(self):
            self.log = _FakeLog()
            self.workspace = tmp_path
            self.logdir = tmp_path / "logs"

        def append(self, _msg):
            return None

    monkeypatch.setattr(
        chat_module.LogManager, "load", lambda *args, **kwargs: _FakeManager()
    )
    monkeypatch.setattr(chat_module, "trigger_hook", lambda *args, **kwargs: [])
    monkeypatch.setattr(chat_module, "_run_chat_loop", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        chat_module,
        "set_current_conv_name",
        lambda name, refresh_status_line=True: events.append(("set", name)),
    )
    monkeypatch.setattr(
        chat_module,
        "clear_status_line",
        lambda: events.append(("clear", None)),
    )

    chat_module.chat(
        prompt_msgs=[Message("user", "hi")],
        initial_msgs=[],
        logdir=tmp_path / "conversation",
        workspace=tmp_path,
        model="test/model",
        stream=False,
        no_confirm=True,
        interactive=False,
        show_hidden=False,
        tool_allowlist=[],
        tool_format="markdown",
        output_schema=None,
    )

    assert events[-2:] == [("clear", None), ("set", None)]


def test_chat_resets_conv_name_on_exit(monkeypatch, tmp_path):
    import importlib

    from gptme.message import Message

    chat_module = importlib.import_module("gptme.chat")
    conv_names: list[str | None] = []

    monkeypatch.setattr(chat_module, "init", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        chat_module,
        "get_default_model",
        lambda: type("M", (), {"full": "test/model"})(),
    )
    monkeypatch.setattr(
        chat_module,
        "get_model",
        lambda _model: type(
            "Meta",
            (),
            {"supports_streaming": True, "provider": "test", "model": "model"},
        )(),
    )

    class _FakeLog:
        def print(self, show_hidden=False):
            return None

    class _FakeManager:
        def __init__(self):
            self.log = _FakeLog()
            self.workspace = tmp_path
            self.logdir = tmp_path / "logs"

        def append(self, _msg):
            return None

    monkeypatch.setattr(
        chat_module.LogManager, "load", lambda *args, **kwargs: _FakeManager()
    )
    monkeypatch.setattr(chat_module, "trigger_hook", lambda *args, **kwargs: [])
    monkeypatch.setattr(chat_module, "_run_chat_loop", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_module, "clear_status_line", lambda: None)
    monkeypatch.setattr(
        chat_module,
        "set_current_conv_name",
        lambda name, refresh_status_line=True: conv_names.append(name),
    )

    chat_module.chat(
        prompt_msgs=[Message("user", "hi")],
        initial_msgs=[],
        logdir=tmp_path / "conversation",
        workspace=tmp_path,
        model="test/model",
        stream=False,
        no_confirm=True,
        interactive=False,
        show_hidden=False,
        tool_allowlist=[],
        tool_format="markdown",
        output_schema=None,
    )

    assert conv_names == ["conversation", None]


def test_try_auto_name_and_refresh_updates_conv_name(monkeypatch, tmp_path):
    import importlib

    from gptme.message import Message

    chat_module = importlib.import_module("gptme.chat")
    conv_names: list[str | None] = []
    saved_messages = [Message("user", "hello"), Message("assistant", "hi")]

    monkeypatch.setattr(
        chat_module.ChatConfig,
        "from_logdir",
        lambda logdir: type("Cfg", (), {"name": None, "_logdir": logdir})(),
    )
    monkeypatch.setattr(
        chat_module,
        "try_auto_name",
        lambda config, messages, model: (
            "Auto Named"
            if config._logdir == tmp_path
            and messages == saved_messages
            and model == "test/model"
            else None
        ),
    )
    monkeypatch.setattr(
        chat_module,
        "set_current_conv_name",
        lambda name, refresh_status_line=True: conv_names.append(name),
    )

    result = chat_module._try_auto_name_and_refresh(
        tmp_path,
        saved_messages,
        "test/model",
    )

    assert result == "Auto Named"
    assert conv_names == ["Auto Named"]
