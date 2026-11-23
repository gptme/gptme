"""Every declared hook type is either dispatched or declared unfired (gptme/gptme#5091).

Several ``HookType`` members were declared without any call site, so a hook
registered for them silently never ran. These tests keep the enum honest:
each member must have a dispatch site in ``gptme/`` or an entry in
``UNFIRED_HOOK_TYPES`` (which makes registration warn), never both.
"""

import logging
import re
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
_DISPATCH_RE = re.compile(r"(?:trigger_hook|get_hooks)\(\s*HookType\.([A-Z_]+)")


def _dispatched() -> set[str]:
    found: set[str] = set()
    for path in sorted(_SRC.rglob("*.py")):
        if "tests" in path.relative_to(_SRC).parts:
            continue
        found |= set(_DISPATCH_RE.findall(path.read_text()))
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
