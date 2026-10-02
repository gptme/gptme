"""Guard heavy imports from leaking into modules loaded on every startup."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

# repo root — ensures the subprocess imports the local checkout, not a stale
# installed copy, regardless of how pytest was invoked.
_REPO_ROOT = str(Path(__file__).parent.parent)


@pytest.mark.parametrize(
    ("module", "heavy"),
    [
        ("gptme.message", "prompt_toolkit"),
        ("gptme.commands", "requests"),
    ],
)
def test_module_does_not_import(module: str, heavy: str):
    code = f"import sys, {module}; assert {heavy!r} not in sys.modules"
    env = {**os.environ, "PYTHONPATH": _REPO_ROOT}
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
