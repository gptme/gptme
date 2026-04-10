"""Shared helper functions for behavioral eval checkers."""

import ast
from collections.abc import Iterator


def parse_python_source(text: str) -> ast.Module | None:
    """Parse Python source text, returning None on SyntaxError."""
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def get_function_def(module: ast.Module | None, name: str) -> ast.FunctionDef | None:
    """Find a top-level function definition by name."""
    if module is None:
        return None
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def iter_nodes_excluding_nested_scopes(node: ast.AST) -> Iterator[ast.AST]:
    """Yield descendants while skipping nested function/lambda/class scopes."""
    for child in ast.iter_child_nodes(node):
        if isinstance(
            child,
            ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef,
        ):
            continue
        yield child
        yield from iter_nodes_excluding_nested_scopes(child)


def function_raises_value_error(func: ast.FunctionDef | None) -> bool:
    """Check whether a function body contains ``raise ValueError(...)``."""
    if func is None:
        return False
    for node in iter_nodes_excluding_nested_scopes(func):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            target = node.exc.func
            if isinstance(target, ast.Name) and target.id == "ValueError":
                return True
    return False
