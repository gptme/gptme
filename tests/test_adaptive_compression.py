"""Integration tests for adaptive compression."""

import pytest

from gptme.context_compression.adaptive import AdaptiveCompressor, WorkspaceContext
from gptme.context_compression.analyzer.task_analyzer import TaskAnalyzer
from gptme.context_compression.config import CompressionConfig
from gptme.context_compression.extractive import ExtractiveSummarizer


@pytest.fixture
def config():
    """Create test compression config."""
    return CompressionConfig(
        enabled=True,
        mode="adaptive",
        target_ratio=0.7,
        log_analysis=False,  # Disable logging in tests
    )


@pytest.fixture
def adaptive_compressor(config):
    """Create adaptive compressor with test configuration."""
    return AdaptiveCompressor(config=config)


def test_adaptive_compressor_initialization(config):
    """Test adaptive compressor initializes correctly."""
    compressor = AdaptiveCompressor(config=config)

    assert compressor.config == config
    assert isinstance(compressor.analyzer, TaskAnalyzer)
    assert isinstance(compressor.base, ExtractiveSummarizer)
    assert compressor.log_analysis is False


def test_workspace_context_from_cwd(tmp_path):
    """Test workspace context creation from current directory."""
    # Create test workspace structure
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()

    # Create some files
    (tmp_path / "src" / "module.py").write_text("# Test module")
    (tmp_path / "tests" / "test_module.py").write_text("# Test file")
    (tmp_path / "docs" / "README.md").write_text("# Documentation")

    # Create workspace context
    ctx = WorkspaceContext.from_cwd(tmp_path)

    assert ctx.workspace_path == tmp_path
    assert len(ctx.active_files) > 0
    assert len(ctx.tests) > 0
    assert len(ctx.docs) > 0


def test_adaptive_compression_focused_task(adaptive_compressor):
    """Test compression with focused task (aggressive ratio)."""
    content = """
    # Test Module

    This is a simple bug fix task.

    ## Details
    Fix the counter increment bug in the process function.
    The counter should increment by 1, not 2.

    ## Code
    ```python
    def process():
        counter += 1  # Fixed from counter += 2
        return counter
    ```
    """

    context = "Fix counter increment bug in process function"

    result = adaptive_compressor.compress(
        content=content,
        context=context,
    )

    # Focused task should use aggressive compression (ratio ~0.10-0.20)
    assert result.compressed_length < result.original_length
    assert result.compression_ratio < 0.3  # Less than 30% kept (aggressive)


def test_adaptive_compression_architecture_task(adaptive_compressor):
    """Test compression with architecture task (conservative ratio)."""
    content = """
    # Implementation Task

    Implement complete orchestrator system with multiple components.

    ## Requirements
    1. Create InputSource interface
    2. Implement GitHubSource class
    3. Implement EmailSource class
    4. Create Orchestrator class to coordinate sources
    5. Add configuration system

    ## Design
    The system should poll multiple input sources and process them.
    Each source should be independent and pluggable.

    ## Files to Create
    - src/orchestrator.py (200 lines)
    - src/sources/base.py (50 lines)
    - src/sources/github.py (100 lines)
    - src/sources/email.py (100 lines)
    - tests/test_orchestrator.py (150 lines)
    """

    context = "Implement complete input orchestrator system with 5 new files"

    result = adaptive_compressor.compress(
        content=content,
        context=context,
    )

    # Architecture task should use conservative compression (ratio ~0.30-0.50)
    assert result.compressed_length < result.original_length
    assert result.compression_ratio > 0.2  # More than 20% kept (conservative)


def test_adaptive_compression_preserves_structure(adaptive_compressor):
    """Test that compression preserves code blocks and headings."""
    content = """
    # Implementation Guide

    This is a detailed implementation guide with code examples.

    ## Step 1: Setup
    First, create the configuration:

    ```python
    config = CompressionConfig(
        enabled=True,
        mode="adaptive"
    )
    ```

    ## Step 2: Initialize
    Then initialize the compressor:

    ```python
    compressor = AdaptiveCompressor(config)
    ```

    ## Step 3: Use
    Finally, use the compressor:

    ```python
    result = compressor.compress(content, context)
    ```
    """

    context = "Implementation guide for adaptive compression"

    result = adaptive_compressor.compress(
        content=content,
        context=context,
    )

    # Code blocks should be preserved
    assert "```python" in result.compressed
    assert "CompressionConfig" in result.compressed or "compressor" in result.compressed

    # Headings should be preserved
    heading_count = content.count("##")
    compressed_heading_count = result.compressed.count("##")
    # Should preserve most headings (allow some loss in aggressive compression)
    assert compressed_heading_count >= heading_count // 2


def test_adaptive_compression_empty_content(adaptive_compressor):
    """Test compression with empty content."""
    result = adaptive_compressor.compress(
        content="",
        context="Empty task",
    )

    assert result.compressed == ""
    assert result.original_length == 0
    assert result.compressed_length == 0


def test_adaptive_compression_with_logging(config):
    """Test that logging can be enabled without errors."""
    compressor = AdaptiveCompressor(config=config, log_analysis=True)

    content = "Test content for compression"
    context = "Simple test task"

    # Should not raise any errors with logging enabled
    result = compressor.compress(content=content, context=context)

    assert result.compressed_length <= result.original_length


def test_workspace_context_handles_missing_directory():
    """Test workspace context handles non-existent directory gracefully."""
    from pathlib import Path

    ctx = WorkspaceContext.from_cwd(Path("/nonexistent/directory"))

    assert len(ctx.active_files) == 0
    assert len(ctx.reference_impls) == 0
    assert len(ctx.tests) == 0
    assert len(ctx.docs) == 0


def test_task_description_extraction(adaptive_compressor):
    """Test extraction of task description from context."""
    # Test with simple context
    desc = adaptive_compressor._extract_task_description("Fix bug in module")
    assert desc == "Fix bug in module"

    # Test with multi-line context
    context = """
    # Task Description

    Implement new feature

    Details:
    - Add function
    - Add tests
    """
    desc = adaptive_compressor._extract_task_description(context)
    assert "Implement new feature" in desc or "Task Description" not in desc

    # Test with empty context
    desc = adaptive_compressor._extract_task_description("")
    assert desc == ""
