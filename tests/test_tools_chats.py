import json
import os
import sys

from gptme.tools import init_tools
from gptme.tools.chats import _format_message_with_context, list_chats, search_chats


def test_chats(tmp_path, monkeypatch, capsys):
    """Test list_chats and search_chats with a controlled conversation store."""
    # Isolate from real conversation history to make the test deterministic
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    # Create a test conversation containing "python"
    conv_dir = tmp_path / "2026-01-01-python-demo"
    conv_dir.mkdir()
    (conv_dir / "conversation.jsonl").write_text(
        json.dumps(
            {
                "role": "user",
                "content": "How do I write python code?",
                "timestamp": "2026-01-01T00:00:00+00:00",
            }
        )
        + "\n"
        + json.dumps(
            {
                "role": "assistant",
                "content": "Here is how to write python: use def for functions.",
                "timestamp": "2026-01-01T00:00:01+00:00",
            }
        )
        + "\n"
    )

    init_tools([])
    list_chats()
    captured = capsys.readouterr()
    assert "1." in captured.out

    search_chats("python", system=True)
    captured = capsys.readouterr()
    assert "Search results" in captured.out


def test_format_message_basic(monkeypatch):
    """Test basic match highlighting."""
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    result = _format_message_with_context("hello world", "world")
    assert "world" in result
    # Should contain ANSI bold red escape codes
    assert "\033[1;31m" in result


def test_format_message_no_match():
    """Test fallback when no match found."""
    result = _format_message_with_context("hello world", "xyz")
    assert result == "hello world"


def test_format_message_regex_special_chars():
    """Test that regex special characters in query don't crash."""
    # These all contain regex metacharacters
    for query in ["myFunc(", "file.txt", "a+b", "foo[0]", "x*y", "a|b"]:
        content = f"some text with {query} in it"
        result = _format_message_with_context(content, query)
        assert query in result


def test_format_message_no_repr_quotes():
    """Test that matches are not wrapped in repr quotes."""
    result = _format_message_with_context("hello world", "world")
    # Should NOT contain repr-style quotes around the match
    assert "'world'" not in result


def test_format_message_case_insensitive_highlight(monkeypatch):
    """Test that highlighting works case-insensitively and preserves original casing."""
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    result = _format_message_with_context("Hello WORLD", "world")
    # The ANSI highlight should be present
    assert "\033[1;31m" in result
    # Original casing must be preserved inside the highlight (not lowercased)
    assert "WORLD" in result


def test_search_chats_handles_non_string_content(tmp_path, monkeypatch, capsys):
    """Test that search_chats doesn't crash when messages have non-string content."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    conv_dir = tmp_path / "2026-01-01-int-content-demo"
    conv_dir.mkdir()
    # Simulate a conversation JSONL with mixed string and int content
    (conv_dir / "conversation.jsonl").write_text(
        json.dumps(
            {
                "role": "system",
                "content": "system message",
                "timestamp": "2026-01-01T00:00:00+00:00",
            }
        )
        + "\n"
        + json.dumps(
            {
                "role": "user",
                "content": 12345,  # int content - the actual crash case
                "timestamp": "2026-01-01T00:00:01+00:00",
            }
        )
        + "\n"
        + json.dumps(
            {
                "role": "assistant",
                "content": "hello world",
                "timestamp": "2026-01-01T00:00:02+00:00",
            }
        )
        + "\n"
    )

    # Should not crash on int content
    search_chats("hello", system=False)
    captured = capsys.readouterr()
    assert "Search results" in captured.out


def test_format_message_with_non_string_content():
    """Test _format_message_with_context gracefully handles non-string content."""
    result = _format_message_with_context(12345, "test")
    assert result == "[non-string content]"


def test_search_chats_with_empty_after_strip(tmp_path, monkeypatch, capsys):
    """Test that search_chats with whitespace-only query fails early."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    conv_dir = tmp_path / "2026-01-01-demo"
    conv_dir.mkdir()
    (conv_dir / "conversation.jsonl").write_text(
        json.dumps(
            {
                "role": "user",
                "content": "hello world",
                "timestamp": "2026-01-01T00:00:00+00:00",
            }
        )
        + "\n"
    )

    search_chats("   ", system=False)
    captured = capsys.readouterr()
    # Should exit early with an error message, not crash
    assert "search query cannot be empty" in captured.err


def test_search_chats_count_sort_includes_matches_beyond_scan_cutoff(
    tmp_path, monkeypatch, capsys
):
    """Count sorting considers older matches before applying max_results."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    base = 1_700_000_000
    for index in range(12):
        conv_id = f"2026-01-01-chat-{index:02d}"
        conv_dir = tmp_path / conv_id
        conv_dir.mkdir()
        contents = "\n".join(
            json.dumps(
                {
                    "role": "user",
                    "content": "count-needle" if index < 2 else "unmatched",
                    "timestamp": f"2026-01-01T00:00:{match:02d}+00:00",
                }
            )
            for match in range(3 if index == 0 else 1)
        )
        (conv_dir / "conversation.jsonl").write_text(contents + "\n")
        os.utime(conv_dir / "conversation.jsonl", (base + index, base + index))

    from gptme.tools.chats import search_chats

    search_chats("count-needle", max_results=1, sort="count")
    captured = capsys.readouterr()
    assert "2026-01-01-chat-00" in captured.out
    assert "2026-01-01-chat-01" not in captured.out
    assert "(3 matches)" in captured.out
    assert "No results found" not in captured.out


def test_search_chats_nonpositive_limit_skips_history(tmp_path, monkeypatch, capsys):
    """Nonpositive limits preserve the empty result behavior without scanning."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    def fail_get_user_conversations():
        raise AssertionError("history should not be enumerated")

    monkeypatch.setattr(
        "gptme.logmanager.get_user_conversations", fail_get_user_conversations
    )

    from gptme.tools.chats import search_chats

    for limit in (0, -1):
        search_chats("needle", max_results=limit)
        captured = capsys.readouterr()
        assert captured.out == "No results found for query: 'needle'\n"
