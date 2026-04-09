"""Behavioral scenario: fix-data-mutation."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _get_records_source(ctx) -> str:
    content = ctx.files.get("records.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return content


def check_mutation_tests_pass(ctx):
    """All tests should pass after fixing the mutation bugs."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_tag_records_no_in_place_append(ctx):
    """tag_records should not call .append() on input records' tag lists."""
    content = _get_records_source(ctx)
    # The buggy pattern: appending directly to the input dict's tags field
    buggy_patterns = [
        'record["tags"].append(',
        "record['tags'].append(",
    ]
    return not any(p in content for p in buggy_patterns)


def check_apply_updates_returns_new_dict(ctx):
    """apply_updates should not mutate state via state.update()."""
    content = _get_records_source(ctx)
    # The buggy pattern: state.update() mutates the input dict in-place
    return "state.update(" not in content


def check_test_file_unchanged(ctx):
    """test_records.py should not be modified."""
    content = ctx.files.get("test_records.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return (
        "test_tag_records_does_not_mutate_input" in content
        and "test_apply_updates_does_not_mutate_input" in content
        and "from records import" in content
    )


test: "EvalSpec" = {
    "name": "fix-data-mutation",
    "files": {
        "records.py": """\
\"\"\"Record processing utilities.\"\"\"


def tag_records(records: list, tag: str) -> list:
    \"\"\"Add a tag to each record and return the updated records.\"\"\"
    for record in records:
        record["tags"].append(tag)  # bug: mutates input dicts in-place
    return records


def apply_updates(state: dict, updates: dict) -> dict:
    \"\"\"Apply updates to state and return the new state.\"\"\"
    state.update(updates)  # bug: mutates the input dict
    return state
""",
        "test_records.py": """\
from records import tag_records, apply_updates


def test_tag_records_adds_tag():
    records = [{"name": "a", "tags": ["x"]}, {"name": "b", "tags": ["y"]}]
    result = tag_records(records, "new")
    assert result[0]["tags"] == ["x", "new"]
    assert result[1]["tags"] == ["y", "new"]


def test_tag_records_does_not_mutate_input():
    \"\"\"tag_records must return NEW records — the originals must be unchanged.\"\"\"
    originals = [{"name": "a", "tags": ["x"]}, {"name": "b", "tags": ["y"]}]
    tag_records(originals, "extra")
    assert originals[0]["tags"] == ["x"]   # FAILS with buggy code
    assert originals[1]["tags"] == ["y"]   # FAILS with buggy code


def test_apply_updates_merges():
    state = {"x": 1, "y": 2}
    result = apply_updates(state, {"y": 99, "z": 3})
    assert result == {"x": 1, "y": 99, "z": 3}


def test_apply_updates_does_not_mutate_input():
    \"\"\"apply_updates must not modify the original state dict.\"\"\"
    state = {"x": 1, "y": 2}
    apply_updates(state, {"y": 99})
    assert state["y"] == 2          # FAILS with buggy code — state was mutated
    assert state == {"x": 1, "y": 2}
""",
    },
    "run": "python3 -m pytest test_records.py -v --tb=short 2>&1",
    "prompt": (
        "The tests in `test_records.py` are failing — run them to see the errors. "
        "Both functions in `records.py` have a data mutation bug: they modify their "
        "input arguments instead of working on copies. "
        "Fix `records.py` so that: "
        "(1) `tag_records` returns new record dicts without modifying the originals, and "
        "(2) `apply_updates` returns a new dict without modifying the input `state`. "
        "Do not modify `test_records.py`."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_mutation_tests_pass,
        "tag_records no in-place append": check_tag_records_no_in_place_append,
        "apply_updates returns new dict": check_apply_updates_returns_new_dict,
        "test file unchanged": check_test_file_unchanged,
    },
}
