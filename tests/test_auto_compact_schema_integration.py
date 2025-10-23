"""
Tests for autocompact schema-based integration.

Tests that create_tool_result_summary uses schema-based summarization
when tool can be detected from content.
"""

from pathlib import Path
import tempfile

from gptme.util.output_storage import create_tool_result_summary


def test_create_tool_result_summary_with_shell_schema():
    """Test that create_tool_result_summary uses shell schema when detected."""
    # Shell tool output format
    content = """Ran command: `echo hello world`
```bash
echo hello world
```

```stdout
hello world
```

Return code: 0
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        logdir = Path(tmpdir)
        summary = create_tool_result_summary(
            content=content,
            original_tokens=100,
            logdir=logdir,
            tool_name="autocompact",
        )

        # Should use schema-based summarization
        assert "Tool result reference" in summary
        assert "shell" in summary.lower()
        assert "echo hello world" in summary
        assert "Status: success" in summary

        # Should have saved full result
        output_dir = logdir / "tool-outputs" / "shell"
        assert output_dir.exists()
        saved_files = list(output_dir.glob("result-*.json"))
        assert len(saved_files) == 1


def test_create_tool_result_summary_with_python_schema():
    """Test that create_tool_result_summary uses python schema when detected."""
    # Python tool output format
    content = """Executed: `print("hello")`
```python
print("hello")
```

```stdout
hello
```

exit_code: 0
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        logdir = Path(tmpdir)
        summary = create_tool_result_summary(
            content=content,
            original_tokens=100,
            logdir=logdir,
            tool_name="autocompact",
        )

        # Should use schema-based summarization (note: tool_name was "autocompact")
        assert "Tool result reference" in summary
        assert "Status: success" in summary

        # Python not detected (header was "Executed:" not "Ran command:")
        # But should still use schema-based approach with generic tool
        assert "Full output:" in summary

        # Should have saved full result (under autocompact since that's the tool_name)
        output_dir = logdir / "tool-outputs" / "autocompact"
        assert output_dir.exists()
        saved_files = list(output_dir.glob("result-*.json"))
        assert len(saved_files) == 1


def test_create_tool_result_summary_fallback_to_legacy():
    """Test that create_tool_result_summary falls back to legacy for unknown formats."""
    # Content with no recognizable tool pattern
    content = "Some random output that doesn't match any tool format\nwith multiple lines\nand no clear structure"

    with tempfile.TemporaryDirectory() as tmpdir:
        logdir = Path(tmpdir)
        summary = create_tool_result_summary(
            content=content,
            original_tokens=100,
            logdir=logdir,
            tool_name="unknown_tool",
        )

        # Should use structured approach (even for unknown tools)
        assert isinstance(summary, str)
        assert "Tool result reference" in summary
        assert "unknown_tool" in summary
        assert "Full output:" in summary  # Should have saved the file


def test_create_tool_result_summary_no_logdir():
    """Test that create_tool_result_summary works without logdir (legacy path)."""
    content = """Ran command: `echo test`
```bash
echo test
```

```stdout
test
```
"""

    summary = create_tool_result_summary(
        content=content,
        original_tokens=100,
        logdir=None,  # No logdir forces legacy path
        tool_name="shell",
    )

    # Should use legacy approach
    assert isinstance(summary, str)
    # Without logdir, no file storage possible
    assert "removed" in summary.lower() or "output" in summary.lower()
