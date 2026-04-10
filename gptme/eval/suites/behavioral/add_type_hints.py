"""Behavioral scenario: add-type-hints."""

import ast
from typing import TYPE_CHECKING

from ._common import parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_typehints_mypy_passes(ctx):
    """mypy should pass with no errors after adding type hints."""
    output = ctx.stdout.lower()
    return ctx.exit_code == 0 and "success" in output


def check_typehints_function_params_annotated(ctx):
    """All function parameters should have type annotations."""
    content = ctx.files.get("datastore.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef):
            # Skip dunder methods with common self-only signatures
            if node.name.startswith("__") and len(node.args.args) <= 1:
                continue
            params = node.args.args
            for param in params:
                if param.arg == "self":
                    continue
                if param.annotation is None:
                    return False
    return True


def check_typehints_function_returns_annotated(ctx):
    """All non-dunder functions should have return type annotations."""
    content = ctx.files.get("datastore.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name.startswith("__") and node.name.endswith("__"):
                continue
            if node.returns is None:
                return False
    return True


def check_typehints_uses_generic_collection(ctx):
    """Code should use generic types like dict[str, int] instead of bare dict."""
    content = ctx.files.get("datastore.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    module = parse_python_source(content)
    if module is None:
        return False
    # Look for subscripted annotations (e.g. dict[str, ...], list[int], Optional[...])
    for node in ast.walk(module):
        if isinstance(node, ast.Subscript):
            # Check if the subscript is used in a type annotation context
            if isinstance(node.value, ast.Name) and node.value.id in (
                "dict",
                "list",
                "set",
                "tuple",
                "Optional",
                "Union",
            ):
                return True
    return False


def check_typehints_class_attribute_annotated(ctx):
    """The DataStore class should have annotated instance attributes."""
    content = ctx.files.get("datastore.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.ClassDef) and node.name == "DataStore":
            # Look for annotated assignments in __init__ (self.x: int = ...)
            has_annotated = False
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.AnnAssign)
                    and isinstance(child.target, ast.Attribute)
                    and isinstance(child.target.value, ast.Name)
                    and child.target.value.id == "self"
                ):
                    has_annotated = True
                    break
            return has_annotated
    return False


test: "EvalSpec" = {
    "name": "add-type-hints",
    "files": {
        "datastore.py": """\
class DataStore:
    def __init__(self):
        self.data = {}
        self.metadata = {}

    def set(self, key, value):
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)

    def delete(self, key):
        if key in self.data:
            del self.data[key]
            return True
        return False

    def keys(self):
        return list(self.data.keys())

    def items(self):
        return list(self.data.items())

    def update_metadata(self, key, info):
        self.metadata[key] = info

    def get_metadata(self, key):
        return self.metadata.get(key)

    def count(self):
        return len(self.data)

    def search(self, prefix):
        return [k for k in self.data if k.startswith(prefix)]


def merge_stores(primary, secondary):
    result = DataStore()
    for key, value in primary.items():
        result.set(key, value)
    for key, value in secondary.items():
        if key not in primary.data:
            result.set(key, value)
    return result


def filter_by_value(store, predicate):
    result = DataStore()
    for key, value in store.items():
        if predicate(value):
            result.set(key, value)
    return result
""",
        "mypy.ini": """\
[mypy]
strict = true
""",
        "test_datastore.py": """\
from datastore import DataStore, merge_stores, filter_by_value


def test_basic_operations():
    store = DataStore()
    store.set("a", 1)
    assert store.get("a") == 1
    assert store.get("missing", 42) == 42
    assert store.count() == 1


def test_delete():
    store = DataStore()
    store.set("x", 10)
    assert store.delete("x") is True
    assert store.delete("x") is False
    assert store.count() == 0


def test_search():
    store = DataStore()
    store.set("foo", 1)
    store.set("bar", 2)
    store.set("baz", 3)
    results = store.search("ba")
    assert sorted(results) == ["bar", "baz"]


def test_merge():
    a = DataStore()
    a.set("x", 1)
    a.set("y", 2)
    b = DataStore()
    b.set("y", 20)
    b.set("z", 3)
    merged = merge_stores(a, b)
    assert merged.get("x") == 1
    assert merged.get("y") == 2
    assert merged.get("z") == 3


def test_filter():
    store = DataStore()
    store.set("a", 10)
    store.set("b", 5)
    store.set("c", 20)
    result = filter_by_value(store, lambda v: v > 8)
    assert sorted(result.keys()) == ["a", "c"]


def test_metadata():
    store = DataStore()
    store.set("key1", "value1")
    store.update_metadata("key1", {"source": "test"})
    assert store.get_metadata("key1") == {"source": "test"}
""",
    },
    "run": "python3 -m pytest test_datastore.py -v --tb=short 2>&1 && python3 -m mypy datastore.py --config-file mypy.ini 2>&1",
    "prompt": (
        "The file `datastore.py` contains a `DataStore` class and two helper "
        "functions (`merge_stores`, `filter_by_value`), but has **zero type "
        "annotations**.  Add proper type hints to everything:\\n\\n"
        "1. Add type annotations to all method parameters and return types "
        "in the `DataStore` class (use generic types like `dict[str, int]`, "
        "`list[str]`, `Optional[int]`, etc. — NOT bare `dict`, `list`, or "
        "`Optional`).\\n"
        "2. Annotate the instance attributes `self.data` and `self.metadata` "
        "in `__init__` (use `self.data: dict[str, Any] = {}`).\\n"
        "3. Add type hints to `merge_stores` and `filter_by_value` "
        "functions including their parameters and return types.\\n"
        "4. All existing tests in `test_datastore.py` must still pass.\\n"
        "5. `python3 -m mypy datastore.py --config-file mypy.ini` must pass "
        "with `strict = true` mode (no errors).\\n\\n"
        "Use `from typing import Any, Optional` if needed. Import them at "
        "the top of the file."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "mypy passes": check_typehints_mypy_passes,
        "function params annotated": check_typehints_function_params_annotated,
        "function returns annotated": check_typehints_function_returns_annotated,
        "uses generic collection types": check_typehints_uses_generic_collection,
        "class attributes annotated": check_typehints_class_attribute_annotated,
    },
}
