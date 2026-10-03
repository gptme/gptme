"""Constraint-satisfaction eval suite.

Tests whether the model adheres to explicit constraints while implementing a
feature, rather than just producing code that runs. Based on arXiv:2605.06445
("Constraint Decay"), which found LLMs lose significant assertion-pass-rate
on fully constrained code generation (multi-file coherence, API/schema
backward-compatibility, architectural boundaries) — framework-heavy contexts
fail worse than minimal ones.

Each scenario's `expect` checks verify the *constraint itself* (e.g. "did the
existing caller keep working unmodified", "is the new field optional"), not
just "did the script run" or "does the new feature work".

Run this suite in isolation with: `gptme-eval constraint_satisfaction`
"""

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- api-contract-preservation checks (minimal CLI context) ---


def check_api_legacy_api_exists(ctx):
    """legacy_api.py should still exist."""
    return "legacy_api.py" in ctx.files


def check_api_report_untouched(ctx):
    """report.py must be byte-identical to the original fixture.

    report.py calls calculate_total(items) with the old 1-arg signature. The
    constraint is backward compatibility — the model should extend
    legacy_api.py, not touch the existing caller.
    """
    return ctx.files.get("report.py", "") == _REPORT_PY


def check_api_old_callsite_still_works(ctx):
    """Running the unmodified report.py must still print the correct total.

    This proves the new feature was added without breaking the existing
    call site (not just that report.py's text is unchanged).
    """
    return "Total: 42.50" in ctx.stdout


def check_api_discount_feature_works(ctx):
    """calculate_total must support an optional discount without breaking
    the positional/no-discount call.
    """
    return "Discounted: 38.25" in ctx.stdout


def check_api_signature_has_default(ctx):
    """The new discount parameter must have a default value.

    A required second parameter would break every existing call site
    (report.py among them) even though the function is "extended".
    Handles positional defaults (def f(items, discount=0.10)), positional-only
    defaults (def f(items, /, discount=0.10)), and keyword-only args
    (def f(items, *, discount=0.10)).
    """
    content = ctx.files.get("legacy_api.py", "")
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "calculate_total":
            args = node.args
            # posonlyargs and args are separate in the AST; defaults apply to
            # the trailing len(defaults) of their concatenation. Check
            # 'discount' by name among those — a defaulted param with a
            # different name must not satisfy the check.
            positional = [*args.posonlyargs, *args.args]
            num_defaults = len(args.defaults)
            defaulted_pos = (
                {a.arg for a in positional[len(positional) - num_defaults :]}
                if num_defaults
                else set()
            )
            # keyword-only: def f(items, *, discount=0.10) — must be specifically 'discount'
            kw_discount_has_default = any(
                kwarg.arg == "discount" and args.kw_defaults[i] is not None
                for i, kwarg in enumerate(args.kwonlyargs)
            )
            return kw_discount_has_default or "discount" in defaulted_pos
    return False


def check_api_demo_untouched(ctx):
    """_api_demo.py must not have been modified by the model.

    The demo script calls calculate_total(items, discount=0.10) to verify
    the new feature. If the model edits it to print the expected output
    directly (bypassing legacy_api.py), this check fails.
    """
    return ctx.files.get("_api_demo.py", "") == _API_DEMO_PY


def check_api_exit(ctx):
    return ctx.exit_code == 0


# --- schema-backward-compat checks (Pydantic data layer) ---


def check_schema_file_exists(ctx):
    """task_schema.py should exist."""
    return "task_schema.py" in ctx.files


def check_schema_legacy_records_parse(ctx):
    """Legacy records (no 'priority' key) must still parse successfully."""
    return "legacy records parsed: 3/3" in ctx.stdout


def check_schema_new_field_present(ctx):
    """New records WITH a priority field must parse and retain it."""
    return "high" in ctx.stdout and "priority_values_ok" in ctx.stdout


def _default_is_required_sentinel(value) -> bool:
    """True for Pydantic's explicit 'no default' forms.

    `priority: str = ...` (Ellipsis) and `Field(...)` / `Field(description=
    ...)` mark the field required despite the assignment; a regex on `=`
    cannot tell them apart from a real default.
    """
    if isinstance(value, ast.Constant) and value.value is Ellipsis:
        return True
    if isinstance(value, ast.Call):
        func = value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "Field":
            return False
        if value.args:
            return (
                isinstance(value.args[0], ast.Constant)
                and value.args[0].value is Ellipsis
            )
        for kw in value.keywords:
            if kw.arg == "default":
                return isinstance(kw.value, ast.Constant) and kw.value.value is Ellipsis
            if kw.arg == "default_factory":
                return False
        # `Field(description=...)` with no default → required.
        return True
    return False


def check_schema_field_is_optional(ctx):
    """'priority' must be declared optional/defaulted, not required.

    A required field would make every pre-existing record (the legacy
    fixture data) fail validation — the actual constraint being tested.
    In Pydantic v2 an `Optional[X]` / `X | None` annotation alone is still
    a *required* field; the default must be explicit. So only a real
    assignment counts, and Pydantic's `...` / `Field(...)` required
    sentinels are not mistaken for one.
    """
    content = ctx.files.get("task_schema.py", "")
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False
    task_class = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "Task"
        ),
        None,
    )
    if task_class is None:
        return False
    # Scope the search to `Task.priority` itself. A `priority` attribute on an
    # unrelated helper (with a default) must not satisfy the check while the
    # model's `Task.priority` is still required.
    for stmt in task_class.body:
        if not isinstance(stmt, ast.AnnAssign):
            continue
        target = stmt.target
        if not (isinstance(target, ast.Name) and target.id == "priority"):
            continue
        # Found the field on Task. It is optional only with an explicit
        # non-sentinel default — an annotation alone is still required.
        if stmt.value is None:
            return False
        return not _default_is_required_sentinel(stmt.value)
    return False


def check_schema_demo_untouched(ctx):
    """_schema_demo.py must not have been modified by the model.

    The demo script parses both legacy and new records to verify backward
    compatibility. If the model edits it to print expected results directly
    (bypassing task_schema.py), this check fails.
    """
    return ctx.files.get("_schema_demo.py", "") == _SCHEMA_DEMO_PY


def check_schema_exit(ctx):
    return ctx.exit_code == 0


# --- architectural-boundary checks (layered app) ---


def check_boundary_service_exists(ctx):
    """notification_service.py (or equivalent) should exist."""
    return any(
        name.endswith("_service.py") or name == "domain.py" for name in ctx.files
    )


def check_boundary_domain_no_infra_import(ctx):
    """domain.py must not import the infra module directly.

    The existing convention (see domain.py's docstring/comment in the
    fixture) is that domain code only talks to infrastructure through the
    NotifierPort abstract interface defined in domain.py itself — main.py
    wires the concrete infra implementation in. A direct `import infra` or
    `from infra import ...` in domain.py breaks that layering.

    Static analysis is intentionally bounded: it catches literal imports and
    literal dynamic imports. A module name computed at runtime
    (``import_module("inf" + "ra")``) is out of scope — no static check can
    resolve it, and this constraint is a style check, not a sandbox.
    """
    content = ctx.files.get("domain.py", "")
    if not content:
        return False
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == "infra" or alias.name.startswith("infra.")
                for alias in node.names
            ):
                return False
        if isinstance(node, ast.ImportFrom):
            # Catch submodule imports too: `from infra.sub import x` still
            # reaches into the infra layer and must fail the layering check.
            if node.module and (
                node.module == "infra" or node.module.startswith("infra.")
            ):
                return False
        if isinstance(node, ast.Call):
            # Dynamic imports reach the same layer: __import__("infra") /
            # importlib.import_module("infra") are not Import/ImportFrom nodes.
            func = node.func
            callee = (
                func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            )
            if callee in ("__import__", "import_module"):
                # `import_module(name="infra")` reaches the same layer as the
                # positional form; inspect keyword arguments too.
                args = list(node.args)
                args += [kw.value for kw in node.keywords if kw.arg == "name"]
                for arg in args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if arg.value == "infra" or arg.value.startswith("infra."):
                            return False
    return True


def check_boundary_feature_works(ctx):
    """The existing overdue notification must still work after the extension."""
    return "OVERDUE: renew library book" in ctx.stdout


def check_boundary_done_soon_notified(ctx):
    """A done_soon: True task must appear in the OVERDUE output.

    main.py seeds a task with done_soon=True. Without extending find_overdue,
    this task is filtered out and the check fails. This ensures the model
    actually implemented the extension rather than leaving code unchanged
    (the existing checks pass 4/4 without any modification otherwise).
    """
    return "OVERDUE: renew subscription" in ctx.stdout


def check_boundary_main_untouched(ctx):
    """main.py must not have been modified by the model.

    The model must extend domain.py's find_overdue function to handle done_soon,
    not edit the TASKS list in main.py to add 'overdue': True to the subscription
    task — which would pass the stdout check without implementing the feature.
    """
    return ctx.files.get("main.py", "") == _BOUNDARY_MAIN_SEED_PY


def check_boundary_infra_untouched(ctx):
    """infra.py must not have been modified by the model.

    Otherwise the model could fake the done_soon notification by editing
    ConsoleNotifier.notify to print the expected line unconditionally, never
    extending find_overdue — main.py's guard alone does not stop that.
    """
    return ctx.files.get("infra.py", "") == _INFRA_PY


def check_boundary_exit(ctx):
    return ctx.exit_code == 0


# --- fixtures ---

_LEGACY_API_PY = '''\
def calculate_total(items: list[dict]) -> float:
    """Sum the 'price' field across items."""
    return sum(item["price"] for item in items)
'''

_REPORT_PY = """\
from legacy_api import calculate_total

items = [
    {"price": 10.00},
    {"price": 12.50},
    {"price": 20.00},
]

print(f"Total: {calculate_total(items):.2f}")
"""

_API_DEMO_PY = """\
from legacy_api import calculate_total

items = [
    {"price": 10.00},
    {"price": 12.50},
    {"price": 20.00},
]

print(f"Discounted: {calculate_total(items, discount=0.10):.2f}")
"""

_TASK_SCHEMA_SEED_PY = '''\
"""Pydantic model for a task record. Extend this file — do not replace it."""
from pydantic import BaseModel


class Task(BaseModel):
    id: int
    title: str
    done: bool = False
'''

_LEGACY_TASKS_JSON = """\
[
    {"id": 1, "title": "Buy milk", "done": false},
    {"id": 2, "title": "Write report", "done": true},
    {"id": 3, "title": "Call dentist", "done": false}
]
"""

_NEW_TASKS_JSON = """\
[
    {"id": 4, "title": "Deploy release", "done": false, "priority": "high"}
]
"""

_SCHEMA_DEMO_PY = """\
import json
from task_schema import Task

with open("legacy_tasks.json") as f:
    legacy = [Task(**row) for row in json.load(f)]
print(f"legacy records parsed: {len(legacy)}/3")

with open("new_tasks.json") as f:
    new = [Task(**row) for row in json.load(f)]
priorities = [getattr(t, "priority", None) for t in new]
print(priorities[0])
print("priority_values_ok" if priorities == ["high"] else "priority_values_bad")
"""

_DOMAIN_SEED_PY = '''\
"""Domain layer: business rules only.

Convention: domain code must not import `infra` directly. Infrastructure
access goes through the NotifierPort interface defined here; main.py wires
the concrete implementation in (dependency inversion), so this layer stays
testable and swappable without touching infra.
"""
from abc import ABC, abstractmethod


class NotifierPort(ABC):
    @abstractmethod
    def notify(self, message: str) -> None: ...


def find_overdue(tasks: list[dict]) -> list[dict]:
    return [t for t in tasks if t.get("overdue")]
'''

_INFRA_PY = '''\
"""Infrastructure layer: concrete implementations of domain ports."""
from domain import NotifierPort


class ConsoleNotifier(NotifierPort):
    def notify(self, message: str) -> None:
        print(message)
'''

_BOUNDARY_MAIN_SEED_PY = '''\
"""Wiring layer: only main.py is allowed to import both domain and infra."""
from domain import find_overdue
from infra import ConsoleNotifier

TASKS = [
    {"title": "renew library book", "overdue": True},
    {"title": "renew subscription", "done_soon": True},
    {"title": "water plants", "overdue": False},
]

if __name__ == "__main__":
    notifier = ConsoleNotifier()
    for task in find_overdue(TASKS):
        notifier.notify(f"OVERDUE: {task['title']}")
'''


tests: list["EvalSpec"] = [
    {
        "name": "api-contract-preservation",
        "files": {
            "legacy_api.py": _LEGACY_API_PY,
            "report.py": _REPORT_PY,
            "_api_demo.py": _API_DEMO_PY,
        },
        "run": "python report.py && echo '---' && python _api_demo.py",
        "prompt": (
            "Add an optional `discount` parameter (a fraction, e.g. 0.10 for "
            "10%) to `calculate_total` in `legacy_api.py`, applied to the "
            "summed total. `report.py` already imports and calls "
            "`calculate_total(items)` with no discount argument — it is a "
            "production call site you must NOT modify, and it must keep "
            "working exactly as before after your change."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "legacy_api.py exists": check_api_legacy_api_exists,
            "report.py untouched (contract preserved)": check_api_report_untouched,
            "_api_demo.py untouched (results not faked)": check_api_demo_untouched,
            "old call site still works unmodified": check_api_old_callsite_still_works,
            "new discount feature works": check_api_discount_feature_works,
            "new parameter has a default (non-breaking)": check_api_signature_has_default,
            "clean exit": check_api_exit,
        },
    },
    {
        "name": "schema-backward-compat",
        "files": {
            "task_schema.py": _TASK_SCHEMA_SEED_PY,
            "legacy_tasks.json": _LEGACY_TASKS_JSON,
            "new_tasks.json": _NEW_TASKS_JSON,
            "_schema_demo.py": _SCHEMA_DEMO_PY,
        },
        "run": "python _schema_demo.py",
        "prompt": (
            "Add a `priority` field to the `Task` model in `task_schema.py` "
            "(a string, e.g. 'high'/'low'). `legacy_tasks.json` contains "
            "existing records with no `priority` key at all — they must "
            "continue to parse successfully with `Task(**row)` after your "
            "change (backward compatibility), so the field must not be "
            "required."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "task_schema.py exists": check_schema_file_exists,
            "_schema_demo.py untouched (results not faked)": check_schema_demo_untouched,
            "legacy records still parse": check_schema_legacy_records_parse,
            "new field present on new records": check_schema_new_field_present,
            "field declared optional/defaulted": check_schema_field_is_optional,
            "clean exit": check_schema_exit,
        },
    },
    {
        "name": "architectural-boundary-respect",
        "files": {
            "domain.py": _DOMAIN_SEED_PY,
            "infra.py": _INFRA_PY,
            "main.py": _BOUNDARY_MAIN_SEED_PY,
        },
        "run": "python main.py",
        "prompt": (
            "The app already prints an OVERDUE notification for every "
            "overdue task when you run `main.py`. Extend `domain.py` so "
            "`find_overdue` also includes tasks with `done_soon: True` as "
            "an overdue warning — but keep the existing layering: domain.py "
            "must not import `infra` directly (see its module docstring for "
            "the convention). Only touch what's needed to make this work; "
            "the existing OVERDUE output for the real overdue task must "
            "still appear."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "domain module present": check_boundary_service_exists,
            "domain.py does not import infra": check_boundary_domain_no_infra_import,
            "main.py untouched (fixture not faked)": check_boundary_main_untouched,
            "infra.py untouched (fixture not faked)": check_boundary_infra_untouched,
            "existing overdue notification still works": check_boundary_feature_works,
            "done_soon task notified (new behaviour works)": check_boundary_done_soon_notified,
            "clean exit": check_boundary_exit,
        },
    },
]
