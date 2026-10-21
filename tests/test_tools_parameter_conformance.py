"""
Conformance test: every literal string key read from kwargs inside a tool
module must be declared in at least one ToolSpec.parameters in that module.

Catches mismatches before they cause silent LLM failures (the model can't
pass a kwarg it doesn't know exists).

The reverse direction is checked too: a parameter a tool declares must be read
from kwargs, and a tool taking input must declare parameters. Native tool calls
deliver input only as kwargs (``code`` and ``args`` are None), so a tool reading
only ``code`` silently drops every native call (gptme/gptme#4463).

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
    # Declared parameters per executable ToolSpec name
    spec_params: dict[str, set[str]]


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


def _is_kwargs_expr(node: ast.expr) -> bool:
    """Match ``kwargs`` and ``(kwargs or {})``."""
    if isinstance(node, ast.Name):
        return node.id == "kwargs"
    return (
        isinstance(node, ast.BoolOp)
        and isinstance(node.op, ast.Or)
        and isinstance(node.values[0], ast.Name)
        and node.values[0].id == "kwargs"
    )


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
                and _is_kwargs_expr(child.func.value)
                and child.args
                and isinstance(child.args[0], ast.Constant)
                and isinstance(child.args[0].value, str)
            ):
                keys.add(child.args[0].value)
            elif (
                isinstance(child, ast.Subscript)
                and _is_kwargs_expr(child.value)
                and isinstance(child.slice, ast.Constant)
                and isinstance(child.slice.value, str)
            ):
                keys.add(child.slice.value)

    return keys


# Keys intentionally read from explicit-kwargs functions without being
# ToolSpec parameters. Values are per-module short names.
_ALLOWLIST: dict[str, set[str]] = {}

# Declared parameters read in a way the AST scan cannot see. Keyed by ToolSpec
# name; each entry must say how the parameter is actually read.
_UNREAD_ALLOWLIST: dict[str, set[str]] = {
    # base.get_path() reads kwargs["path"]
    "save": {"path"},
    "append": {"path"},
    "patch": {"path"},
    "morph": {"path"},
    # read in a loop: kwargs.get(name) for each declared parameter
    "recall": {"max_chars", "result_id", "start_char"},
    # read through an alias: values = kwargs or {}
    "request_tool_change": {"change_type", "reason", "tool_name", "urgency"},
}

# Executable tools that genuinely take no input, so declare no parameters.
_INPUT_FREE_TOOLS = {
    "complete",
    "restart",
    # Takes input but declares none; fixed by gptme/gptme#4467.
    "memory",
}

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
        spec_params: dict[str, set[str]] = {}
        for spec in specs:
            names = {p.name for p in spec.parameters or []}
            declared |= names
            if spec.execute is not None:
                spec_params[spec.name] = names
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
            ModuleConformance(
                mod_name, short, declared, read_keys, undeclared, spec_params
            )
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


_SPEC_CASES = [(c, name) for c in _CASES for name in sorted(c.spec_params)]


@pytest.mark.parametrize(
    ("case", "spec_name"), _SPEC_CASES, ids=[n for _, n in _SPEC_CASES]
)
def test_declared_parameters_are_read(case: ModuleConformance, spec_name: str) -> None:
    """Every declared parameter must be read from kwargs, or native calls drop it."""
    params = case.spec_params[spec_name]
    if not params:
        assert spec_name in _INPUT_FREE_TOOLS, (
            f"Tool '{spec_name}' declares no parameters, so a native tool call "
            "cannot pass it any input. Declare parameters and read them from "
            "kwargs, or add it to _INPUT_FREE_TOOLS if it takes no input."
        )
        return
    unread = params - case.read_keys - _UNREAD_ALLOWLIST.get(spec_name, set())
    assert not unread, (
        f"Tool '{spec_name}' declares parameters never read from kwargs: "
        f"{sorted(unread)}\n"
        "Native tool calls pass input only as kwargs (code/args are None), so "
        "these values are silently dropped. Read them from kwargs, or add to "
        "_UNREAD_ALLOWLIST with how they are read."
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
