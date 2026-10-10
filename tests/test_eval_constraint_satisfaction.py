"""Tests for the constraint-satisfaction eval suite's check helpers.

Focused on `check_schema_field_is_optional`, which must distinguish a
backward-compatible (optional/defaulted) `Task.priority` from a required one —
including when the field is inherited from a base class in the same file.
"""

from gptme.eval.suites import constraint_satisfaction as suite
from gptme.eval.types import ResultContext


def _ctx(schema_src: str) -> ResultContext:
    return ResultContext(
        files={"task_schema.py": schema_src},
        stdout="",
        stderr="",
        exit_code=0,
    )


def test_optional_with_inline_default():
    src = (
        "from pydantic import BaseModel\n"
        "class Task(BaseModel):\n"
        "    id: int\n"
        '    priority: str = "medium"\n'
    )
    assert suite.check_schema_field_is_optional(_ctx(src))


def test_optional_none_default():
    src = (
        "from pydantic import BaseModel\n"
        "class Task(BaseModel):\n"
        "    id: int\n"
        "    priority: str | None = None\n"
    )
    assert suite.check_schema_field_is_optional(_ctx(src))


def test_required_annotation_only_fails():
    src = (
        "from pydantic import BaseModel\n"
        "class Task(BaseModel):\n"
        "    id: int\n"
        "    priority: str\n"
    )
    assert not suite.check_schema_field_is_optional(_ctx(src))


def test_ellipsis_and_field_sentinels_are_required():
    ellipsis_src = (
        "from pydantic import BaseModel\n"
        "class Task(BaseModel):\n"
        "    id: int\n"
        "    priority: str = ...\n"
    )
    field_src = (
        "from pydantic import BaseModel, Field\n"
        "class Task(BaseModel):\n"
        "    id: int\n"
        '    priority: str = Field(description="x")\n'
    )
    assert not suite.check_schema_field_is_optional(_ctx(ellipsis_src))
    assert not suite.check_schema_field_is_optional(_ctx(field_src))


def test_inherited_default_counts():
    """A defaulted field on an in-file base class is backward-compatible."""
    src = (
        "from pydantic import BaseModel\n"
        "class BaseTask(BaseModel):\n"
        '    priority: str = "medium"\n'
        "class Task(BaseTask):\n"
        "    id: int\n"
    )
    assert suite.check_schema_field_is_optional(_ctx(src))


def test_inherited_requirement_fails():
    src = (
        "from pydantic import BaseModel\n"
        "class BaseTask(BaseModel):\n"
        "    priority: str\n"
        "class Task(BaseTask):\n"
        "    id: int\n"
    )
    assert not suite.check_schema_field_is_optional(_ctx(src))


def test_grandparent_default_counts():
    src = (
        "from pydantic import BaseModel\n"
        "class A(BaseModel):\n"
        '    priority: str = "low"\n'
        "class B(A):\n"
        "    pass\n"
        "class Task(B):\n"
        "    id: int\n"
    )
    assert suite.check_schema_field_is_optional(_ctx(src))


def test_unrelated_helper_default_does_not_count():
    """A `priority` on a class outside Task's hierarchy must not satisfy it."""
    src = (
        "from pydantic import BaseModel\n"
        "class Helper(BaseModel):\n"
        '    priority: str = "medium"\n'
        "class Task(BaseModel):\n"
        "    id: int\n"
    )
    assert not suite.check_schema_field_is_optional(_ctx(src))


def test_most_derived_override_wins():
    """A required override on Task must not be rescued by a defaulted base."""
    src = (
        "from pydantic import BaseModel\n"
        "class BaseTask(BaseModel):\n"
        '    priority: str = "medium"\n'
        "class Task(BaseTask):\n"
        "    id: int\n"
        "    priority: str\n"
    )
    assert not suite.check_schema_field_is_optional(_ctx(src))


def test_missing_task_or_bad_syntax_fails():
    other = "from pydantic import BaseModel\nclass Other(BaseModel):\n    id: int\n"
    assert not suite.check_schema_field_is_optional(_ctx(other))
    assert not suite.check_schema_field_is_optional(_ctx("def f(:"))
