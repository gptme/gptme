"""Tests for ToolSpec.sensitivity — per-tool approval-gate metadata.

Design: knowledge/technical-designs/2026-09-18-per-tool-approval-gate-managed-service.md
"""

from dataclasses import replace
from importlib import import_module
from types import SimpleNamespace

import pytest

from gptme.tools.base import ToolFunction, ToolSensitivity, ToolSpec


def _make_spec(name: str, **kwargs) -> ToolSpec:
    return ToolSpec(name=name, desc="test", **kwargs)


def test_sensitivity_defaults_to_safe():
    """Adding the field must be non-breaking: nothing prompts by default."""
    assert _make_spec("write").sensitivity == "safe"


@pytest.mark.parametrize("level", ["safe", "moderate", "sensitive", "dangerous"])
def test_sensitivity_accepts_each_level(level: ToolSensitivity):
    assert _make_spec("t", sensitivity=level).sensitivity == level


def test_sensitivity_is_a_dataclass_field():
    """dataclasses.replace() (used by as_function_subtoolspecs) must carry it."""
    spec = _make_spec("t", sensitivity="dangerous")
    assert replace(spec, name="other").sensitivity == "dangerous"


# (module, attribute, expected level) for the built-in spec objects. Asserting
# against the module-level specs rather than get_available_tools() keeps this
# independent of runtime availability (browser needs a backend; computer is
# disabled_by_default).
BUILTIN_SENSITIVITY = {
    "append": ("gptme.tools.save", "tool_append", "moderate"),
    "browser": ("gptme.tools.browser", "tool", "sensitive"),
    "computer": ("gptme.tools.computer", "tool", "dangerous"),
    "hashline_edit": ("gptme.tools.hashline_edit", "tool", "moderate"),
    "ipython": ("gptme.tools.python", "tool", "dangerous"),
    "patch": ("gptme.tools.patch", "tool", "moderate"),
    "patch_many": ("gptme.tools.patch_many", "tool_patch_many", "moderate"),
    "save": ("gptme.tools.save", "tool_save", "moderate"),
    "shell": ("gptme.tools.shell", "tool", "dangerous"),
}


@pytest.mark.parametrize(
    ("module", "attr", "expected"), sorted(BUILTIN_SENSITIVITY.values())
)
def test_builtin_tool_sensitivity(module: str, attr: str, expected: str):
    spec = getattr(import_module(module), attr)
    assert spec.sensitivity == expected


@pytest.mark.parametrize("tool_name", ["read", "rag", "vision", "screenshot"])
def test_read_only_tools_are_not_gated(tool_name: str):
    """Read-only tools must stay at the default 'safe' level."""
    from gptme.tools import get_available_tools

    matches = [t for t in get_available_tools() if t.name == tool_name]
    assert matches, f"tool {tool_name!r} not found"
    assert matches[0].sensitivity == "safe"


def test_function_subtools_inherit_parent_sensitivity():
    """Function-based tools (browser, computer) are invoked as <parent>.<fn>."""
    from gptme.tools.browser import tool as browser_tool

    subs = browser_tool.as_function_subtoolspecs()
    assert subs, "browser tool should expose function subtools"
    assert all(sub.sensitivity == "sensitive" for sub in subs)


@pytest.mark.parametrize("level", ["safe", "moderate", "sensitive", "dangerous"])
def test_function_subtools_inherit_each_level(level: ToolSensitivity):
    def helper() -> str:
        return "ok"

    parent = ToolSpec(
        name="parent",
        desc="test",
        functions=[ToolFunction.from_callable(helper)],
        sensitivity=level,
    )
    (sub,) = parent.as_function_subtoolspecs()
    assert sub.sensitivity == level


def _annotations(**kwargs: object) -> SimpleNamespace:
    base: dict[str, object] = {"readOnlyHint": None, "destructiveHint": None}
    base.update(kwargs)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    ("annotations", "expected"),
    [
        (None, "moderate"),
        (_annotations(readOnlyHint=True), "safe"),
        (_annotations(destructiveHint=True), "dangerous"),
        (_annotations(destructiveHint=False), "moderate"),
        (_annotations(readOnlyHint=True, destructiveHint=True), "safe"),
        # destructiveHint unset mirrors the MCP default (true) -> destructive
        (_annotations(), "dangerous"),
    ],
)
def test_mcp_sensitivity_from_annotations(annotations: object, expected: str):
    from gptme.tools.mcp_adapter import sensitivity_from_annotations

    assert sensitivity_from_annotations(annotations) == expected
