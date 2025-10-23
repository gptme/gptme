"""Tests for output schema system."""

from pathlib import Path
from datetime import datetime
import tempfile

from gptme.util.output_storage import (
    FullResult,
    ShellResultSchema,
    PythonResultSchema,
    BrowserResultSchema,
    FileResultSchema,
    register_schema,
    get_schema,
    save_structured_result,
)


def test_shell_schema_success():
    """Test shell schema with successful command."""
    result = FullResult(
        tool="shell",
        timestamp=datetime.now().isoformat(),
        command="echo hello",
        output="hello\n",
        meta={"exit_code": 0, "duration_ms": 10},
    )

    summary = ShellResultSchema.summarize(result)

    assert summary["tool"] == "shell"
    assert summary["command"] == "echo hello"
    assert summary["exit_code"] == 0
    assert summary["status"] == "success"
    assert summary["lines"] == 2  # "hello\n" splits to ["hello", ""]
    assert summary["duration_ms"] == 10


def test_shell_schema_failure():
    """Test shell schema with failed command."""
    result = FullResult(
        tool="shell",
        timestamp=datetime.now().isoformat(),
        command="false",
        output="",
        meta={"exit_code": 1, "duration_ms": 5},
    )

    summary = ShellResultSchema.summarize(result)

    assert summary["status"] == "failed"
    assert summary["exit_code"] == 1


def test_shell_schema_preview():
    """Test shell schema preview generation."""
    # Short output (≤3 lines)
    result = FullResult(
        tool="shell",
        timestamp=datetime.now().isoformat(),
        command="ls",
        output="file1\nfile2\nfile3",
        meta={"exit_code": 0},
    )

    summary = ShellResultSchema.summarize(result)
    assert summary["preview"] == "file1\nfile2\nfile3"

    # Long output (>3 lines)
    result = FullResult(
        tool="shell",
        timestamp=datetime.now().isoformat(),
        command="ls",
        output="file1\nfile2\nfile3\nfile4\nfile5",
        meta={"exit_code": 0},
    )

    summary = ShellResultSchema.summarize(result)
    assert "..." in summary["preview"]
    assert summary["preview"].startswith("file1\nfile2")
    assert summary["preview"].endswith("file5")


def test_python_schema_success():
    """Test python schema with successful execution."""
    result = FullResult(
        tool="python",
        timestamp=datetime.now().isoformat(),
        command=None,
        output="42",
        meta={"result": 42, "duration_ms": 15},
    )

    summary = PythonResultSchema.summarize(result)

    assert summary["tool"] == "python"
    assert summary["status"] == "success"
    assert summary["result"] == "42"
    assert summary["has_exception"] is False


def test_python_schema_exception():
    """Test python schema with exception."""
    result = FullResult(
        tool="python",
        timestamp=datetime.now().isoformat(),
        command=None,
        output="Traceback (most recent call last):\n  File ...\nNameError: name 'x' is not defined",
        meta={},
    )

    summary = PythonResultSchema.summarize(result)

    assert summary["status"] == "error"
    assert summary["has_exception"] is True


def test_browser_schema():
    """Test browser schema."""
    result = FullResult(
        tool="browser",
        timestamp=datetime.now().isoformat(),
        command=None,
        output="<html>...</html>",
        meta={
            "operation": "read_url",
            "url": "https://example.com",
            "status_code": 200,
            "duration_ms": 500,
        },
    )

    summary = BrowserResultSchema.summarize(result)

    assert summary["tool"] == "browser"
    assert summary["operation"] == "read_url"
    assert summary["url"] == "https://example.com"
    assert summary["status_code"] == 200
    assert summary["content_size"] == len("<html>...</html>")


def test_file_schema():
    """Test file schema."""
    result = FullResult(
        tool="file",
        timestamp=datetime.now().isoformat(),
        command=None,
        output="file contents...",
        meta={
            "operation": "read",
            "path": "/path/to/file.txt",
            "status": "completed",
        },
    )

    summary = FileResultSchema.summarize(result)

    assert summary["tool"] == "file"
    assert summary["operation"] == "read"
    assert summary["path"] == "/path/to/file.txt"
    assert summary["status"] == "completed"


def test_schema_registry():
    """Test schema registration and retrieval."""
    # Built-in schemas
    assert get_schema("shell") == ShellResultSchema
    assert get_schema("python") == PythonResultSchema
    assert get_schema("ipython") == PythonResultSchema
    assert get_schema("browser") == BrowserResultSchema

    # Non-existent schema
    assert get_schema("nonexistent") is None

    # Custom registration
    class CustomSchema(ShellResultSchema):
        pass

    register_schema("custom", CustomSchema)
    assert get_schema("custom") == CustomSchema


def test_save_structured_result_with_schema():
    """Test save_structured_result uses schema for summarization."""
    with tempfile.TemporaryDirectory() as tmpdir:
        logdir = Path(tmpdir)

        # Test with shell tool (has schema)
        compact, full = save_structured_result(
            tool="shell",
            command="echo test",
            output="test\n",
            meta={"exit_code": 0, "duration_ms": 5},
            logdir=logdir,
        )

        # Verify schema was used (summary has schema-specific fields)
        assert "exit_code" in compact.summary
        assert "status" in compact.summary
        assert compact.summary["status"] == "success"

        # Test with unknown tool (fallback to basic summary)
        compact, full = save_structured_result(
            tool="unknown",
            command="test",
            output="output",
            meta={"status": "done"},
            logdir=logdir,
        )

        # Verify fallback was used
        assert compact.summary["tool"] == "unknown"
        assert compact.summary["status"] == "done"
        assert "lines" in compact.summary
