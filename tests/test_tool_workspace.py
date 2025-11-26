"""Tests for the workspace tool."""

import tempfile
from pathlib import Path

from gptme.message import Message
from gptme.tools.workspace import execute_workspace


def test_workspace_basic():
    """Test basic workspace output."""
    # Create a temporary directory to test with
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        # Create some test files
        (tmppath / "README.md").write_text("Test readme")
        (tmppath / "ARCHITECTURE.md").write_text("Test architecture")

        # Create some test directories
        (tmppath / "tasks").mkdir()
        (tmppath / "journal").mkdir()
        (tmppath / "tasks" / "test.md").write_text("Test task")

        # Change to temp directory and execute
        import os

        old_cwd = os.getcwd()
        try:
            os.chdir(tmppath)
            result = execute_workspace("", [], {}, lambda x: True)

            # Check result
            assert isinstance(result, Message)
            assert result.role == "system"
            assert str(tmppath) in result.content
            assert "README.md" in result.content
            assert "ARCHITECTURE.md" in result.content
            assert "tasks/" in result.content
            assert "journal/" in result.content
            assert "1 items" in result.content  # tasks has 1 file
            assert "0 items" in result.content  # journal is empty
        finally:
            os.chdir(old_cwd)


def test_workspace_minimal():
    """Test workspace with no special files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        import os

        old_cwd = os.getcwd()
        try:
            os.chdir(tmppath)
            result = execute_workspace("", [], {}, lambda x: True)

            # Should still work with minimal content
            assert isinstance(result, Message)
            assert result.role == "system"
            assert str(tmppath) in result.content
            assert "Tip:" in result.content
        finally:
            os.chdir(old_cwd)
