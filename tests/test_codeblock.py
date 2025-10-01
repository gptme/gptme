from gptme.codeblock import Codeblock, _extract_codeblocks


def test_extract_codeblocks_basic():
    markdown = """
Some text

```python
def hello():
    print("Hello, World!")
```

More text
"""
    assert Codeblock.iter_from_markdown(markdown) == [
        Codeblock("python", 'def hello():\n    print("Hello, World!")')
    ]


def test_extract_codeblocks_multiple():
    markdown = """
```java
public class Main {
    public static void main(String[] args) {
        System.out.println("Hello, Java!");
    }
}
```

Some text

```python
def greet(name):
    return f"Hello, {name}!"
```
"""
    assert Codeblock.iter_from_markdown(markdown) == [
        Codeblock(
            "java",
            'public class Main {\n    public static void main(String[] args) {\n        System.out.println("Hello, Java!");\n    }\n}',
        ),
        Codeblock("python", 'def greet(name):\n    return f"Hello, {name}!"'),
    ]


def test_extract_codeblocks_nested():
    markdown = """
```python
def print_readme():
    print('''Usage:

```javascript
callme()
```

''')
```

"""
    assert Codeblock.iter_from_markdown(markdown) == [
        Codeblock(
            "python",
            "def print_readme():\n    print('''Usage:\n\n```javascript\ncallme()\n```\n\n''')",
        )
    ]


def test_extract_codeblocks_unfinished_nested():
    markdown = """
```python
def print_readme():
    print('''Usage:
```javascript

"""
    assert Codeblock.iter_from_markdown(markdown) == []


def test_extract_codeblocks_empty():
    assert Codeblock.iter_from_markdown("") == []


def test_extract_codeblocks_text_only():
    assert (
        Codeblock.iter_from_markdown("Just some regular text\nwithout any code blocks.")
        == []
    )


def test_extract_codeblocks_no_language():
    markdown = """
```
def hello():
    print("Hello, World!")
```
"""
    assert Codeblock.iter_from_markdown(markdown) == [
        Codeblock("", 'def hello():\n    print("Hello, World!")')
    ]


def test_extract_codeblocks_markdown_with_nested_no_langtag():
    """
    Test that markdown blocks containing nested codeblocks without language tags
    are parsed correctly. This addresses the issue where ``` followed by content
    was mistaken for a closing tag instead of an opening tag.
    """
    markdown = """
```markdown
# README

Installation:

```
npm install
```

Usage:

```
node app.js
```

Done!
```
"""
    # Should parse as single markdown block, not get cut off at first ```
    blocks = Codeblock.iter_from_markdown(markdown)
    assert len(blocks) == 1
    assert blocks[0].lang == "markdown"

    # Should contain all the nested content
    content = blocks[0].content
    assert "npm install" in content
    assert "node app.js" in content
    assert "Done!" in content


def test_extract_codeblocks_consecutive():
    """Test that consecutive codeblocks are both extracted."""
    markdown = """```python
print("first")
```
```bash
echo "second"
```"""
    codeblocks = list(_extract_codeblocks(markdown))
    assert len(codeblocks) == 2
    assert codeblocks[0].lang == "python"
    assert codeblocks[0].content == 'print("first")'
    assert codeblocks[0].start == 0
    assert codeblocks[1].lang == "bash"
    assert codeblocks[1].content == 'echo "second"'
    assert codeblocks[1].start == 3


def test_extract_codeblocks_streaming_interrupted():
    """
    Test case based on real interruption during streaming.

    Reproduces issue where bare ``` after descriptive text was incorrectly
    treated as closing delimiter instead of opening a nested code block.
    """
    # Read the actual interrupted example
    with open("example-interrupted.txt") as f:
        content = f.read()

    # Extract just the markdown part (after "create a journal entry")
    # This should parse as a single append block with nested code blocks inside
    start_marker = "```append journal/2025-10-01.md"
    start_idx = content.find(start_marker)
    assert start_idx != -1, "Could not find append block in example"

    markdown = content[start_idx:]

    # Should extract one append block
    blocks = list(_extract_codeblocks(markdown))
    assert len(blocks) == 1, f"Expected 1 block, got {len(blocks)}"
    assert blocks[0].lang == "append journal/2025-10-01.md"

    # The content should include all the nested parts
    content_text = blocks[0].content
    assert "**Output Format:**" in content_text
    assert "Journal Entry" in content_text


def test_extract_codeblocks_nested_without_lang():
    """
    Test that nested code blocks without language tags are handled correctly.

    This reproduces the streaming interruption issue where ``` after descriptive
    text should open a nested block, not close the outer block.
    """
    # Build the test case programmatically to avoid triggering the bug
    fence = "```"

    # This is what should be parsed correctly:
    # An append block containing text followed by a nested code block example
    markdown = f"""{fence}append journal/entry.md
# Journal Entry

**Output Format:**
{fence}
key: value
{fence}

Done!
{fence}"""

    blocks = list(_extract_codeblocks(markdown))

    # Should extract one append block
    assert len(blocks) == 1, f"Expected 1 block, got {len(blocks)}"
    assert blocks[0].lang == "append journal/entry.md"

    # The content should include ALL parts including the nested block and "Done!"
    content = blocks[0].content
    assert "**Output Format:**" in content
    assert "key: value" in content
    assert (
        "Done!" in content
    ), "Content was cut off prematurely - nested block was treated as closing delimiter"


def test_extract_codeblocks_incomplete_streaming():
    """
    Test parsing incomplete content as would happen during streaming.

    When content ends with ``` after descriptive text, but more content
    is expected, the parser should not extract an incomplete block.
    """
    fence = "```"

    # Simulate streaming: content stops mid-block after a bare ```
    incomplete_markdown = f"""{fence}append journal/entry.md
# Journal Entry

**Output Format:**
{fence}"""

    # During streaming, this appears incomplete - we shouldn't extract it yet
    # With streaming=True, requires blank line after ``` to confirm closure
    blocks = list(_extract_codeblocks(incomplete_markdown, streaming=True))

    # Should not extract incomplete blocks during streaming
    assert len(blocks) == 0, "Should not extract incomplete block during streaming"

    # But without streaming flag (completed message), should extract
    blocks_complete = list(_extract_codeblocks(incomplete_markdown, streaming=False))
    assert len(blocks_complete) == 1, "Should extract when message is complete"
