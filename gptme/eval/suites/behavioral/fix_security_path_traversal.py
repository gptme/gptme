"""Behavioral scenario: fix-security-path-traversal."""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_security_tests_pass(ctx):
    """All tests should pass after the security fix."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_security_uses_realpath(ctx):
    """server.py should resolve the path to prevent traversal via symlinks."""
    content = ctx.files.get("server.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "os.path.realpath" in content


def check_security_blocks_traversal(ctx):
    """Resolved path must still start with the base directory."""
    content = ctx.files.get("server.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return bool(
        re.search(r"\\.startswith\s*\(\s*(?:base_dir|root|safe_dir|allowed)", content)
        or "not " in content
        and ".startswith(" in content
    )


def check_security_no_direct_join(ctx):
    """The unsafe os.path.join(base, filename) without validation should be gone."""
    content = ctx.files.get("server.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    # The original unsafe pattern was: return open(os.path.join(base_dir, filename))
    # After fix, the raw join should be followed by a validation check
    return "os.path.realpath" in content


def check_security_has_traversal_test(ctx):
    """test_server.py should contain a test for path traversal (../..)."""
    content = ctx.files.get("test_server.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return bool(re.search(r"\.\./|\.\.%2[fF]", content))


test: "EvalSpec" = {
    "name": "fix-security-path-traversal",
    "task_type": "structured_process",
    "files": {
        "server.py": """\
\"\"\"Minimal static file server.\"\"\"

import os


def serve_file(base_dir: str, filename: str) -> bytes:
    \"\"\"Return the contents of *filename* from *base_dir*.

    Raises FileNotFoundError if the file does not exist.
    Raises ValueError if *filename* is absolute.
    \"\"\"
    if os.path.isabs(filename):
        raise ValueError(f"Absolute paths not allowed: {filename!r}")

    filepath = os.path.join(base_dir, filename)
    with open(filepath, "rb") as f:
        return f.read()
""",
        "test_server.py": """\
import os
import pytest
from server import serve_file


def test_serves_existing_file(tmp_path):
    (tmp_path / "hello.txt").write_bytes(b"hello world")
    assert serve_file(str(tmp_path), "hello.txt") == b"hello world"


def test_rejects_absolute_path(tmp_path):
    with pytest.raises(ValueError):
        serve_file(str(tmp_path), "/etc/passwd")
""",
    },
    "run": "python3 -m pytest test_server.py -v --tb=short 2>&1",
    "prompt": (
        "The `serve_file` function in `server.py` has a path traversal "
        "vulnerability: passing a filename like `../../../etc/passwd` will "
        "escape the intended base directory because `os.path.join` does not "
        "prevent `..` components.  Fix the vulnerability by resolving the "
        "final path with `os.path.realpath` and verifying that it still "
        "starts with the resolved base directory.  Then add a test in "
        "`test_server.py` that verifies path traversal is blocked (e.g. "
        "requesting `../../etc/passwd` raises ValueError).  All existing "
        "tests must still pass."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_security_tests_pass,
        "uses os.path.realpath": check_security_uses_realpath,
        "blocks traversal": check_security_blocks_traversal,
        "no unsafe direct join": check_security_no_direct_join,
        "has traversal test": check_security_has_traversal_test,
    },
}
