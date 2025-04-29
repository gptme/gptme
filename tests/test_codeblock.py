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
            "def print_readme():\n    print('''Usage:\n```javascript\ncallme()\n```\n''')",
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


def test_extract_codeblocks_nested_oneline():
    markdown = """
```python
def print_readme():
    print('''
        ```echo test```
    ''')
```
"""
    blocks = list(Codeblock.iter_from_markdown(markdown))
    assert len(blocks) == 1
    assert blocks[0].lang == "python"
    assert "```echo test```" in blocks[0].content


def test_extract_codeblocks_complete_nested():
    markdown = """
```python
def print_readme():
    print('''Usage:
```javascript
console.log('hello')
```
    ''')
```
"""
    blocks = list(Codeblock.iter_from_markdown(markdown))
    assert len(blocks) == 1
    assert blocks[0].lang == "python"
    assert "```javascript" in blocks[0].content


def test_extract_codeblocks_multiple_nested():
    markdown = """
```python
def example():
    code = '''
```javascript
console.log('hello')
```
    '''
    doc = '''
```html
<div>test</div>
```
    '''
```
"""
    blocks = list(Codeblock.iter_from_markdown(markdown))
    assert len(blocks) == 1
    assert blocks[0].lang == "python"
    assert "```javascript" in blocks[0].content
    assert "```html" in blocks[0].content
    # check that entire content is extracted
    assert blocks[0].content.count("```") == 4
    assert blocks[0].content.count("'''") == 4


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
