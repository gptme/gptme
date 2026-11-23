"""Every declared hook type is either dispatched or declared unfired (gptme/gptme#5091).

Several ``HookType`` members were declared without any call site, so a hook
registered for them silently never ran. These tests keep the enum honest:
each member must have a dispatch site in ``gptme/`` or an entry in
``UNFIRED_HOOK_TYPES`` (which makes registration warn), never both.

Dispatch sites are found by parsing each module, not by scanning its text: a
mention inside a comment, docstring or string literal must not count as a call
site, or a ``# TODO: trigger_hook(HookType.FOO)`` note would silently satisfy
the guard for a type that never fires.
"""

import ast
import logging
from pathlib import Path

import gptme
from gptme.hooks import (
    UNFIRED_HOOK_TYPES,
    HookType,
    register_hook,
    unregister_hook,
)

_SRC = Path(gptme.__file__).parent
# trigger_hook() fires Message-yielding hooks; TOOL_CONFIRM and ELICIT return
# data and are dispatched by looking the hooks up with get_hooks().
_DISPATCH_FUNCS = frozenset({"trigger_hook", "get_hooks"})
_LOOKUP_DISPATCH_TYPES = frozenset({"TOOL_CONFIRM", "ELICIT"})


def _hook_type_member(node: ast.expr) -> str | None:
    """Return the member name for a ``HookType.X`` expression, else None."""
    if not isinstance(node, ast.Attribute):
        return None
    owner = node.value
    # HookType.X
    if isinstance(owner, ast.Name) and owner.id == "HookType":
        return node.attr
    # Qualified forms, e.g. hooks.HookType.X or gptme.hooks.HookType.X.
    if isinstance(owner, ast.Attribute) and owner.attr == "HookType":
        return node.attr
    return None


def _dispatched_in_source(source: str, filename: str = "<test>") -> set[str]:
    """Hook types passed to a dispatch call in ``source``.

    Only real calls count. Comments and string literals never reach the AST as
    ``ast.Call`` nodes, so they cannot fake a dispatch site. Both the positional
    and the ``hook_type=`` keyword form are accepted. ``get_hooks`` counts only
    for the two data-returning hook types dispatched through direct invocation;
    looking up Message-yielding hooks (e.g. for availability) is not dispatch.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            call_name = func.id
        elif isinstance(func, ast.Attribute):
            call_name = func.attr
        else:
            continue
        if call_name not in _DISPATCH_FUNCS:
            continue
        candidates = list(node.args[:1]) + [
            kw.value for kw in node.keywords if kw.arg == "hook_type"
        ]
        for candidate in candidates:
            member = _hook_type_member(candidate)
            if member is not None and (
                call_name == "trigger_hook" or member in _LOOKUP_DISPATCH_TYPES
            ):
                found.add(member)
    return found


def _dispatched() -> set[str]:
    found: set[str] = set()
    for path in sorted(_SRC.rglob("*.py")):
        if "tests" in path.relative_to(_SRC).parts:
            continue
        found |= _dispatched_in_source(
            path.read_text(encoding="utf-8"), filename=str(path)
        )
    return found


def test_every_hook_type_is_dispatched_or_declared_unfired():
    dispatched = _dispatched()
    unfired = {h.name for h in UNFIRED_HOOK_TYPES}
    silent = {h.name for h in HookType} - dispatched - unfired
    assert not silent, (
        f"Hook types with no dispatch site: {sorted(silent)}. Wire a "
        "trigger_hook() call or add them to UNFIRED_HOOK_TYPES with a reason."
    )
    stale = unfired & dispatched
    assert not stale, (
        f"UNFIRED_HOOK_TYPES lists hook types that are now dispatched: "
        f"{sorted(stale)}. Remove them from the mapping."
    )


def test_dispatch_scan_accepts_real_call_shapes():
    source = """
from gptme.hooks import HookType, get_hooks, trigger_hook

def run(manager):
    yield from trigger_hook(
        HookType.FILE_PATCH_PRE,
        log=None,
    )
    trigger_hook(hook_type=HookType.TOOL_CONFIRM)
    return get_hooks(HookType.ELICIT)
"""
    assert _dispatched_in_source(source) == {
        "FILE_PATCH_PRE",
        "TOOL_CONFIRM",
        "ELICIT",
    }


def test_dispatch_scan_handles_qualified_types_and_ignores_message_lookups():
    source = """
import gptme.hooks

get_hooks(HookType.GENERATION_CHUNK)  # Availability check, not dispatch.
get_hooks(HookType.STEP_POST)  # Merely looking up a hook cannot wire it.
gptme.hooks.trigger_hook(gptme.hooks.HookType.FILE_PATCH_POST)
gptme.hooks.get_hooks(gptme.hooks.HookType.TOOL_CONFIRM)
get_hooks(hook_type=gptme.hooks.HookType.ELICIT)
"""
    assert _dispatched_in_source(source) == {
        "FILE_PATCH_POST",
        "TOOL_CONFIRM",
        "ELICIT",
    }


def test_dispatch_scan_ignores_comments_docstrings_and_strings():
    source = '''
"""Module docstring: trigger_hook(HookType.STEP_POST) is not wired yet."""

# TODO: trigger_hook(HookType.MESSAGE_TRANSFORM) once the signature lands
NOTE = "trigger_hook(HookType.TOOL_TRANSFORM)"


def helper():
    """See get_hooks(HookType.GENERATION_INTERRUPT)."""
    return None
'''
    assert _dispatched_in_source(source) == set()


def test_registering_unfired_hook_type_warns(caplog):
    def noop(manager):
        yield from ()

    with caplog.at_level(logging.WARNING, logger="gptme.hooks.registry"):
        register_hook("test_unfired_warn", HookType.STEP_POST, noop)
    try:
        assert any(
            "test_unfired_warn" in r.getMessage()
            and "step.post" in r.getMessage()
            and "will not run" in r.getMessage()
            for r in caplog.records
        )
    finally:
        unregister_hook("test_unfired_warn", HookType.STEP_POST)


def test_registering_fired_hook_type_does_not_warn(caplog):
    def noop(manager):
        yield from ()

    with caplog.at_level(logging.WARNING, logger="gptme.hooks.registry"):
        register_hook("test_fired_no_warn", HookType.TURN_POST, noop)
    try:
        assert not any("never triggers" in r.getMessage() for r in caplog.records)
    finally:
        unregister_hook("test_fired_no_warn", HookType.TURN_POST)
