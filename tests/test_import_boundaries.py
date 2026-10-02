"""Guard heavy imports from leaking into modules loaded on every startup."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("module", "heavy"),
    [
        ("gptme.message", "prompt_toolkit"),
        ("gptme.commands", "requests"),
    ],
)
def test_module_does_not_import(module: str, heavy: str):
    code = f"import sys, {module}; assert {heavy!r} not in sys.modules"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
