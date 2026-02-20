from pathlib import Path

import pytest

from gptme.message import Message, len_tokens
from gptme.util.reduce import _truncate_details_blocks, reduce_log, truncate_msg

# Project root
root = Path(__file__).parent.parent

# Some large files
readme = root / "README.md"
cli = root / "gptme" / "cli" / "main.py"
htmlindex = root / "gptme" / "server" / "static" / "index.html"

long_msg = Message(
    "system",
    content="\n\n".join(
        f"```{fn.name}\n{fn.read_text()}\n```" for fn in [cli, htmlindex]
    ),
)


def test_truncate_msg():
    len_pre = len_tokens(long_msg, "gpt-4")
    truncated = truncate_msg(long_msg)
    assert truncated is not None
    len_post = len_tokens(truncated, "gpt-4")
    assert len_pre > len_post
    assert "[...]" in truncated.content
    assert "```main.py" in truncated.content
    assert "```index.html" in truncated.content


def test_truncate_details_block():
    """Test that long <details> blocks are truncated."""
    # Generate a long details block with 50 lines
    body_lines = [f"line {i}: some log output here" for i in range(50)]
    body = "\n".join(body_lines)
    content = f"Some context.\n<details>\n<summary>CI logs</summary>\n{body}\n</details>\nMore context."

    msg = Message("system", content=content)
    truncated = truncate_msg(msg)
    assert truncated is not None
    assert "[...]" in truncated.content
    # summary is preserved
    assert "<summary>CI logs</summary>" in truncated.content
    # opening and closing tags preserved
    assert "<details>" in truncated.content
    assert "</details>" in truncated.content
    # surrounding context preserved
    assert "Some context." in truncated.content
    assert "More context." in truncated.content
    # first and last lines preserved
    assert "line 0:" in truncated.content
    assert "line 49:" in truncated.content
    # middle lines removed
    assert "line 25:" not in truncated.content


def test_truncate_details_short():
    """Short <details> blocks should not be truncated."""
    body = "\n".join(f"line {i}" for i in range(5))
    content = f"<details>\n<summary>Short</summary>\n{body}\n</details>"

    msg = Message("system", content=content)
    truncated = truncate_msg(msg)
    # No truncation needed, should return None
    assert truncated is None


def test_truncate_details_no_summary():
    """<details> without <summary> should still be truncated."""
    body_lines = [f"log line {i}" for i in range(50)]
    body = "\n".join(body_lines)
    content = f"<details>\n{body}\n</details>"

    msg = Message("system", content=content)
    truncated = truncate_msg(msg)
    assert truncated is not None
    assert "[...]" in truncated.content
    assert "log line 0" in truncated.content
    assert "log line 49" in truncated.content


def test_truncate_details_and_codeblocks():
    """Both codeblocks and <details> should be truncated in the same message."""
    code_lines = "\n".join(f"    code line {i}" for i in range(50))
    details_lines = "\n".join(f"detail line {i}" for i in range(50))
    content = (
        f"```python\n{code_lines}\n```\n\n"
        f"<details>\n<summary>Logs</summary>\n{details_lines}\n</details>"
    )

    msg = Message("system", content=content)
    truncated = truncate_msg(msg)
    assert truncated is not None
    # Both should be truncated
    assert truncated.content.count("[...]") == 2


def test_truncate_details_helper():
    """Test the _truncate_details_blocks helper directly."""
    body = "\n".join(f"line {i}" for i in range(30))
    content = f"<details>\n<summary>Test</summary>\n{body}\n</details>"
    result = _truncate_details_blocks(content, lines_pre=5, lines_post=5)
    assert "[...]" in result
    assert "line 0" in result
    assert "line 29" in result
    assert "line 15" not in result


def test_truncate_details_nested():
    """Nested <details> blocks should be handled correctly (only outer truncated)."""
    inner_body = "\n".join(f"inner {i}" for i in range(5))
    outer_lines = [f"outer {i}" for i in range(40)]
    # Insert a nested <details> block in the middle
    outer_lines.insert(
        20,
        f"<details>\n<summary>Inner</summary>\n{inner_body}\n</details>",
    )
    outer_body = "\n".join(outer_lines)
    content = f"<details>\n<summary>Outer</summary>\n{outer_body}\n</details>"

    result = _truncate_details_blocks(content, lines_pre=5, lines_post=5)
    assert "[...]" in result
    # Outer structure preserved
    assert "<summary>Outer</summary>" in result
    # First and last outer lines preserved
    assert "outer 0" in result
    assert "outer 39" in result
    # Middle lines truncated
    assert "outer 15" not in result


@pytest.mark.slow
def test_reduce_log():
    msgs = [
        Message("system", content="system prompt"),
        Message("user", content=" ".join(fn.name for fn in [readme, cli, htmlindex])),
        long_msg,
    ]
    len_pre = len_tokens(msgs, "gpt-4")
    print(f"{len_pre=}")

    limit = 1000
    reduced = list(reduce_log(msgs, limit=limit))
    len_post = len_tokens(reduced, "gpt-4")
    print(f"{len_post=}")
    print(f"{reduced[-1].content=}")

    assert len_pre > len_post
    assert len_post < limit
