from gptme.codeblock import Codeblock


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
