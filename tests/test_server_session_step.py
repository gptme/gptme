"""Unit tests for visible-output sanitization."""

from gptme.llm.visible_output import VisibleOutputSanitizer


def _sanitize(*chunks: str) -> str:
    sanitizer = VisibleOutputSanitizer()
    visible = "".join(sanitizer.feed(chunk) for chunk in chunks)
    return visible + sanitizer.finish()


def test_visible_output_sanitizer_strips_reasoning_block():
    """Reasoning tags and their content should never reach visible output."""
    result = _sanitize(
        "<thinking>\n",
        "private reasoning\n",
        "\n</thinking>\n\n",
        "Visible answer",
    )

    assert result == "Visible answer"


def test_visible_output_sanitizer_handles_single_chunk_responses():
    """Sanitization should also work when the provider returns one large chunk."""
    result = _sanitize(
        "<thinking>\nprivate reasoning\n</thinking>\n"
        '@save(call-1): {"path": "hello.py", "content": "print()"}'
    )

    assert result == '@save(call-1): {"path": "hello.py", "content": "print()"}'


def test_visible_output_sanitizer_strips_whitespace_from_tags():
    """Tags with trailing/leading whitespace should still be detected."""
    result = _sanitize(
        "<thinking> \n",  # opening tag with trailing space
        "reasoning\n",
        " </thinking>\n",  # closing tag with leading space
        "visible",
    )

    assert result == "visible"


def test_visible_output_sanitizer_all_reasoning_returns_empty():
    """If the entire response is a reasoning block, visible output should be empty."""
    result = _sanitize(
        "<thinking>\n",
        "only reasoning content here\n",
        "no visible text at all\n",
        "</thinking>\n",
    )

    assert result == ""


def test_visible_output_sanitizer_no_trailing_newline():
    """Content without a trailing newline must be flushed by finish(), not feed()."""
    # This mirrors markdown tool blocks: ```shell\nls -la\n``` (no trailing \n)
    result = _sanitize("```shell\nls -la\n```")

    # finish() must return the buffered closing fence
    assert result == "```shell\nls -la\n```"


def test_visible_output_sanitizer_closing_tag_with_inline_content_feed():
    """Visible text on the same line as a closing tag must not be silently dropped."""
    # Some models emit </think>visible text on one line (no separate newline).
    result = _sanitize(
        "<think>\n",
        "private reasoning\n",
        "</think>Visible answer\n",
    )

    assert result == "Visible answer\n"


def test_visible_output_sanitizer_closing_tag_with_inline_content_finish():
    """finish() must return visible text that follows a closing tag on the final line."""
    result = _sanitize(
        "<think>\n",
        "private reasoning\n",
        "</think>Visible answer",  # no trailing newline — flushed by finish()
    )

    assert result == "Visible answer"


def test_visible_output_sanitizer_thinking_tag_not_shadowed_by_think_prefix():
    """</thinking> must not be mismatched as </think> + 'ing>' suffix (set ordering bug)."""
    # If _CLOSING_TAGS is iterated in wrong order, "</think>" matches first and
    # slices "</thinking>visible" → "ing>visible" instead of "visible".
    result = _sanitize(
        "<thinking>\n",
        "private reasoning\n",
        "</thinking>visible answer\n",  # closing tag with inline content
    )

    assert result == "visible answer\n"


def test_visible_output_sanitizer_thinking_tag_inline_finish():
    """finish() must also handle </thinking>text without the prefix-order bug."""
    result = _sanitize(
        "<thinking>\n",
        "private reasoning\n",
        "</thinking>visible answer",  # no trailing newline — flushed by finish()
    )

    assert result == "visible answer"


def test_visible_output_sanitizer_uppercase_tags():
    """Tag detection must be case-insensitive (some providers emit <THINK>)."""
    result = _sanitize(
        "<THINK>\n",
        "private reasoning\n",
        "</THINK>\n",
        "Visible answer",
    )

    assert result == "Visible answer"


def test_visible_output_sanitizer_mixed_case_tag_with_inline_content():
    """Mixed-case closing tag with inline content must be handled correctly."""
    result = _sanitize(
        "<Think>\n",
        "private reasoning\n",
        "</Think>Visible answer\n",
    )

    assert result == "Visible answer\n"
