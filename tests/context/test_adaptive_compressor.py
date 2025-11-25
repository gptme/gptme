"""Tests for adaptive context compressor."""

from pathlib import Path

import pytest

from gptme.context import AdaptiveCompressor, CompressionResult


def test_adaptive_compressor_init():
    """Test AdaptiveCompressor initialization."""
    compressor = AdaptiveCompressor()
    assert compressor.workspace_root == Path.cwd()
    assert compressor.enable_logging is True


def test_adaptive_compressor_init_with_workspace():
    """Test AdaptiveCompressor initialization with custom workspace."""
    workspace = Path("/tmp/test-workspace")
    compressor = AdaptiveCompressor(workspace_root=workspace)
    assert compressor.workspace_root == workspace


def test_compress_simple_fix():
    """Test compression for simple fix task."""
    compressor = AdaptiveCompressor(enable_logging=False)

    result = compressor.compress(
        prompt="Fix the counter increment bug in utils.py",
        context_files=["File content for utils.py", "Test content"],
    )

    assert isinstance(result, CompressionResult)
    assert result.task_classification.primary_type in ["fix", "diagnostic"]
    assert 0.1 <= result.compression_ratio <= 0.5
    assert len(result.compressed_content) < len(result.original_content)


def test_compress_architecture_task():
    """Test compression for architecture/implementation task."""
    compressor = AdaptiveCompressor(enable_logging=False)

    result = compressor.compress(
        prompt="Implement a new service package with REST API, database models, and tests",
        context_files=["Large architectural context"] * 10,
    )

    assert isinstance(result, CompressionResult)
    # Architecture tasks should get more conservative compression
    assert result.compression_ratio >= 0.25
    # Refactor is also an architecture-related task type
    assert result.task_classification.primary_type in [
        "implementation",
        "exploration",
        "refactor",
    ]


def test_compression_result_tokens_saved():
    """Test tokens_saved property calculation."""
    result = CompressionResult(
        original_content="a" * 1000,  # 1000 chars
        compressed_content="a" * 500,  # 500 chars
        compression_ratio=0.5,
        task_classification=None,  # type: ignore
        rationale="Test",
    )

    # Estimate: (1000 - 500) / 4 = 125 tokens
    assert result.tokens_saved == 125


def test_compression_rationale_generation():
    """Test that rationale is generated with useful information."""
    compressor = AdaptiveCompressor(enable_logging=False)

    result = compressor.compress(
        prompt="Debug the failing test in test_utils.py",
        context_files=["Test context"],
    )

    assert "Task Type:" in result.rationale
    assert "Confidence:" in result.rationale
    assert "Compression Ratio:" in result.rationale


def test_compress_handles_empty_context():
    """Test compression with no context files."""
    compressor = AdaptiveCompressor(enable_logging=False)

    result = compressor.compress(
        prompt="Simple task",
        context_files=None,
    )

    assert isinstance(result, CompressionResult)
    assert result.original_content == ""
    assert result.compressed_content == ""


def test_compress_diagnostic_vs_implementation():
    """Test that diagnostic tasks get more aggressive compression than implementation."""
    compressor = AdaptiveCompressor(enable_logging=False)

    # Diagnostic task
    diagnostic = compressor.compress(
        prompt="Check why the CI is failing",
        context_files=["CI logs"],
    )

    # Implementation task
    implementation = compressor.compress(
        prompt="Implement OAuth authentication with JWT tokens and refresh logic",
        context_files=["Large codebase context"] * 20,
    )

    # Diagnostic should have more aggressive compression (lower ratio = keep less)
    assert diagnostic.compression_ratio < implementation.compression_ratio


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
