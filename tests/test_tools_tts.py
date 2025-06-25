from gptme.tools.tts import (
    join_short_sentences,
    re_thinking,
    re_tool_use,
    split_text,
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
