"""Unit tests for visible-output sanitization."""

from gptme.llm.visible_output import VisibleOutputSanitizer


def _sanitize(*chunks: str) -> str:
    sanitizer = VisibleOutputSanitizer()
    visible = "".join(sanitizer.feed(chunk) for chunk in chunks)
    return visible + sanitizer.finish()


def test_visible_output_sanitizer_strips_reasoning_block():
    """Reasoning tags and their content should never reach visible output."""
    result = _sanitize(
        "<think>\n",
        "private reasoning\n",
        "\n</think>\n\n",
        "Visible answer",
    )

    assert result == "Visible answer"


def test_visible_output_sanitizer_handles_single_chunk_responses():
    """Sanitization should also work when the provider returns one large chunk."""
    result = _sanitize(
        "<think>\nprivate reasoning\n</think>\n"
        '@save(call-1): {"path": "hello.py", "content": "print()"}'
    )

    assert result == '@save(call-1): {"path": "hello.py", "content": "print()"}'
