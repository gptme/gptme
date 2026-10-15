"""
Conformance test: every literal string key read from kwargs inside a tool
module must be declared in at least one ToolSpec.parameters in that module.

Catches mismatches before they cause silent LLM failures (the model can't
pass a kwarg it doesn't know exists).

Scope: only functions where ``kwargs`` is an explicit positional parameter
(i.e. ``def execute(code, args, kwargs)``), not functions that use Python's
``**kwargs`` variadic form (those are hook callbacks or internal helpers with
a different kwargs contract).
"""

import ast
import importlib
import pkgutil
from pathlib import Path
from typing import NamedTuple

import pytest

import gptme.tools
from gptme.tools.base import ToolSpec


class ModuleConformance(NamedTuple):
    module_name: str
    module_short: str
    declared: set[str]
    read_keys: set[str]
    undeclared: set[str]


def _get_tool_module_names() -> list[str]:
    tools_pkg = Path(gptme.tools.__file__).parent
    return [
        f"gptme.tools.{info.name}"
        for info in pkgutil.iter_modules([str(tools_pkg)])
        if not info.name.startswith("_")
    ]


def _has_explicit_kwargs_param(
    funcdef: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Return True if the function has an explicit ``kwargs`` positional parameter.

    Excludes ``**kwargs`` (variadic keyword arguments) which belong to hook
    callbacks or internal helpers with a different kwargs contract.
    """
    return any(arg.arg == "kwargs" for arg in funcdef.args.args)


def _extract_kwargs_keys_from_functions(source: str) -> set[str]:
    """Return kwargs keys only from functions with an explicit ``kwargs`` parameter."""
    keys: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return keys

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _has_explicit_kwargs_param(node):
            continue
        # Scan this function's body for kwargs reads
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "get"
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "kwargs"
                and child.args
                and isinstance(child.args[0], ast.Constant)
                and isinstance(child.args[0].value, str)
            ):
                keys.add(child.args[0].value)
            elif (
                isinstance(child, ast.Subscript)
                and isinstance(child.value, ast.Name)
                and child.value.id == "kwargs"
                and isinstance(child.slice, ast.Constant)
                and isinstance(child.slice.value, str)
            ):
                keys.add(child.slice.value)

    return keys


# Keys intentionally read from explicit-kwargs functions without being
# ToolSpec parameters. Values are per-module short names.
_ALLOWLIST: dict[str, set[str]] = {}

# Tool modules that could not be imported here because an optional dependency
# is missing. Kept as explicit cases so a skipped module shows as a visible
# skip rather than silently reducing the number of passing assertions.
_SKIPPED_MODULES: list[tuple[str, str]] = []


def _build_conformance_cases() -> list[ModuleConformance]:
    cases = []
    for mod_name in _get_tool_module_names():
        try:
            mod = importlib.import_module(mod_name)
        except ImportError as exc:
            # Missing optional dependency: the module cannot be inspected in
            # this environment. Any other exception is a real defect and must
            # fail loudly instead of removing the module from the suite.
            _SKIPPED_MODULES.append((mod_name, str(exc)))
            continue
        specs = [
            obj
            for attr in dir(mod)
            for obj in [getattr(mod, attr)]
            if isinstance(obj, ToolSpec)
        ]
        if not specs:
            continue
        declared: set[str] = set()
        for spec in specs:
            for p in spec.parameters or []:
                declared.add(p.name)
        try:
            import inspect

            src_file = inspect.getfile(mod)
            source = Path(src_file).read_text()
        except (TypeError, OSError):
            continue
        read_keys = _extract_kwargs_keys_from_functions(source)
        short = mod_name.split(".")[-1]
        allowlisted = _ALLOWLIST.get(short, set())
        undeclared = read_keys - declared - allowlisted
        cases.append(
            ModuleConformance(mod_name, short, declared, read_keys, undeclared)
        )
    return cases


_CASES = _build_conformance_cases()


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c.module_short)
def test_kwarg_keys_declared(case: ModuleConformance) -> None:
    """Every kwargs key read in a tool module must be declared in a ToolSpec parameter."""
    assert not case.undeclared, (
        f"Module '{case.module_short}' reads kwargs keys not declared in any ToolSpec: "
        f"{sorted(case.undeclared)}\n"
        f"Declared parameters: {sorted(case.declared)}\n"
        "Either add to spec.parameters or add to _ALLOWLIST if intentional."
    )


def test_optional_dependency_skips_are_reported() -> None:
    """Surface tool modules skipped because an optional dependency is missing.

    Silent skips shrink the conformance suite's coverage without any signal;
    failing the import loudly (above) covers unexpected errors, and this test
    names the expected imports that could not be inspected in this environment.
    """
    if _SKIPPED_MODULES:
        pytest.skip(
            "tool modules not inspected (missing optional dependency): "
            + ", ".join(f"{name} ({reason})" for name, reason in _SKIPPED_MODULES)
        )
