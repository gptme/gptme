"""
Describes the API of a given Python file such that a LLM can easily get a summary of the API in a codefile (and later an entire project).

NOTE: this is easier to do with ctags
"""

import click
import jedi
from jedi.api.classes import Name


@click.command()
@click.argument("filenames", required=True, nargs=-1)
def main(filenames: list[str]) -> None:
    """Adds prompt and puts API in codeblock."""

    print("Here are API descriptions of the files:")
    for filename in filenames:
        with open(filename) as f:
            code = f.read()
        print(f"\n```{filename}\n{describe_api(code).strip()}\n```")


def describe_api(code: str) -> str:
    """Describes the API of a given Python file in a concise way, such that a LLM can easily get a summary of the API in a codefile."""
    script = jedi.Script(code)
    output = []
    for d in script.get_names(definitions=True):
        output += describe_name(d)
    return "\n".join(output)


def describe_name(name: Name) -> list[str]:
    """Describes a single Name object from Jedi"""
    output = []

    # if name is import, skip

    if "__main__" not in name.full_name or name.type not in ["function", "class"]:
        # skip
        return []

    deftype = "def" if name.type == "function" else "class"
    signatures = name.get_signatures()
    docstring = name.docstring(raw=True)
    docstring = f'\n    """{docstring}"""\n' if docstring else ""

    for s in signatures:
        sigstr = s.to_string() if name.type == "function" else s.name
        output += [f"{deftype} {sigstr}:{f'{docstring}'}"]

    if name.type == "class":
        # Get class methods
        for c in name.defined_names():
            # recurse and indent each line
            output += [
                "\n".join(
                    "    " + line
                    for lines in describe_name(c)
                    for line in lines.split("\n")
                )
            ]
    if output[-1].endswith(":"):
        last_line_indent = output[-1].find("def")
        output += [" " * last_line_indent + "    ..."]

    if name.type == "class":
        # Add a blank line after the class
        output += [""]

    return output


example_code = """
class Test:
    ...

class MyClass:
    def __init__(self, a=1, b: str = "hello") -> None:
        "init"
        pass

    def my_method(self):
        pass

def my_function():
    pass

def another_function():
    pass
"""


def test_example():
    output = describe_api(example_code)
    assert "class MyClass" in output
    assert "def __init__" in output
    assert "def my_function()" in output


def test_simple():
    assert "def f()" in describe_api("def f(): pass")
    assert "class A" in describe_api("class A: pass")
    assert "def __init__" in describe_api("class A:\n    def __init__(self): pass")


if __name__ == "__main__":
    main()
