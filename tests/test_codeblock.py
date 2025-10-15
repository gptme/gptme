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
    # Read the actual interrupted example relative to this test file
    script_dir = __file__.rsplit("/", 1)[0]
    with open(f"{script_dir}/data/example-interrupted.txt") as f:
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


def test_streaming_parameter_comprehensive():
    """
    Comprehensive test for streaming parameter behavior.

    Tests both positive and negative cases:
    - Streaming=True with blank line → should extract
    - Streaming=True without blank line → should NOT extract
    - Streaming=False with blank line → should extract
    - Streaming=False without blank line → should extract
    """
    fence = "```"

    # Case 1: Streaming=True, WITH blank line (positive case)
    # Should extract because blank line confirms completion
    markdown_with_blank = f"""{fence}shell
echo "hello"
{fence}

"""
    blocks = list(_extract_codeblocks(markdown_with_blank, streaming=True))
    assert len(blocks) == 1, "Should extract block when streaming=True with blank line"
    assert blocks[0].lang == "shell"
    assert blocks[0].content == 'echo "hello"'

    # Case 2: Streaming=True, WITHOUT blank line (negative case)
    # Should NOT extract because no blank line to confirm completion
    markdown_without_blank = f"""{fence}shell
echo "hello"
{fence}"""
    blocks = list(_extract_codeblocks(markdown_without_blank, streaming=True))
    assert (
        len(blocks) == 0
    ), "Should NOT extract block when streaming=True without blank line"

    # Case 3: Streaming=False, WITH blank line (positive case)
    # Should extract normally
    blocks = list(_extract_codeblocks(markdown_with_blank, streaming=False))
    assert len(blocks) == 1, "Should extract block when streaming=False with blank line"
    assert blocks[0].lang == "shell"

    # Case 4: Streaming=False, WITHOUT blank line (positive case)
    # Should extract because message is complete (not streaming)
    blocks = list(_extract_codeblocks(markdown_without_blank, streaming=False))
    assert (
        len(blocks) == 1
    ), "Should extract block when streaming=False even without blank line"
    assert blocks[0].lang == "shell"
    assert blocks[0].content == 'echo "hello"'


def test_streaming_nested_blocks():
    """
    Test streaming behavior with nested code blocks.

    Ensures that nested blocks don't cause premature extraction during streaming.
    """
    fence = "```"

    # Case 1: Nested block without blank line during streaming
    # Should NOT extract because the outer block isn't confirmed complete
    nested_markdown = f"""{fence}save example.md
# Example

Usage:
{fence}
npm install
{fence}

Done!
{fence}"""

    # During streaming: should NOT extract without blank line
    blocks = list(_extract_codeblocks(nested_markdown, streaming=True))
    assert (
        len(blocks) == 0
    ), "Should NOT extract nested block during streaming without blank line"

    # After completion: should extract
    blocks = list(_extract_codeblocks(nested_markdown, streaming=False))
    assert len(blocks) == 1, "Should extract nested block when complete"
    assert "npm install" in blocks[0].content
    assert "Done!" in blocks[0].content

    # Case 2: Nested block WITH blank line during streaming
    # Should extract because blank line confirms completion
    nested_with_blank = f"""{fence}save example.md
# Example

Usage:
{fence}
npm install
{fence}

Done!
{fence}

"""

    blocks = list(_extract_codeblocks(nested_with_blank, streaming=True))
    assert (
        len(blocks) == 1
    ), "Should extract nested block with blank line during streaming"
    assert "npm install" in blocks[0].content
    assert "Done!" in blocks[0].content


def test_extract_patch_codeblock_with_nested_backticks():
    """
    Test extraction of patch codeblocks containing nested triple backticks.

    This reproduces an issue where patch blocks with code examples
    inside them (using ```) were incorrectly parsed during streaming.
    """
    # Read the actual example that failed
    script_dir = __file__.rsplit("/", 1)[0]
    with open(f"{script_dir}/data/example-patch-codeblock.txt") as f:
        content = f.read()

    # The file has a blank line after the closing ```, so it should extract in streaming mode
    blocks = list(_extract_codeblocks(content, streaming=True))
    assert (
        len(blocks) == 1
    ), f"Expected 1 patch block in streaming mode, got {len(blocks)}"

    block = blocks[0]
    assert block.lang.startswith(
        "patch "
    ), f"Expected patch block, got lang='{block.lang}'"

    # The content should include all the patch markers and nested code blocks
    assert "<<<<<<< ORIGINAL" in block.content
    assert "=======" in block.content
    assert ">>>>>>> UPDATED" in block.content
    assert "```text" in block.content, "Should preserve nested code block markers"
    assert block.content.count("```text") == 2, "Should have both text blocks"
    assert "git grep" in block.content

    # Test without blank line - should NOT extract in streaming mode
    content_no_blank = content.rstrip()  # Remove trailing whitespace
    blocks = list(_extract_codeblocks(content_no_blank, streaming=True))
    assert (
        len(blocks) == 0
    ), f"Should not extract during streaming without blank line, got {len(blocks)} blocks"

    # But should extract in non-streaming mode
    blocks = list(_extract_codeblocks(content_no_blank, streaming=False))
    assert (
        len(blocks) == 1
    ), f"Expected 1 patch block in non-streaming mode, got {len(blocks)}"


def test_multiple_sequential_nested_blocks():
    """
    Test that multiple nested blocks with language tags are handled correctly.

    When we close a nested block and return to depth 1, we should be able to
    open a new nested block immediately after.
    """
    fence = "```"
    markdown = f"""{fence}outer
First content
{fence}inner1
nested content 1
{fence}
Between blocks
{fence}inner2
nested content 2
{fence}
Final content
{fence}"""

    blocks = list(_extract_codeblocks(markdown))
    assert len(blocks) == 1, f"Expected 1 outer block, got {len(blocks)}"

    content = blocks[0].content
    # Should contain both nested blocks with their markers
    assert "```inner1" in content
    assert "nested content 1" in content
    assert "```inner2" in content
    assert "nested content 2" in content
    assert "Between blocks" in content
    assert "Final content" in content


def test_nested_block_followed_by_content():
    """
    Test that content after a nested block is included in the outer block.

    This is the key case that the depth > 1 heuristic fixes.
    """
    fence = "```"
    markdown = f"""{fence}outer
Before nested
{fence}inner
nested content
{fence}
After nested - this should be included!
{fence}"""

    blocks = list(_extract_codeblocks(markdown))
    assert len(blocks) == 1

    content = blocks[0].content
    assert "Before nested" in content
    assert "```inner" in content
    assert "nested content" in content
    assert "After nested - this should be included!" in content


def test_bare_backticks_open_nested_at_depth_1():
    """
    Test that bare backticks CAN open nested blocks when at depth 1.

    This documents the behavior where bare ``` followed by content
    opens a nested block only when we're at the top level.
    """
    fence = "```"
    markdown = f"""{fence}outer
Some content
{fence}
This starts a nested block (bare backticks at depth 1)
{fence}
More content
{fence}"""

    blocks = list(_extract_codeblocks(markdown))
    assert len(blocks) == 1

    content = blocks[0].content
    assert "Some content" in content
    assert "This starts a nested block" in content
    assert "More content" in content


def test_mixed_nested_blocks():
    """
    Test mixing language-tagged and bare-backtick nested blocks.
    """
    fence = "```"
    markdown = f"""{fence}outer
{fence}python
print("hello")
{fence}
Between
{fence}
Bare nested block
{fence}
After
{fence}"""

    blocks = list(_extract_codeblocks(markdown))
    assert len(blocks) == 1

    content = blocks[0].content
    assert "```python" in content
    assert 'print("hello")' in content
    assert "Between" in content
    assert "Bare nested block" in content
    assert "After" in content


def test_triple_nesting_preserved_as_content():
    """
    Test that triple nesting is preserved as content within the outer block.

    When we have multiple levels of nesting, the parser correctly preserves
    all nested blocks as content within the outermost block. The nested
    blocks keep their markers (```lang) so they can be parsed separately
    if needed.
    """
    fence = "```"
    markdown = f"""{fence}level1
{fence}level2
{fence}level3
innermost content
{fence}
level2 content
{fence}
level1 content
{fence}"""

    blocks = list(_extract_codeblocks(markdown))
    assert len(blocks) == 1

    # All nested levels are preserved as content with their markers
    content = blocks[0].content
    assert "```level2" in content
    assert "```level3" in content
    assert "innermost content" in content
    assert "level2 content" in content
    assert "level1 content" in content


def test_consecutive_bare_nested_blocks():
    """
    Test that consecutive bare ``` nested blocks are preserved as content.

    Multiple bare ``` blocks within an outer block are correctly preserved
    as nested content, maintaining their structure for potential nested parsing.
    """
    fence = "```"
    markdown = f"""{fence}outer
{fence}
First nested (bare)
{fence}
Second nested (bare)
{fence}
{fence}"""

    blocks = list(_extract_codeblocks(markdown))
    assert len(blocks) == 1

    # All bare backtick nested blocks are preserved in the content
    content = blocks[0].content
    assert "First nested (bare)" in content
    assert "Second nested (bare)" in content


def test_ambiguous_bare_backticks():
    """
    Documents behavior with ambiguous bare backticks and blank lines.

    When bare ``` is followed by blank lines and another ```, the parser
    treats the first ``` as closing the outer block. This creates two
    separate blocks rather than nested content.

    Users should use language tags to disambiguate if they want nested blocks.
    """
    fence = "```"
    markdown = f"""{fence}outer
Content before

{fence}

{fence}

Content after
{fence}"""

    blocks = list(_extract_codeblocks(markdown))

    # The parser treats the middle ``` as closing, creating 2 blocks
    assert len(blocks) == 2

    # First block contains content before the first bare ```
    assert blocks[0].lang == "outer"
    assert "Content before" in blocks[0].content
    assert "Content after" not in blocks[0].content

    # Second block (bare) contains content after the second bare ```
    assert blocks[1].lang == ""
    assert "Content after" in blocks[1].content
    assert "Content before" not in blocks[1].content


def test_opening_tag_has_content_after():
    """
    Opening tags (```lang) should have content on the next line.
    This helps distinguish opening from closing tags.
    """
    fence = "```"
    markdown = f"""{fence}python
print("hello")
{fence}"""

    blocks = list(_extract_codeblocks(markdown))
    assert len(blocks) == 1
    assert blocks[0].lang == "python"
    assert 'print("hello")' in blocks[0].content


def test_closing_tag_has_empty_line_after():
    """
    Closing tags (bare ```) should have empty line after or EOF.
    This helps distinguish closing from opening tags.
    """
    fence = "```"
    markdown = f"""{fence}outer
Some content
{fence}

More text after blank line
"""

    blocks = list(_extract_codeblocks(markdown))
    assert len(blocks) == 1
    assert blocks[0].lang == "outer"
    assert "Some content" in blocks[0].content
    assert "More text after blank line" not in blocks[0].content


def test_bare_backticks_followed_by_content_opens_nested():
    """
    When bare ``` is followed by content (no blank line), it opens a nested block.
    This is the key heuristic Erik suggests.
    """
    fence = "```"
    markdown = f"""{fence}outer
Before nested
{fence}
This is nested content (no blank line before)
{fence}
After nested
{fence}"""

    blocks = list(_extract_codeblocks(markdown))
    assert len(blocks) == 1
    content = blocks[0].content
    assert "Before nested" in content
    assert "This is nested content" in content
    assert "After nested" in content


def test_bare_backticks_followed_by_blank_line_closes():
    """
    When bare ``` is followed by blank line, it closes the outer block.
    This disambiguates from opening nested blocks.
    """
    fence = "```"
    markdown = f"""{fence}outer
Content
{fence}

This is outside the block
"""

    blocks = list(_extract_codeblocks(markdown))
    assert len(blocks) == 1
    assert "Content" in blocks[0].content
    assert "This is outside the block" not in blocks[0].content


def test_eof_after_closing_tag():
    """
    EOF after closing tag (```<EOF>) is valid.
    No blank line needed at end of document.
    """
    fence = "```"
    markdown = f"""{fence}python
print("hello")
{fence}"""  # EOF immediately after closing tag

    blocks = list(_extract_codeblocks(markdown))
    assert len(blocks) == 1
    assert blocks[0].lang == "python"


def test_streaming_case_incomplete_closing():
    """
    During streaming, incomplete closing might appear as bare ``` with EOF.
    This should be treated as incomplete, not opening nested.
    """
    fence = "```"
    markdown = f"""{fence}python
print("incomplete")
{fence}"""  # Streaming incomplete - no newline after closing

    blocks = list(_extract_codeblocks(markdown))
    # Should recognize as complete block, not incomplete nested
    assert len(blocks) == 1
    assert 'print("incomplete")' in blocks[0].content


def test_nested_with_same_language_tag():
    """
    Nested blocks with the same language tag as outer block.
    This can confuse parsers that track by language.
    """
    fence = "```"
    markdown = f"""{fence}python
def outer():
    code = '''{fence}python
def inner():
    pass
{fence}'''
{fence}"""

    blocks = list(_extract_codeblocks(markdown))
    assert len(blocks) == 1
    assert blocks[0].lang == "python"
    assert "def outer()" in blocks[0].content
    assert "def inner()" in blocks[0].content


def test_bare_backticks_in_string_literals():
    """
    Triple backticks inside string literals shouldn't be treated as code block markers.
    """
    fence = "```"
    markdown = f"""{fence}python
text = '''
{fence}
This is just a string, not a code block
{fence}
'''
{fence}"""

    blocks = list(_extract_codeblocks(markdown))
    assert len(blocks) == 1
    assert "This is just a string" in blocks[0].content


def test_incomplete_opening_tag_streaming():
    """
    During streaming, opening tag might be incomplete: ```py (no newline yet).
    Should not treat as complete block.
    """
    fence = "```"
    markdown = f"""{fence}py"""  # Incomplete - no newline, no content

    blocks = list(_extract_codeblocks(markdown))
    # Should not extract incomplete opening tag
    assert len(blocks) == 0


def test_indented_code_blocks():
    """
    Indented code blocks (4 spaces) vs fenced blocks.
    Should only extract fenced blocks.
    """
    fence = "```"
    markdown = f"""Regular text

    # This is indented (4 spaces)
    def foo():
        pass

{fence}python
# This is fenced
def bar():
    pass
{fence}"""

    blocks = list(_extract_codeblocks(markdown))
    assert len(blocks) == 1  # Only fenced block
    assert "def bar()" in blocks[0].content
    assert "def foo()" not in blocks[0].content


def test_backticks_in_inline_code():
    """
    Single backtick inline code shouldn't interfere with triple backticks.
    """
    fence = "```"
    markdown = f"""Use `code` inline.

{fence}python
x = `backtick`
{fence}

More `inline code`.
"""

    blocks = list(_extract_codeblocks(markdown))
    assert len(blocks) == 1
    assert "x = `backtick`" in blocks[0].content
