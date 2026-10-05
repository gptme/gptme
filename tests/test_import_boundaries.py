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
        ("gptme.chat", "prompt_toolkit"),
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


def test_noninteractive_cli_does_not_import_prompt_toolkit(tmp_path: Path):
    code = """
import sys
from click.testing import CliRunner
from gptme.cli.main import main

result = CliRunner().invoke(
    main, ["--non-interactive", "--model", "openai/gpt-4o", "/exit"]
)
assert result.exit_code == 0, (result.output, result.exception)
assert "prompt_toolkit" not in sys.modules
"""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": _REPO_ROOT,
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "GPTME_LOGS_HOME": str(tmp_path / "logs"),
        "OPENAI_API_KEY": "test-unused",
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
