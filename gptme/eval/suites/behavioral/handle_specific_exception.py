"""Behavioral scenario: handle-specific-exception."""

import ast
from typing import TYPE_CHECKING

from ._common import parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _get_config_source(ctx) -> str:
    content = ctx.files.get("config.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return content


def check_config_tests_pass(ctx):
    """Tests should pass after narrowing the exception handler."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_config_no_bare_except(ctx):
    """No bare 'except:' or broad 'except Exception:' should remain."""
    content = _get_config_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                return False
            exc_name: str | None = None
            if isinstance(node.type, ast.Name):
                exc_name = node.type.id
            elif isinstance(node.type, ast.Attribute):
                exc_name = node.type.attr
            if exc_name == "Exception":
                return False
    return True


def check_config_catches_json_error(ctx):
    """Handler must catch json.JSONDecodeError (or JSONDecodeError)."""
    content = _get_config_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            exc_type = node.type
            if (
                isinstance(exc_type, ast.Attribute)
                and exc_type.attr == "JSONDecodeError"
            ):
                return True
            if isinstance(exc_type, ast.Name) and exc_type.id == "JSONDecodeError":
                return True
            if isinstance(exc_type, ast.Tuple):
                for elt in exc_type.elts:
                    if isinstance(elt, ast.Attribute) and elt.attr == "JSONDecodeError":
                        return True
                    if isinstance(elt, ast.Name) and elt.id == "JSONDecodeError":
                        return True
    return False


def check_config_propagates_file_error(ctx):
    """Test suite must include a test that expects FileNotFoundError to propagate."""
    content = ctx.files.get("test_config.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "FileNotFoundError" in content and "raises" in content


test: "EvalSpec" = {
    "name": "handle-specific-exception",
    "files": {
        "config.py": (
            '"""Application configuration loader."""\n'
            "\n"
            "import json\n"
            "\n"
            "\n"
            "def parse_config(path):\n"
            '    """Load configuration from a JSON file.\n'
            "\n"
            "    Currently returns an empty dict for *any* error, including a missing file.\n"
            '    """\n'
            "    try:\n"
            "        with open(path) as f:\n"
            "            return json.load(f)\n"
            "    except Exception:\n"
            "        pass\n"
            "    return {}\n"
        ),
        "test_config.py": (
            "import json\n"
            "import os\n"
            "import tempfile\n"
            "\n"
            "from config import parse_config\n"
            "\n"
            "\n"
            "def test_valid_config():\n"
            '    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:\n'
            '        json.dump({"debug": True, "port": 8080}, f)\n'
            "        tmp_path = f.name\n"
            "    try:\n"
            "        result = parse_config(tmp_path)\n"
            '        assert result == {"debug": True, "port": 8080}\n'
            "    finally:\n"
            "        os.unlink(tmp_path)\n"
            "\n"
            "\n"
            "def test_empty_object_config():\n"
            '    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:\n'
            "        json.dump({}, f)\n"
            "        tmp_path = f.name\n"
            "    try:\n"
            "        result = parse_config(tmp_path)\n"
            "        assert result == {}\n"
            "    finally:\n"
            "        os.unlink(tmp_path)\n"
        ),
    },
    "run": "python3 -m pytest test_config.py -v --tb=short 2>&1",
    "prompt": (
        "In `config.py`, the `parse_config` function uses `except Exception:` which "
        "swallows all errors — including `FileNotFoundError` when the file doesn't "
        "exist. Narrow the exception handler to catch only `json.JSONDecodeError` "
        "(raised when the file exists but contains invalid JSON) and let other "
        "exceptions like `FileNotFoundError` propagate naturally. "
        "Update `test_config.py` to add a test that verifies `FileNotFoundError` "
        "is raised when the file does not exist. All existing tests must still pass."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_config_tests_pass,
        "no broad except": check_config_no_bare_except,
        "catches json.JSONDecodeError": check_config_catches_json_error,
        "FileNotFoundError propagates": check_config_propagates_file_error,
    },
}
