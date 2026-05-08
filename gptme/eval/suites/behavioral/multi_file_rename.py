"""Behavioral scenario: multi-file-rename."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_rename_no_old_name(ctx):
    """calcArea should not appear in any .py file after the rename."""
    for name, content in ctx.files.items():
        if not name.endswith(".py"):
            continue
        text = content if isinstance(content, str) else content.decode()
        if "calcArea" in text:
            return False
    return True


def check_rename_new_name_in_geometry(ctx):
    """calculate_area should be defined in src/geometry.py."""
    content = ctx.files.get("src/geometry.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "def calculate_area" in content


def check_rename_tests_pass(ctx):
    """Tests should pass after the rename."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_rename_test_uses_new_name(ctx):
    """tests/test_geometry.py should call calculate_area, not calcArea."""
    content = ctx.files.get("tests/test_geometry.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "calculate_area" in content and "calcArea" not in content


test: "EvalSpec" = {
    "name": "multi-file-rename",
    "task_type": "structured_process",
    "files": {
        "src/__init__.py": "",
        "src/geometry.py": """\
\"\"\"Geometry utilities.\"\"\"
import math


def calcArea(shape, *args):
    \"\"\"Calculate area of a shape.

    Supports: circle (r), rectangle (w, h), triangle (b, h).
    \"\"\"
    if shape == "circle":
        return math.pi * args[0] ** 2
    elif shape == "rectangle":
        return args[0] * args[1]
    elif shape == "triangle":
        return 0.5 * args[0] * args[1]
    else:
        raise ValueError(f"Unknown shape: {shape}")
""",
        "src/utils.py": """\
\"\"\"Utility wrappers around geometry functions.\"\"\"
from src.geometry import calcArea


def room_area(width, height):
    \"\"\"Return floor area of a rectangular room.\"\"\"
    return calcArea("rectangle", width, height)


def circular_pool_area(radius):
    \"\"\"Return surface area of a circular pool.\"\"\"
    return calcArea("circle", radius)
""",
        "tests/__init__.py": "",
        "tests/test_geometry.py": """\
import math
import pytest
from src.geometry import calcArea


def test_rectangle():
    assert calcArea("rectangle", 4, 5) == 20


def test_circle():
    assert abs(calcArea("circle", 3) - math.pi * 9) < 1e-9


def test_triangle():
    assert calcArea("triangle", 6, 4) == 12.0


def test_unknown_shape():
    with pytest.raises(ValueError):
        calcArea("hexagon", 5)
""",
    },
    "run": "python3 -m pytest tests/ -q 2>&1",
    "prompt": (
        "The function `calcArea` in src/geometry.py should be renamed to "
        "`calculate_area` to follow Python naming conventions (PEP 8 snake_case). "
        "Update the function definition in src/geometry.py AND every place it is "
        "imported or called (src/utils.py, tests/test_geometry.py). "
        "Make sure all tests still pass after the rename."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "old name gone from all files": check_rename_no_old_name,
        "new name defined in geometry.py": check_rename_new_name_in_geometry,
        "test file uses new name": check_rename_test_uses_new_name,
        "tests pass": check_rename_tests_pass,
    },
}
