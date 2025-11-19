from unittest.mock import MagicMock, patch

from gptme.message import Message
from gptme.tools.tts import (
    join_short_sentences,
    re_thinking,
    re_tool_use,
    speak_on_generation,
    split_text,
    tool,
    wait_on_session_end,
)


def test_split_text_single_sentence():
    assert split_text("Hello, world!") == ["Hello, world!"]


def test_split_text_multiple_sentences():
    assert split_text("Hello, world! I'm Bob") == ["Hello, world!", "I'm Bob"]


def test_split_text_decimals():
    # Don't split on periods in numbers with decimals
    # Note: For TTS purposes, having a period at the end is acceptable
    result = split_text("0.5x")
    assert result == ["0.5x"]


def test_split_text_numbers_before_punctuation():
    assert split_text("The dog was 12. The cat was 3.") == [
        "The dog was 12.",
        "The cat was 3.",
    ]


def test_split_text_paragraphs():
    assert split_text(
        """
Text without punctuation

Another paragraph
"""
    ) == ["Text without punctuation", "", "Another paragraph"]


def test_join_short_sentences():
    # Test basic sentence joining (should preserve original spacing)
    sentences: list[str] = ["Hello.", "World."]
    result = join_short_sentences(sentences, min_length=100)
    assert result == ["Hello. World."]  # No extra space after period

    # Test with min_length to force splits
    sentences = ["One two", "three four", "five."]
    result = join_short_sentences(sentences, min_length=10)
    assert result == ["One two three four five."]

    # Test with max_length to limit combining
    result = join_short_sentences(sentences, min_length=10, max_length=20)
    assert result == ["One two three four", "five."]

    # Test with empty lines (should preserve paragraph breaks)
    sentences = ["Hello.", "", "World."]
    result = join_short_sentences(sentences, min_length=100)
    assert result == ["Hello.", "", "World."]

    # Test with multiple sentences and punctuation
    sentences = ["First.", "Second!", "Third?", "Fourth."]
    result = join_short_sentences(sentences, min_length=100)
    assert result == ["First. Second! Third? Fourth."]


def test_split_text_lists():
    assert split_text(
        """
- Test
- Test2
"""
    ) == ["- Test", "- Test2"]

    # Markdown list (numbered)
    # Also tests punctuation in list items, which shouldn't cause extra pauses (unlike paragraphs)
    assert split_text(
        """
1. Test.
2. Test2
"""
    ) == ["1. Test.", "2. Test2"]

    # We can strip trailing punctuation from list items
    assert [
        part.strip()
        for part in split_text(
            """
1. Test.
2. Test2.
"""
        )
    ] == ["1. Test", "2. Test2"]

    # Replace asterisk lists with dashes
    assert split_text(
        """
* Test
* Test2
"""
    ) == ["- Test", "- Test2"]


def test_clean_for_speech():
    # test underlying regexes

    # complete
    assert re_thinking.search("<thinking>thinking</thinking>")
    assert re_tool_use.search("```tool\ncontents\n```")

    # with arg
    assert re_tool_use.search("```save ~/path_to/test-file1.txt\ncontents\n```")

    # with `text` contents
    assert re_tool_use.search("```file.md\ncontents with `code` string\n```")

    # incomplete
    assert re_thinking.search("\n<thinking>thinking")
    assert re_tool_use.search("```savefile.txt\ncontents")

    # make sure spoken content is correct
    assert (
        re_tool_use.sub("", "Using tool\n```tool\ncontents\n```").strip()
        == "Using tool"
    )
    assert re_tool_use.sub("", "```tool\ncontents\n```\nRan tool").strip() == "Ran tool"


def test_hooks_registered():
    """Test that TTS hooks are properly registered in the tool spec."""
    assert "speak_on_generation" in tool.hooks
    assert "wait_on_session_end" in tool.hooks

    # Check hook types
    assert tool.hooks["speak_on_generation"][0] == "generation_post"
    assert tool.hooks["wait_on_session_end"][0] == "session_end"


@patch("gptme.tools.tts.speak")
def test_speak_on_generation_hook(mock_speak):
    """Test that speak_on_generation hook calls speak() for assistant messages."""
    # Test with assistant message
    msg = Message("assistant", "Hello, world!")
    result = list(speak_on_generation(message=msg))

    # Should call speak with message content
    mock_speak.assert_called_once_with("Hello, world!")
    # Hook yields None from the bare yield statement
    assert len(result) == 1
    assert result[0] is None

    # Reset mock
    mock_speak.reset_mock()

    # Test with non-assistant message (should not speak)
    user_msg = Message("user", "Test user message")
    result = list(speak_on_generation(message=user_msg))
    mock_speak.assert_not_called()


@patch("gptme.tools.tts.tts_request_queue")
@patch("gptme.tools.tts.stop")
@patch("gptme.tools.tts.os.environ.get")
@patch("gptme.util.sound.wait_for_audio")
def test_wait_on_session_end_hook(mock_wait_audio, mock_env_get, mock_stop, mock_queue):
    """Test that wait_on_session_end hook waits for TTS when enabled."""
    # Mock GPTME_VOICE_FINISH enabled
    mock_env_get.return_value = "1"

    # Create mock manager
    mock_manager = MagicMock()

    # Call the hook
    result = list(wait_on_session_end(manager=mock_manager))

    # Should wait for queue
    mock_queue.join.assert_called_once()
    # Should wait for audio
    mock_wait_audio.assert_called_once()

    # Hook yields None from the bare yield statement
    assert len(result) == 1
    assert result[0] is None


@patch("gptme.tools.tts.os.environ.get")
def test_wait_on_session_end_disabled(mock_env_get):
    """Test that wait_on_session_end hook does nothing when disabled."""
    # Mock GPTME_VOICE_FINISH disabled
    mock_env_get.return_value = "0"

    # Create mock manager
    mock_manager = MagicMock()

    # Call the hook
    with patch("gptme.tools.tts.tts_request_queue") as mock_queue:
        result = list(wait_on_session_end(manager=mock_manager))

    # Should not wait for queue
    mock_queue.join.assert_not_called()

    # Should yield nothing
    assert len(result) == 0
