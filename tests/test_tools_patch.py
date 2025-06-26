from gptme.tools.patch import Patch, apply, execute_patch

example_patch = """
<<<<<<< ORIGINAL
original lines
=======
modified lines
>>>>>>> UPDATED
"""


def test_execute_patch(temp_file):
    with temp_file("""original lines""") as f:
        result = next(execute_patch(example_patch, [f], None)).content

        assert "successfully" in result

        with open(f, encoding="utf-8") as f:
            assert f.read() == """modified lines"""


def test_apply_simple():
    codeblock = example_patch
    content = """original lines"""
    result = apply(codeblock, content)
    assert result == """modified lines"""


def test_apply_function():
    content = """
def hello():
    print("hello")

if __name__ == "__main__":
    hello()
"""

    codeblock = """
<<<<<<< ORIGINAL
def hello():
    print("hello")
=======
def hello(name="world"):
    print(f"hello {name}")
>>>>>>> UPDATED
"""

    result = apply(codeblock, content)
    assert result.startswith(
        """
def hello(name="world"):
    print(f"hello {name}")
"""
    )


def test_apply_clear_file():
    content = "test"
    codeblock = """
<<<<<<< ORIGINAL
test
=======
>>>>>>> UPDATED
    """
    result = apply(codeblock, content)
    assert result == ""


def test_apply_rm_function():
    # only remove code in patch
    content = """
def hello():
    print("hello")

if __name__ == "__main__":
    hello()
"""

    codeblock = """
<<<<<<< ORIGINAL
def hello():
    print("hello")
=======
>>>>>>> UPDATED
"""
    result = apply(codeblock, content)
    assert result.count("\n") == content.count("\n") - 1


def test_apply_empty_lines():
    # a test where it replaces a empty line with 3 empty lines
    # checks that whitespace is preserved
    content = """
def hello():
    print("hello")

if __name__ == "__main__":
    hello()
"""
    codeblock = """
<<<<<<< ORIGINAL



=======




>>>>>>> UPDATED
"""
    result = apply(codeblock, content)
    assert "\n\n\n" in result


def test_apply_multiple():
    # tests multiple patches in a single codeblock, with placeholders in patches
    # checks that whitespace is preserved
    content = """
def hello():
    print("hello")

if __name__ == "__main__":
    hello()
"""
    codeblock = """
<<<<<<< ORIGINAL
def hello():
=======
def hello_world():
>>>>>>> UPDATED

<<<<<<< ORIGINAL
    hello()
=======
    hello_world()
>>>>>>> UPDATED
"""
    result = apply(codeblock, content)
    assert "    hello_world()" in result


def test_apply_with_placeholders():
    # tests multiple patches in a single codeblock, with placeholders in patches
    # checks that whitespace is preserved
    content = """
def hello():
    print("hello")
"""
    codeblock = """
<<<<<<< ORIGINAL
def hello():
    # ...
=======
def hello_world():
    # ...
>>>>>>> UPDATED
"""
    result = apply(codeblock, content)
    assert "hello_world()" in result


def test_patch_minimal():
    p = Patch(
        """1
2
3
""",
        """1
0
3
""",
    )
    assert (
        p.diff_minimal()
        == """ 1
-2
+0
 3"""
    )
    assert p.diff_minimal(strip_context=True) == "-2\n+0"


def test_apply_with_extra_divider_fails():
    """Test that extra ======= markers before >>>>>>> UPDATED cause a clear error."""
    # This is the problematic case where Claude adds an extra =======
    codeblock = """
<<<<<<< ORIGINAL
    print("Hello world")
=======
    name = input("What is your name? ")
    print(f"Hello {name}")
=======
>>>>>>> UPDATED
"""

    # Should raise a clear error about the extra divider
    try:
        list(Patch.from_codeblock(codeblock.strip()))
        raise AssertionError("Expected ValueError for extra ======= marker")
    except ValueError as e:
        assert "extra ======= marker found" in str(e)
        assert "Use only one =======" in str(e)


# TODO: write a test for this
example_patch_with_nested_codeblock = '''
<<<<<<< ORIGINAL
        return_prompt = """Thank you for doing the task, please reply with a JSON codeblock on the format:

```json
{
    result: 'A description of the task result/outcome',
    status: 'success' | 'failure',
}
```"""
=======
        return_prompt = """Thank you for doing the task, please reply with a JSON codeblock on the format:

```json
{
    "result": "A description of the task result/outcome",
    "status": "success"
}
```"""
>>>>>>> UPDATED
```
'''


def test_apply_with_nested_codeblock():
    """Test that patches containing nested codeblocks (like ```json) work correctly."""
    # Parse the example patch to extract original and expected content
    patches = list(Patch.from_codeblock(example_patch_with_nested_codeblock.strip()))
    patch = patches[0]

    content = patch.original
    expected = patch.updated

    result = apply(example_patch_with_nested_codeblock.strip(), content)
    assert result == expected
