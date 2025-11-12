"""Simplified context compression tests (MVP)"""

import pytest

from gptme.context_compression import CompressionConfig, ExtractiveSummarizer


def test_basic_compression():
    """Test basic compression functionality"""
    text = """
    This is a long piece of text that should be compressed.
    It contains multiple sentences and paragraphs.

    The compression should preserve the most important information
    while reducing the overall token count.

    Code blocks should be preserved:
    ```python
    def example():
        return "preserved"
    ```

    As well as important headings and structure.
    """

    config = CompressionConfig()
    compressor = ExtractiveSummarizer(config)
    result = compressor.compress(text, target_ratio=0.5)

    # Verify compression happened
    assert len(result.compressed) < len(text)
    assert result.compression_ratio < 1.0

    # Verify code blocks preserved
    assert "```python" in result.compressed
    assert "preserved" in result.compressed


def test_compression_with_context():
    """Test compression with context/query"""
    text = """
    Machine learning is a subset of artificial intelligence.
    Deep learning uses neural networks with many layers.
    Python is a popular programming language.
    JavaScript is used for web development.
    """

    config = CompressionConfig()
    compressor = ExtractiveSummarizer(config)
    # Compress with ML context - should keep ML sentences
    result = compressor.compress(text, target_ratio=0.5, context="machine learning")
    assert (
        "Machine learning" in result.compressed or "Deep learning" in result.compressed
    )

    # Compressed size should be less than original
    assert len(result.compressed) < len(text)


def test_config_options():
    """Test configuration options"""
    # Default config
    config1 = CompressionConfig()
    assert config1.target_ratio == 0.7
    assert config1.preserve_code is True

    # Custom config
    config2 = CompressionConfig(target_ratio=0.3, preserve_code=False)
    assert config2.target_ratio == 0.3
    assert config2.preserve_code is False


def test_small_text_unchanged():
    """Test that very small text is not over-compressed"""
    text = "This is a short sentence."

    config = CompressionConfig()
    compressor = ExtractiveSummarizer(config)
    result = compressor.compress(text, target_ratio=0.5)

    # Small text should be minimally compressed or unchanged
    assert len(result.compressed) > 0
    assert "short" in result.compressed or "sentence" in result.compressed


def test_empty_input():
    """Test handling of empty input"""
    config = CompressionConfig()
    compressor = ExtractiveSummarizer(config)
    result = compressor.compress("", target_ratio=0.7)

    assert result.compressed == ""
    assert result.compression_ratio == 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
