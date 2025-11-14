"""Integration tests for adaptive compression configuration."""

from gptme.context_compression.adaptive import AdaptiveCompressor
from gptme.context_compression.config import CompressionConfig


def test_manual_override_ratio():
    """Test that manual_override_ratio skips analysis."""
    config = CompressionConfig(
        enabled=True,
        mode="adaptive",
        manual_override_ratio=0.35,  # Force specific ratio
    )

    compressor = AdaptiveCompressor(config)

    content = """
    # Test Content

    This is a simple focused task that would normally get aggressive compression (0.10-0.20).
    But with manual override, it should use 0.35 regardless.

    Fix counter increment bug.
    """

    # Should use manual override (0.35) not analyzed ratio (~0.15 for focused task)
    result = compressor.compress(
        content=content,
        context="Fix counter bug (focused task)",
    )

    # Manual override was 0.35, so compressed should be ~35% of original
    assert result.compressed is not None
    assert result.compression_ratio == 0.35  # Exact match for override


def test_custom_thresholds():
    """Test custom complexity thresholds."""
    config = CompressionConfig(
        enabled=True,
        mode="adaptive",
        complexity_thresholds={
            "focused": 0.2,  # Lower threshold (more tasks classified as focused)
            "architecture": 0.8,  # Higher threshold (fewer tasks classified as architecture)
        },
    )

    compressor = AdaptiveCompressor(config)

    # Task with complexity ~0.5 would be "mixed" with default thresholds
    # but might be different with custom thresholds
    content = """
    # Implementation Task

    Implement new feature with moderate complexity.
    Requires updating 2-3 files and writing tests.
    """

    result = compressor.compress(
        content=content,
        context="Implement feature with 3 files",
    )

    assert result.compressed is not None
    # Verify compression happened
    assert len(result.compressed) < len(content)


def test_custom_ratio_ranges():
    """Test custom compression ratio ranges."""
    config = CompressionConfig(
        enabled=True,
        mode="adaptive",
        ratio_ranges={
            "focused": (0.15, 0.25),  # Less aggressive than default (0.10-0.20)
            "mixed": (0.25, 0.35),  # Less aggressive than default (0.20-0.30)
            "architecture": (0.35, 0.55),  # Less aggressive than default (0.30-0.50)
        },
    )

    compressor = AdaptiveCompressor(config)

    content = """
    # Simple Bug Fix

    Fix counter increment in process function.
    Change counter += 2 to counter += 1.
    """

    result = compressor.compress(
        content=content,
        context="Fix counter bug (simple focused task)",
    )

    assert result.compressed is not None
    # With custom ranges, focused task should use 0.15-0.25 instead of 0.10-0.20
    # This means less aggressive compression (keep more content)
    assert result.compression_ratio >= 0.15
    assert result.compression_ratio <= 0.25


def test_config_integration():
    """Test complete configuration integration."""
    config = CompressionConfig(
        enabled=True,
        mode="adaptive",
        log_analysis=True,
        complexity_thresholds={
            "focused": 0.3,
            "architecture": 0.7,
        },
        ratio_ranges={
            "focused": (0.10, 0.20),
            "mixed": (0.20, 0.30),
            "architecture": (0.30, 0.50),
        },
    )

    compressor = AdaptiveCompressor(config)

    # Test that config is properly passed through
    assert compressor.config.complexity_thresholds["focused"] == 0.3
    assert compressor.config.complexity_thresholds["architecture"] == 0.7
    assert compressor.config.ratio_ranges["focused"] == (0.10, 0.20)
    assert compressor.config.ratio_ranges["mixed"] == (0.20, 0.30)
    assert compressor.config.ratio_ranges["architecture"] == (0.30, 0.50)

    # Test that analyzer received the config
    assert compressor.analyzer.thresholds == config.complexity_thresholds
    assert compressor.analyzer.ratio_ranges == config.ratio_ranges


def test_gptme_toml_config_loading():
    """Test that config can be loaded from dict (gptme.toml format)."""
    config_dict = {
        "enabled": True,
        "mode": "adaptive",
        "log_analysis": True,
        "manual_override_ratio": 0.25,
        "complexity_thresholds": {
            "focused": 0.25,
            "architecture": 0.75,
        },
        "ratio_ranges": {
            "focused": (0.12, 0.22),
            "mixed": (0.22, 0.32),
            "architecture": (0.32, 0.52),
        },
    }

    config = CompressionConfig.from_dict(config_dict)

    assert config.enabled is True
    assert config.mode == "adaptive"
    assert config.log_analysis is True
    assert config.manual_override_ratio == 0.25
    assert config.complexity_thresholds["focused"] == 0.25
    assert config.complexity_thresholds["architecture"] == 0.75
    assert config.ratio_ranges["focused"] == (0.12, 0.22)
