from datetime import datetime, timezone

from gptme.util import (
    clean_example,
    epoch_to_age,
    example_to_xml,
    transform_examples_to_chat_directives,
)
from gptme.util.generate_name import generate_name, is_generated_name


def test_generate_name():
    name = generate_name()
    assert is_generated_name(name)


def test_epoch_to_age():
    epoch_today = datetime.now(tz=timezone.utc).timestamp()
    assert epoch_to_age(epoch_today) == "just now"
    epoch_yesterday = epoch_today - 24 * 60 * 60
    assert epoch_to_age(epoch_yesterday) == "yesterday"


def test_transform_examples_to_chat_directives():
    src = """
# Example
> User: Hello
> Bot: Hi
"""
    expected = """
Example

.. chat::

   User: Hello
   Bot: Hi
"""

    assert transform_examples_to_chat_directives(src, strict=True) == expected


def test_transform_examples_to_chat_directives_tricky():
    src = """
> User: hello
> Assistant: lol
> Assistant: lol
> Assistant: lol
""".strip()

    expected = """

.. chat::

   User: hello
   Assistant: lol
   Assistant: lol
   Assistant: lol"""

    assert transform_examples_to_chat_directives(src, strict=True) == expected


def test_example_to_xml_basic():
    x1 = example_to_xml(
        """
> User: Hello
How are you?
> Assistant: Hi
"""
    )

    assert (
        x1
        == """
<user>
Hello
How are you?
</user>
<assistant>
Hi
</assistant>
""".strip()
    )


def test_example_to_xml_preserve_header():
    x1 = example_to_xml(
        """
Header1
-------

> User: Hello

Header2
-------

> System: blah
"""
    )

    assert (
        x1
        == """
Header1
-------

<user>
Hello
</user>

Header2
-------

<system>
blah
</system>
""".strip()
    )


def test_clean_example_strip_system():
    """System blocks are removed when strip_system=True."""
    src = """\
User: Hello
System: some output
Assistant: Hi there"""
    result = clean_example(src, strip_system=True)
    assert "System:" not in result
    assert "some output" not in result
    assert "User: Hello" in result
    assert "Assistant: Hi there" in result


def test_clean_example_strip_system_multiline():
    """Multi-line System blocks (including codeblocks) are fully removed."""
    src = """\
User: run ls
System: output of ls
file1.txt
file2.txt
Assistant: Done"""
    result = clean_example(src, strip_system=True)
    assert "System:" not in result
    assert "file1.txt" not in result
    assert "User: run ls" in result
    assert "Assistant: Done" in result


def test_clean_example_strip_system_with_codeblock():
    """Codeblocks inside System blocks don't desync state."""
    src = """\
User: run command
System: output
```
some code with ```
```
more system output
Assistant: result"""
    result = clean_example(src, strip_system=True)
    assert "System:" not in result
    assert "some code" not in result
    assert "User: run command" in result
    assert "Assistant: result" in result


def test_clean_example_strip_system_preserves_non_system():
    """Non-System roles are preserved."""
    src = """\
User: Hello
Assistant: Hi
User: Bye
Assistant: Goodbye"""
    result = clean_example(src, strip_system=True)
    assert result == src


def test_clean_example_strip_system_blank_line_separator():
    """System block ending with blank line preserves following content."""
    src = """\
User: test

System: output

User: next"""
    result = clean_example(src, strip_system=True)
    assert "System:" not in result
    assert "User: test" in result
    assert "User: next" in result


def test_clean_example_strip_system_consecutive():
    """Consecutive System blocks are both fully stripped."""
    src = """\
User: run ls
System: output of ls
file1.txt
System: another system block
extra output
Assistant: Done"""
    result = clean_example(src, strip_system=True)
    assert "System:" not in result
    assert "file1.txt" not in result
    assert "another system block" not in result
    assert "extra output" not in result
    assert "User: run ls" in result
    assert "Assistant: Done" in result
