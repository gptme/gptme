from gptme.tools import init_tools
from gptme.tools.chats import _format_message_with_context, list_chats, search_chats


def test_chats(capsys):
    init_tools([])
    list_chats()
    captured = capsys.readouterr()
    if "No conversations found" in captured.out:
        search_chats("python", system=True)
        captured = capsys.readouterr()
        assert "No results found" in captured.out
    else:
        assert "1." in captured.out
        search_chats("python", system=True)
        captured = capsys.readouterr()
        assert "Search results" in captured.out


def test_format_message_basic():
    """Test basic match highlighting."""
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


def test_format_message_case_insensitive_highlight():
    """Test that highlighting works case-insensitively and preserves original casing."""
    result = _format_message_with_context("Hello WORLD", "world")
    # The ANSI highlight should be present
    assert "\033[1;31m" in result
    # Original casing must be preserved inside the highlight (not lowercased)
    assert "WORLD" in result
