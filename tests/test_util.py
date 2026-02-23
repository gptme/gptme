from datetime import datetime, timezone

from gptme.util import (
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
