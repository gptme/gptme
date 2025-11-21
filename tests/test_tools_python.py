from typing import Literal, TypeAlias

from gptme.tools.base import callable_signature
from gptme.tools.python import execute_python


def run(code):
    return next(execute_python(code, [], None)).content


def run_with_kwargs(code):
    return next(execute_python(None, None, {"code": code})).content


def test_execute_python():
    assert "2\n" in run("print(1 + 1)")
    assert "2\n" in run("a = 2\nprint(a)")
    assert "2\n" in run("a = 1\na += 1\nprint(a)")

    # test that vars are preserved between executions
    assert run("a = 2")
    assert "2\n" in run("print(a)")


def test_execute_python_with_kwargs():
    assert "2\n" in run_with_kwargs("print(1 + 1)")


TestType: TypeAlias = Literal["a", "b"]


def test_callable_signature():
    def f():
        pass

    assert callable_signature(f) == "f()"

    def g(a: int) -> str:
        return str(a)

    assert callable_signature(g) == "g(a: int) -> str"

    def h(a: TestType) -> str:
        return str(a)

    assert callable_signature(h) == 'h(a: Literal["a", "b"]) -> str'

    # Test generic types

    def i(a: list[int]) -> str:
        return str(a)

    assert callable_signature(i) == "i(a: list[int]) -> str"

    def j(a: list[int] | None) -> str:
        return str(a)

    assert callable_signature(j) == "j(a: Union[list[int], None]) -> str"

    def k(a: dict[str, int]) -> str:
        return str(a)

    assert callable_signature(k) == "k(a: dict[str, int]) -> str"

    # Test union types with | syntax
    def m(a: int | str) -> str:
        return str(a)

    assert callable_signature(m) == "m(a: Union[int, str]) -> str"
